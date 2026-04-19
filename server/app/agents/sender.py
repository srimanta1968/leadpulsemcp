"""Sender agent loop.

Every 15s:
  1. Refresh active campaigns from CRM (every 60s, not every tick).
  2. lease a batch of due send_queue rows.
  3. For each: hygiene gate, resolve creds, render template, send, report.
  4. Sweep expired leases every ~30s (one agent in the container handles this).
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any

from app.agents.supervisor import supervisor
from app.core.logging import get_logger, hash_email
from app.core.metrics import metric_counter, metric_latency_ms
from app.core.runtime_config import runtime_config
from app.db.mongodb import get_db
from app.services import (
    email_sender,
    leadpulse_client as lpc_mod,
    refined_contacts_service as rcs,
    send_queue_service as sqs,
    template_renderer,
    throttle_service,
)

log = get_logger(__name__)

_POLL_INTERVAL_SECONDS = 15
_SWEEP_INTERVAL_SECONDS = 30
_ACTIVE_CAMPAIGNS_REFRESH_SECONDS = 60
_BATCH_SIZE = 8


async def run(agent_uid: str, is_sweeper: bool = False) -> None:
    cfg = runtime_config.get()
    last_active_refresh: float = 0.0
    active_campaigns_cache: dict[str, dict[str, Any]] = {}
    last_sweep: float = 0.0

    while True:
        if supervisor.quarantined():
            await asyncio.sleep(_POLL_INTERVAL_SECONDS)
            continue
        now = time.monotonic()

        if is_sweeper and now - last_sweep > _SWEEP_INTERVAL_SECONDS:
            try:
                await sqs.sweep_expired_leases(get_db())
            except Exception:  # noqa: BLE001
                log.exception("sweep_failed")
            last_sweep = now

        if now - last_active_refresh > _ACTIVE_CAMPAIGNS_REFRESH_SECONDS:
            try:
                active_campaigns_cache = await _refresh_active_campaigns()
                last_active_refresh = now
            except Exception:  # noqa: BLE001
                log.exception("refresh_active_campaigns_failed")

        if not active_campaigns_cache:
            await asyncio.sleep(_POLL_INTERVAL_SECONDS)
            continue

        try:
            batch = await sqs.lease_batch(
                get_db(), agent_uid, list(active_campaigns_cache.keys()), batch_size=_BATCH_SIZE,
            )
        except Exception:  # noqa: BLE001
            log.exception("lease_batch_failed")
            batch = []

        if not batch:
            await asyncio.sleep(_POLL_INTERVAL_SECONDS)
            continue

        for doc in batch:
            await _process_one(doc, active_campaigns_cache)


async def _refresh_active_campaigns() -> dict[str, dict[str, Any]]:
    resp = await lpc_mod.leadpulse_client.get_active_campaigns()
    out: dict[str, dict[str, Any]] = {}
    for c in (resp.get("data") or {}).get("campaigns", []):
        cid = c.get("id") or c.get("campaign_id")
        if cid:
            out[cid] = c
    return out


async def _process_one(doc: dict[str, Any], active_campaigns: dict[str, dict[str, Any]]) -> None:
    db = get_db()
    campaign_id = doc["campaign_id"]
    email = doc["email"]
    tenant_user_id = doc["tenant_user_id"]

    campaign_summary = active_campaigns.get(campaign_id) or {}
    if campaign_summary.get("paused"):
        await sqs.mark_status(db, doc["_id"], "pending", last_error="paused")
        return

    # Hygiene gate
    if await rcs.is_blocked(db, email):
        await sqs.mark_status(db, doc["_id"], "skipped_hygiene")
        return

    # Daily cap
    daily_cap = int(campaign_summary.get("daily_send_cap", 0) or 0)
    if daily_cap and not await throttle_service.try_consume_daily_cap(db, campaign_id, daily_cap):
        # Return to pending so it's reconsidered tomorrow.
        await sqs.mark_status(db, doc["_id"], "pending", last_error="daily_cap_reached")
        return

    # Provider rate limit
    provider_hint = (campaign_summary.get("provider") or "sendgrid")
    refill = int(campaign_summary.get("provider_refill_per_minute", 100))
    capacity = int(campaign_summary.get("provider_capacity", 200))
    if not await throttle_service.try_consume_token_bucket(
        db, tenant_user_id, provider_hint, refill, capacity
    ):
        if daily_cap:
            await throttle_service.release_daily_cap(db, campaign_id)
        await sqs.mark_status(db, doc["_id"], "pending", last_error="provider_rate_limited")
        return

    # Resolve credentials (cached 10 min)
    try:
        creds = await lpc_mod.leadpulse_client.resolve_sender_credentials(
            campaign_id, tenant_user_id
        )
    except Exception as exc:  # noqa: BLE001
        await sqs.mark_status(db, doc["_id"], "failed", last_error=f"resolve_secret: {exc}")
        if daily_cap:
            await throttle_service.release_daily_cap(db, campaign_id)
        return

    # Load contact
    contact = await db.campaign_contacts.find_one({"_id": doc.get("contact_id")})
    if contact is None:
        contact = await db.campaign_contacts.find_one({"campaign_id": campaign_id, "email": email})
    if contact is None:
        await sqs.mark_status(db, doc["_id"], "failed", last_error="contact_missing")
        return

    # Find matching step template
    steps = campaign_summary.get("sequence_snapshot") or campaign_summary.get("steps") or []
    step = next((s for s in steps if int(s.get("step_index", -1)) == int(doc["step_index"])), None)
    if step is None:
        await sqs.mark_status(db, doc["_id"], "failed", last_error="step_template_missing")
        return

    tracker_base = campaign_summary.get("tracking_domain") or runtime_config.get().leadpulse_url
    rendered = template_renderer.render_email(
        step=step,
        contact=contact,
        campaign=campaign_summary,
        tracker_id=doc.get("tracker_id", ""),
        tracker_base_url=tracker_base,
    )

    result = await email_sender.send_email(
        creds=creds,
        to_email=email,
        subject=rendered["subject"],
        body_html=rendered["body_html"],
        body_text=rendered["body_text"],
    )

    now_iso = datetime.now(timezone.utc).isoformat()
    log_extra = {
        "campaign_id": campaign_id,
        "email_hash": hash_email(email),
        "agent_uid": doc.get("leased_by"),
        "event": "send_result",
        "extra_payload": {"ok": result.ok, "provider": result.provider},
    }
    if result.ok:
        await sqs.mark_status(
            db, doc["_id"], "sent",
            provider_message_id=result.provider_message_id, provider=result.provider,
        )
        await throttle_service.increment_stat(db, campaign_id, "delivered", 1)
        metric_counter("mcp.sender.emails_sent_total", 1, {"provider": result.provider})
        try:
            await lpc_mod.leadpulse_client.post_tracker_event(
                {"trackerId": doc.get("tracker_id"), "event": "sent", "ts": now_iso}
            )
        except Exception:  # noqa: BLE001
            log.exception("tracker_event_failed")
        log.info("send_ok", extra=log_extra)
    elif result.bounce:
        await sqs.mark_status(db, doc["_id"], "bounced", last_error=result.error)
        metric_counter("mcp.sender.emails_bounced_total", 1,
                       {"provider": result.provider, "type": result.bounce_type or "hard"})
        await rcs.mark_bounce(db, email, result.bounce_type or "hard", decay_points=100)
        await throttle_service.increment_stat(
            db, campaign_id, "bounces_hard" if result.bounce_type == "hard" else "bounces_soft", 1
        )
        if daily_cap:
            await throttle_service.release_daily_cap(db, campaign_id)
        try:
            await lpc_mod.leadpulse_client.post_tracker_event(
                {
                    "trackerId": doc.get("tracker_id"),
                    "event": "bounced",
                    "ts": now_iso,
                    "meta": {"bounce_type": result.bounce_type, "error": result.error},
                }
            )
        except Exception:  # noqa: BLE001
            log.exception("bounce_event_failed")
        log.warning("send_bounce", extra=log_extra)
    else:
        await sqs.mark_status(db, doc["_id"], "failed", last_error=result.error)
        metric_counter("mcp.sender.emails_failed_total", 1, {"provider": result.provider})
        if daily_cap:
            await throttle_service.release_daily_cap(db, campaign_id)
        log.warning("send_failed", extra={**log_extra, "extra_payload": {"err": result.error}})

    # Completion detection
    try:
        if not await sqs.any_pending_for_campaign(db, campaign_id):
            await lpc_mod.leadpulse_client.post_campaign_step_complete(
                {"campaign_id": campaign_id, "step_index": int(doc["step_index"])}
            )
    except Exception:  # noqa: BLE001
        log.exception("completion_post_failed")
