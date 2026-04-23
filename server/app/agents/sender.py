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

from app.agents import crm_connectivity
from app.agents.supervisor import supervisor
from app.core.logging import get_logger, hash_email
from app.core.metrics import metric_counter, metric_latency_ms
from app.core.runtime_config import runtime_config
from app.db.mongodb import get_db
from app.services import (
    email_sender,
    leadpulse_client as lpc_mod,
    pending_crm_events,
    refined_contacts_service as rcs,
    send_queue_service as sqs,
    template_renderer,
    tenant_quotas as tq_mod,
    throttle_service,
)

log = get_logger(__name__)

_POLL_INTERVAL_SECONDS = 15
_SWEEP_INTERVAL_SECONDS = 30
_ACTIVE_CAMPAIGNS_REFRESH_SECONDS = 60
_BATCH_SIZE = 8
_BACKPRESSURE_PAUSE_SECONDS = 60


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

        # CRM-connectivity gate: when heartbeats have been failing for
        # 30s+ (stop_new_work / degraded / isolated) we skip leasing new
        # send_queue docs. Anything already leased before this gate kicked
        # in still runs through mark_status normally — in-flight work
        # completes, only new work pauses. When heartbeats recover, we
        # resume automatically.
        if not crm_connectivity.can_lease_new_work():
            mode = crm_connectivity.current_mode()
            log.warning(
                "sender_paused_crm_connectivity",
                extra={"extra_payload": {"mode": mode.value, "pause_s": _POLL_INTERVAL_SECONDS}},
            )
            await asyncio.sleep(_POLL_INTERVAL_SECONDS)
            continue

        # Backpressure: when pending_crm_events is > 80% full, stop
        # leasing new sends for 60s so the replay worker can catch up.
        try:
            if await pending_crm_events.is_under_backpressure(get_db()):
                log.warning(
                    "sender_paused_crm_events_backpressure",
                    extra={"extra_payload": {"pause_s": _BACKPRESSURE_PAUSE_SECONDS}},
                )
                await asyncio.sleep(_BACKPRESSURE_PAUSE_SECONDS)
                continue
        except Exception:  # noqa: BLE001
            log.exception("backpressure_probe_failed")

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


def _within_send_window(campaign_summary: dict[str, Any], contact_tz: str | None = None) -> bool:
    from datetime import time as _time
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    cfg = campaign_summary.get("config") or campaign_summary
    # Per-contact tz wins over campaign tz when present. This lets a single
    # campaign target contacts across regions and still respect each
    # recipient's 09:00–17:00 local window.
    tz_name = contact_tz or cfg.get("timezone") or "UTC"
    start_s = cfg.get("send_window_start") or "00:00"
    end_s = cfg.get("send_window_end") or "23:59"
    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("UTC")
    try:
        sh, sm = (int(x) for x in start_s.split(":"))
        eh, em = (int(x) for x in end_s.split(":"))
    except (ValueError, AttributeError):
        return True
    now_local = datetime.now(tz).time()
    start_t = _time(sh, sm)
    end_t = _time(eh, em)
    if start_t <= end_t:
        return start_t <= now_local <= end_t
    # Window wraps past midnight (e.g. 22:00-06:00) — treat as union.
    return now_local >= start_t or now_local <= end_t


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
    # Pre-send campaign-status sync-check: the active_campaigns cache
    # refreshes every 60s — if a campaign was paused/completed in between,
    # skip this send immediately rather than consume a slot. Belt-and-
    # braces next to the `paused` boolean because CRM may flip the status
    # enum (running -> paused/completed) without toggling the legacy
    # `paused` field.
    status = str(campaign_summary.get("status") or "").lower()
    if campaign_summary.get("paused") or status in ("paused", "completed", "cancelled"):
        await sqs.mark_status(
            db, doc["_id"], "pending", last_error=f"campaign_{status or 'paused'}"
        )
        return
    if status and status != "running":
        await sqs.mark_status(db, doc["_id"], "pending", last_error=f"campaign_{status}")
        return

    # Per-contact tz lookup (cheap projection). Falls back to campaign tz
    # inside _within_send_window when absent.
    contact_tz: str | None = None
    tz_doc = await db.refined_contacts.find_one({"email": email}, {"tz": 1})
    if tz_doc and tz_doc.get("tz"):
        contact_tz = str(tz_doc["tz"])

    # Campaign send-window gate — defer if outside [send_window_start, send_window_end]
    # in the contact's (or campaign's) configured timezone. Keeps sends aligned
    # with each recipient's local business hours.
    if not _within_send_window(campaign_summary, contact_tz):
        await sqs.mark_status(db, doc["_id"], "pending", last_error="outside_window")
        return

    # Hygiene gate
    if await rcs.is_blocked(db, email):
        await sqs.mark_status(db, doc["_id"], "skipped_hygiene")
        return

    # Plan-tier per-tenant daily cap (from CRM tenant-quotas cache).
    # No-op when the CRM endpoint hasn't shipped — tq.get returns None.
    tq = tq_mod.tenant_quotas.get(tenant_user_id)
    tenant_daily_cap = tq.daily_cap if tq else 0
    if tenant_daily_cap and not await throttle_service.try_consume_tenant_daily_cap(
        db, tenant_user_id, tenant_daily_cap
    ):
        await sqs.mark_status(
            db, doc["_id"], "pending", last_error="tenant_daily_cap_reached"
        )
        return

    # Per-campaign daily cap
    cfg = campaign_summary.get("config") or campaign_summary
    daily_cap = int(cfg.get("daily_send_cap", 0) or campaign_summary.get("daily_send_cap", 0) or 0)
    if daily_cap and not await throttle_service.try_consume_daily_cap(db, campaign_id, tenant_user_id, daily_cap):
        # Return to pending so it's reconsidered tomorrow.
        if tenant_daily_cap:
            await throttle_service.release_tenant_daily_cap(db, tenant_user_id)
        await sqs.mark_status(db, doc["_id"], "pending", last_error="daily_cap_reached")
        return

    # Hourly cap (plan-tier throttle_per_hour from campaign config_snapshot)
    per_hour_cap = int(cfg.get("throttle_per_hour", 0) or campaign_summary.get("throttle_per_hour", 0) or 0)
    if per_hour_cap and not await throttle_service.try_consume_hourly_cap(
        db, campaign_id, tenant_user_id, per_hour_cap
    ):
        if daily_cap:
            await throttle_service.release_daily_cap(db, campaign_id, tenant_user_id)
        if per_hour_cap:
            await throttle_service.release_hourly_cap(db, campaign_id, tenant_user_id)
        await sqs.mark_status(db, doc["_id"], "pending", last_error="hour_cap_reached")
        return

    # Provider rate limit
    provider_hint = (campaign_summary.get("provider") or "sendgrid")
    refill = int(campaign_summary.get("provider_refill_per_minute", 100))
    capacity = int(campaign_summary.get("provider_capacity", 200))
    if not await throttle_service.try_consume_token_bucket(
        db, tenant_user_id, provider_hint, refill, capacity
    ):
        if daily_cap:
            await throttle_service.release_daily_cap(db, campaign_id, tenant_user_id)
        if per_hour_cap:
            await throttle_service.release_hourly_cap(db, campaign_id, tenant_user_id)
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
            await throttle_service.release_daily_cap(db, campaign_id, tenant_user_id)
        if per_hour_cap:
            await throttle_service.release_hourly_cap(db, campaign_id, tenant_user_id)
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
        # Make the per-send key unique by folding in the attempts counter.
        # A plain (campaign,email,step) key is deterministic across retries
        # which makes recipient MTAs (Gmail et al) dedupe on Message-Id —
        # so only the first attempt ever lands. custom_args.idempotency_key
        # at the provider level still carries the stable key for dedupe
        # against our own crashed-between-send-and-write race.
        idempotency_key=(
            f"{doc.get('idempotency_key')}-a{int(doc.get('attempts', 1))}"
            if doc.get("idempotency_key") else None
        ),
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
        await throttle_service.increment_stat(db, campaign_id, tenant_user_id, "delivered", 1)
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
            db, campaign_id, tenant_user_id, "bounces_hard" if result.bounce_type == "hard" else "bounces_soft", 1
        )
        if daily_cap:
            await throttle_service.release_daily_cap(db, campaign_id, tenant_user_id)
        if per_hour_cap:
            await throttle_service.release_hourly_cap(db, campaign_id, tenant_user_id)
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
            await throttle_service.release_daily_cap(db, campaign_id, tenant_user_id)
        if per_hour_cap:
            await throttle_service.release_hourly_cap(db, campaign_id, tenant_user_id)
        log.warning("send_failed", extra={**log_extra, "extra_payload": {"err": result.error}})

    # Completion detection
    try:
        if not await sqs.any_pending_for_campaign(db, campaign_id):
            await lpc_mod.leadpulse_client.post_campaign_step_complete(
                {"campaign_id": campaign_id, "step_index": int(doc["step_index"])}
            )
    except Exception:  # noqa: BLE001
        log.exception("completion_post_failed")
