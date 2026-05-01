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
            poll_started_at = datetime.now(timezone.utc).isoformat()
            resp = await lpc_mod.leadpulse_client.get_active_campaigns(since=since)
            campaigns: list[dict[str, Any]] = (resp.get("data") or {}).get("campaigns", [])
            all_ok = True
            for campaign in campaigns:
                campaign_ok = await _ingest_campaign(container_id, campaign)
                if not campaign_ok:
                    all_ok = False
            # Only advance `since` when every file in every campaign this tick
            # finished cleanly. A transient presign failure or 5xx must not
            # park a campaign forever — next tick will re-list it.
            if all_ok:
                since = poll_started_at
        except Exception:  # noqa: BLE001
            log.exception("extraction_poll_failed")
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)


async def _ingest_campaign(container_id: str, summary: dict[str, Any]) -> bool:
    """Ingest every non-complete file in a campaign. Returns True if every
    attempted file finished cleanly (or was already complete / skipped).
    Returning False tells the caller not to advance its `since` watermark,
    so the campaign stays in the next poll's delta window."""
    campaign_id = summary.get("id") or summary.get("campaign_id")
    if not campaign_id:
        return True
    db = get_db()
    acquired = await lease_service.acquire_campaign_lease(db, campaign_id, container_id)
    if not acquired:
        return True  # another container holds the lease — not our failure
    ok = True
    try:
        manifest_resp = await lpc_mod.leadpulse_client.get_campaign_manifest(campaign_id)
        manifest = manifest_resp.get("data") or manifest_resp
        campaign = manifest.get("campaign") or manifest
        files = manifest.get("files") or campaign.get("files") or []
        steps = manifest.get("steps") or campaign.get("sequence_snapshot") or []
        tenant_user_id = campaign.get("tenant_user_id") or campaign.get("user_id")
        # TK-2833: organization_id is added to the manifest by the CRM
        # (mcp-work.service.ts) when the campaign belongs to a workspace.
        # Personal/legacy campaigns leave this null.
        organization_id = campaign.get("organization_id")
        for file_info in files:
            status = file_info.get("ingestion_status")
            if status in ("complete", "failed"):
                continue
            file_ok = await _ingest_file(
                campaign_id=campaign_id,
                tenant_user_id=tenant_user_id,
                organization_id=organization_id,
                campaign=campaign,
                steps=steps,
                file_info=file_info,
            )
            if not file_ok:
                ok = False
    except Exception:  # noqa: BLE001
        log.exception("ingest_campaign_failed", extra={"extra_payload": {"campaign_id": campaign_id}})
        ok = False
    finally:
        await lease_service.release_campaign_lease(db, campaign_id, container_id)
    return ok


async def _ingest_file(
    *,
    campaign_id: str,
    tenant_user_id: str,
    organization_id: str | None,
    campaign: dict[str, Any],
    steps: list[dict[str, Any]],
    file_info: dict[str, Any],
) -> bool:
    """Ingest one file. Returns False only for transient manifest issues
    (missing presigned URL) so the outer loop keeps the campaign in its
    delta window. Parse / validation failures return True because they
    mark the file 'failed' on the CRM and should not retry forever."""
    db = get_db()
    file_id = file_info.get("file_id") or file_info.get("id") or ""
    presigned_url = file_info.get("presigned_url") or file_info.get("s3_url")
    filename = file_info.get("original_filename") or file_info.get("filename") or "unknown"
    if not presigned_url:
        log.error("ingest_missing_presigned_url", extra={"extra_payload": {"file_id": file_id}})
        return False

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
                await _report_file_failed(file_id, str(exc), campaign_id=campaign_id)
                return True
        else:
            await _report_file_failed(file_id, str(exc), campaign_id=campaign_id)
            return True

    # Caller-supplied column mapping from the CRM column-mapping UI. Keys
    # are the file's original headers, values are canonical field names.
    # Empty value (or missing key) leaves a header to alias auto-detection.
    raw_mapping = file_info.get("column_mapping") or {}
    column_mapping: dict[str, str] | None = (
        {str(k): str(v) for k, v in raw_mapping.items()}
        if isinstance(raw_mapping, dict) and raw_mapping
        else None
    )

    # Header validation — peek at the first row without materializing the
    # whole file. Fail fast if required columns are missing so the user
    # sees a clear error instead of an empty ingest result.
    try:
        headers = contact_parser.peek_headers(filename, content)
    except ValueError as exc:
        await _report_file_failed(file_id, f"unparseable: {exc}", campaign_id=campaign_id)
        return True
    header_report = contact_parser.validate_headers(headers, column_mapping)
    if header_report["missing_required"]:
        missing = ", ".join(header_report["missing_required"])  # type: ignore[arg-type]
        await _report_file_failed(
            file_id,
            f"missing required column(s): {missing}",
            validation={
                "matched": header_report["matched"],
                "missing_required": header_report["missing_required"],
                "unknown_headers": header_report["unknown"],
                "detected_headers": headers,
            },
            campaign_id=campaign_id,
        )
        return True

    rows: list[dict[str, Any]] = []
    row_stats: dict[str, int] = {}
    try:
        rows, row_stats = contact_parser.parse_stream_with_stats(
            filename, content, column_mapping
        )
    except ValueError as exc:
        await _report_file_failed(file_id, f"unparseable: {exc}", campaign_id=campaign_id)
        return True

    validation_block: dict[str, object] = {
        "matched": header_report["matched"],
        "unknown_headers": header_report["unknown"],
        "detected_headers": headers,
        **row_stats,
    }

    if not rows:
        await lpc_mod.leadpulse_client.post_file_ingested(
            {
                "campaign_id": campaign_id,
                "file_id": file_id,
                "row_count": 0,
                "error_count": row_stats.get("rows_missing_email", 0)
                + row_stats.get("rows_invalid_email", 0),
                "ingestion_status": "complete",
                "validation": validation_block,
            }
        )
        return True

    upsert_stats = await rcs.bulk_upsert(
        db, campaign_id, tenant_user_id, rows,
        source_file_id=file_id,
        organization_id=organization_id,
    )
    parse_errors = (
        upsert_stats.get("errors", 0)
        + row_stats.get("rows_missing_email", 0)
        + row_stats.get("rows_invalid_email", 0)
    )
    error_rate = parse_errors / max(1, len(rows) + parse_errors)

    if error_rate > _MAX_ROW_ERROR_RATE:
        await _report_file_failed(
            file_id, f"row error rate {error_rate:.0%} > 10%", campaign_id=campaign_id,
        )
        return True

    # Mint encrypted tracker ids up-front — one per (email, step_index) —
    # so URLs don't embed recipient email as plaintext and the CRM click
    # handler can decode full engagement context via its owner key.
    # Manifest-provided trackers (file_info.trackers) win; otherwise we
    # batch-mint from CRM. Last-resort fallback retained for defensiveness.
    manifest_trackers: dict[tuple[str, int], str] = {
        (t.get("email"), int(t.get("step_index", 0))): t.get("tracker_id")
        for t in (file_info.get("trackers") or [])
        if t.get("email") and t.get("tracker_id")
    }
    mint_requests: list[dict[str, Any]] = []
    for row in rows:
        for step in steps:
            key = (row["email"], int(step.get("step_index", 0)))
            if key in manifest_trackers:
                continue
            mint_requests.append({
                "email": row["email"],
                "step_index": int(step.get("step_index", 0)),
                "first_name": row.get("first_name") or None,
                "last_name": row.get("last_name") or None,
                "phone": row.get("phone") or None,
                "company": row.get("company") or None,
            })
    minted_trackers: dict[tuple[str, int], str] = {}
    if mint_requests:
        try:
            resp = await lpc_mod.leadpulse_client.mint_trackers(campaign_id, mint_requests)
            for t in (resp.get("data") or {}).get("trackers", []):
                minted_trackers[(t.get("email"), int(t.get("step_index", 0)))] = t.get("tracker_id")
        except Exception:  # noqa: BLE001
            log.exception("mint_trackers_failed", extra={"extra_payload": {"campaign_id": campaign_id}})

    start_date = _parse_start_date(campaign.get("start_date"))
    cfg = campaign.get("config") or campaign
    send_window_start = cfg.get("send_window_start", "09:00")
    tz_name = cfg.get("timezone", "UTC")

    inserted_sends = 0
    for row in rows:
        email = row["email"]
        contact_id = await _lookup_contact_id(db, campaign_id, email)
        for step in steps:
            step_idx = int(step.get("step_index", 0))
            scheduled = _compute_scheduled_for(start_date, step, send_window_start, tz_name)
            tracker_id = (
                manifest_trackers.get((email, step_idx))
                or minted_trackers.get((email, step_idx))
                or f"{campaign_id}:{email}:{step_idx}"
            )
            if await sqs.enqueue_send(
                db,
                campaign_id=campaign_id,
                tenant_user_id=tenant_user_id,
                organization_id=organization_id,
                contact_id=contact_id,
                email=email,
                step_index=step_idx,
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
            "campaign_id": campaign_id,
            "file_id": file_id,
            "row_count": len(rows),
            "error_count": parse_errors,
            "ingestion_status": "complete",
            "validation": validation_block,
        }
    )
    return True


async def _report_file_failed(
    file_id: str,
    message: str,
    validation: dict[str, object] | None = None,
    campaign_id: str | None = None,
) -> None:
    db = get_db()
    await db.ingest_errors.insert_one(
        {
            "campaign_id": campaign_id,
            "file_id": file_id,
            "message": message,
            "validation": validation,
            "ts": datetime.now(timezone.utc),
        }
    )
    try:
        payload: dict[str, object] = {
            "file_id": file_id,
            "row_count": 0,
            "error_count": 1,
            "ingestion_status": "failed",
            "error": message,
        }
        if campaign_id is not None:
            payload["campaign_id"] = campaign_id
        if validation is not None:
            payload["validation"] = validation
        await lpc_mod.leadpulse_client.post_file_ingested(payload)
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
