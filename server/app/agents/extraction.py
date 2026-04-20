"""Extraction agent loop.

Every 60s:
  1. GET /api/mcp/campaigns?since=<ts>
  2. For each campaign returned, acquire a 10-min campaign_lease in Mongo.
  3. GET /api/mcp/campaigns/:id/manifest (presigned S3 URLs + steps + schedule).
  4. For each file in manifest: stream rows, upsert campaign_contacts +
     refined_contacts, enqueue send_queue per (contact, step).
  5. POST /api/mcp/file-ingested per file, release lease.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import httpx

from app.agents.supervisor import supervisor
from app.core.logging import get_logger, hash_email
from app.core.runtime_config import runtime_config
from app.db.mongodb import get_db
from app.services import (
    contact_parser,
    lease_service,
    leadpulse_client as lpc_mod,
    refined_contacts_service as rcs,
    send_queue_service as sqs,
)

log = get_logger(__name__)

_POLL_INTERVAL_SECONDS = 60
_MAX_ROW_ERROR_RATE = 0.10


async def run() -> None:
    cfg = runtime_config.get()
    container_id = cfg.instance_id
    since: str | None = None
    while True:
        if supervisor.quarantined():
            await asyncio.sleep(_POLL_INTERVAL_SECONDS)
            continue
        try:
            resp = await lpc_mod.leadpulse_client.get_active_campaigns(since=since)
            campaigns: list[dict[str, Any]] = (resp.get("data") or {}).get("campaigns", [])
            for campaign in campaigns:
                await _ingest_campaign(container_id, campaign)
            since = datetime.now(timezone.utc).isoformat()
        except Exception:  # noqa: BLE001
            log.exception("extraction_poll_failed")
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)


async def _ingest_campaign(container_id: str, summary: dict[str, Any]) -> None:
    campaign_id = summary.get("id") or summary.get("campaign_id")
    if not campaign_id:
        return
    db = get_db()
    acquired = await lease_service.acquire_campaign_lease(db, campaign_id, container_id)
    if not acquired:
        return  # another container holds the lease
    try:
        manifest_resp = await lpc_mod.leadpulse_client.get_campaign_manifest(campaign_id)
        manifest = manifest_resp.get("data") or manifest_resp
        campaign = manifest.get("campaign") or manifest
        files = manifest.get("files") or campaign.get("files") or []
        steps = manifest.get("steps") or campaign.get("sequence_snapshot") or []
        tenant_user_id = campaign.get("tenant_user_id") or campaign.get("user_id")
        for file_info in files:
            if file_info.get("ingestion_status") == "complete":
                continue
            await _ingest_file(
                campaign_id=campaign_id,
                tenant_user_id=tenant_user_id,
                campaign=campaign,
                steps=steps,
                file_info=file_info,
            )
    finally:
        await lease_service.release_campaign_lease(db, campaign_id, container_id)


async def _ingest_file(
    *,
    campaign_id: str,
    tenant_user_id: str,
    campaign: dict[str, Any],
    steps: list[dict[str, Any]],
    file_info: dict[str, Any],
) -> None:
    db = get_db()
    file_id = file_info.get("file_id") or file_info.get("id") or ""
    presigned_url = file_info.get("presigned_url") or file_info.get("s3_url")
    filename = file_info.get("original_filename") or file_info.get("filename") or "unknown"
    if not presigned_url:
        log.error("ingest_missing_presigned_url", extra={"extra_payload": {"file_id": file_id}})
        return

    try:
        content = await _download_bytes(presigned_url)
    except httpx.HTTPStatusError as exc:
        # 403 typically means presigned URL expired -> refresh once.
        if exc.response.status_code in (401, 403):
            fresh_resp = await lpc_mod.leadpulse_client.get_campaign_manifest(campaign_id)
            fresh_files = (fresh_resp.get("data") or {}).get("files") or []
            match = next((f for f in fresh_files if (f.get("file_id") or f.get("id")) == file_id), None)
            if match and (match.get("presigned_url") or match.get("s3_url")):
                content = await _download_bytes(match["presigned_url"] or match["s3_url"])
            else:
                await _report_file_failed(file_id, str(exc))
                return
        else:
            await _report_file_failed(file_id, str(exc))
            return

    rows: list[dict[str, Any]] = []
    parse_errors = 0
    try:
        for normalized in contact_parser.parse_stream(filename, content):
            rows.append(normalized)
    except ValueError as exc:
        await _report_file_failed(file_id, f"unparseable: {exc}")
        return

    if not rows:
        await lpc_mod.leadpulse_client.post_file_ingested(
            {"file_id": file_id, "row_count": 0, "error_count": 0, "ingestion_status": "complete"}
        )
        return

    # For full fidelity we'd count per-row errors during parse_stream. Here we
    # approximate: normalized rows that made it through are valid.
    stats = await rcs.bulk_upsert(
        db, campaign_id, tenant_user_id, rows, source_file_id=file_id
    )
    parse_errors += stats.get("errors", 0)
    error_rate = parse_errors / max(1, len(rows) + parse_errors)

    if error_rate > _MAX_ROW_ERROR_RATE:
        await _report_file_failed(
            file_id, f"row error rate {error_rate:.0%} > 10%"
        )
        return

    # Enqueue sends for each contact x step
    tracker_ids = {t.get("email"): t.get("tracker_id") for t in (file_info.get("trackers") or [])}
    start_date = _parse_start_date(campaign.get("start_date"))
    cfg = campaign.get("config") or campaign
    send_window_start = cfg.get("send_window_start", "09:00")
    tz_name = cfg.get("timezone", "UTC")

    inserted_sends = 0
    for row in rows:
        email = row["email"]
        contact_id = await _lookup_contact_id(db, campaign_id, email)
        for step in steps:
            scheduled = _compute_scheduled_for(start_date, step, send_window_start, tz_name)
            tracker_id = tracker_ids.get(email) or f"{campaign_id}:{email}:{step.get('step_index', 0)}"
            if await sqs.enqueue_send(
                db,
                campaign_id=campaign_id,
                tenant_user_id=tenant_user_id,
                contact_id=contact_id,
                email=email,
                step_index=int(step.get("step_index", 0)),
                tracker_id=tracker_id,
                scheduled_for=scheduled,
            ):
                inserted_sends += 1

    log.info(
        "file_ingested",
        extra={
            "extra_payload": {
                "file_id": file_id,
                "rows": len(rows),
                "sends_enqueued": inserted_sends,
                "error_rate": error_rate,
            },
            "campaign_id": campaign_id,
        },
    )
    await lpc_mod.leadpulse_client.post_file_ingested(
        {
            "file_id": file_id,
            "row_count": len(rows),
            "error_count": parse_errors,
            "ingestion_status": "complete",
        }
    )


async def _report_file_failed(file_id: str, message: str) -> None:
    db = get_db()
    await db.ingest_errors.insert_one(
        {"file_id": file_id, "message": message, "ts": datetime.now(timezone.utc)}
    )
    try:
        await lpc_mod.leadpulse_client.post_file_ingested(
            {"file_id": file_id, "row_count": 0, "error_count": 1, "ingestion_status": "failed", "error": message}
        )
    except Exception:  # noqa: BLE001
        log.exception("report_file_failed_unreachable")


async def _download_bytes(url: str) -> bytes:
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.content


async def _lookup_contact_id(db: Any, campaign_id: str, email: str) -> str | None:
    doc = await db.campaign_contacts.find_one(
        {"campaign_id": campaign_id, "email": email.lower().strip()}, {"_id": 1}
    )
    return str(doc["_id"]) if doc else None


def _parse_start_date(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        s = value.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(s)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _compute_scheduled_for(
    start_date: datetime,
    step: dict[str, Any],
    send_window_start: str,
    tz_name: str = "UTC",
) -> datetime:
    from datetime import time as _time, timedelta
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    offset_days = int(step.get("send_offset_days", 0))
    offset_hours = int(step.get("send_offset_hours", 0))
    hh, mm = (int(x) for x in send_window_start.split(":"))
    try:
        tz = ZoneInfo(tz_name or "UTC")
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("UTC")
    # Take the calendar date from start_date as-is (the CRM stores it as
    # the user's chosen start day, typically serialized as UTC midnight),
    # combine with HH:MM in the campaign's tz, then convert to UTC. This
    # avoids a negative-offset tz (e.g. America/Los_Angeles) rolling the
    # calendar day backwards when start_date is UTC midnight.
    local = datetime.combine(start_date.date(), _time(hh, mm), tzinfo=tz)
    local += timedelta(days=offset_days, hours=offset_hours)
    return local.astimezone(timezone.utc)
