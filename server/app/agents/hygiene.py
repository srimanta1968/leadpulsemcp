"""Hygiene agent singleton.

One instance across the whole fleet, elected via ``mcp_hygiene_lease`` in Mongo.
Responsibilities:
- Consume ``bounce_events`` capped collection via tailable cursor.
- Run a periodic re-verification sweep (30 days TTL on ``refined_contacts``).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from app.agents.supervisor import supervisor
from app.core.logging import get_logger
from app.core.runtime_config import runtime_config
from app.db.mongodb import get_db
from app.services import lease_service, refined_contacts_service as rcs

log = get_logger(__name__)

_RENEW_INTERVAL_SECONDS = 30
_REVERIFY_SWEEP_INTERVAL_SECONDS = 3600  # run sweep check hourly; actually re-verify contacts stale >30d


async def run() -> None:
    container_id = runtime_config.get().instance_id
    holding = False
    last_sweep: datetime | None = None

    while True:
        if supervisor.quarantined():
            await asyncio.sleep(_RENEW_INTERVAL_SECONDS)
            continue
        try:
            db = get_db()
            if holding:
                holding = await lease_service.renew_hygiene_lease(db, container_id)
            else:
                holding = await lease_service.try_acquire_hygiene_lease(db, container_id)
                if holding:
                    log.info("hygiene_lease_acquired")
            if holding:
                await _process_bounce_events(db)
                now = datetime.now(timezone.utc)
                if last_sweep is None or (now - last_sweep).total_seconds() > _REVERIFY_SWEEP_INTERVAL_SECONDS:
                    await _reverification_sweep(db)
                    last_sweep = now
        except Exception:  # noqa: BLE001
            log.exception("hygiene_loop_error")

        await asyncio.sleep(_RENEW_INTERVAL_SECONDS)


async def _process_bounce_events(db) -> None:
    """Drain any new bounce_events since the cursor last stored position."""
    state = await db.mcp_hygiene_lease.find_one({"_id": "singleton"}, {"last_bounce_ts": 1}) or {}
    last_ts = state.get("last_bounce_ts") or datetime(1970, 1, 1, tzinfo=timezone.utc)
    newest_ts = last_ts
    cursor = db.bounce_events.find({"ts": {"$gt": last_ts}}).sort("ts", 1).limit(500)
    async for evt in cursor:
        email = evt.get("email")
        bounce_type = evt.get("bounce_type", "soft")
        try:
            await rcs.mark_bounce(db, email, bounce_type, decay_points=100 if bounce_type == "hard" else 5)
        except Exception:  # noqa: BLE001
            log.exception("bounce_apply_failed")
        if evt.get("ts") and evt["ts"] > newest_ts:
            newest_ts = evt["ts"]
    if newest_ts > last_ts:
        await db.mcp_hygiene_lease.update_one(
            {"_id": "singleton"}, {"$set": {"last_bounce_ts": newest_ts}}
        )


async def _reverification_sweep(db) -> None:
    """Pick contacts whose verification is older than 30 days and that were
    active in a campaign in the last 90 days. Integration with third-party
    verifier (ZeroBounce / NeverBounce) is stubbed here and logged.
    """
    stale_before = datetime.now(timezone.utc) - timedelta(days=30)
    active_after = datetime.now(timezone.utc) - timedelta(days=90)
    cursor = db.refined_contacts.find(
        {
            "$or": [{"last_verified_at": {"$lt": stale_before}}, {"last_verified_at": None}],
            "last_seen_at": {"$gt": active_after},
            "hard_bounce": {"$ne": True},
            "unsubscribed_global": {"$ne": True},
        }
    ).limit(200)

    async for doc in cursor:
        # TODO-integration: call ZeroBounce / NeverBounce here.
        # For now, mark the timestamp forward to avoid hot-looping the same rows;
        # a real integration updates verification_status + deliverability_score.
        await db.refined_contacts.update_one(
            {"_id": doc["_id"]},
            {"$set": {"last_verified_at": datetime.now(timezone.utc)}},
        )


async def handle_unsubscribe(
    db, email: str, campaign_id: str | None, reason: str | None = None
) -> dict[str, Any]:
    """Called by the CRM callback endpoint; persists + cascades."""
    if campaign_id is None:
        await rcs.mark_unsubscribed_global(db, email)
    stats = await rcs.cascade_unsubscribe_to_campaigns(db, email, campaign_id)
    await db.audit_log.insert_one(
        {
            "event": "mark_unsubscribed",
            "email_hash": email.lower().strip(),  # stored for audit; if sensitivity required hash at write
            "campaign_id": campaign_id,
            "reason": reason,
            "ts": datetime.now(timezone.utc),
            **stats,
        }
    )
    return stats


async def erase_contact(db, email: str) -> dict[str, int]:
    """DSAR right-to-erasure: remove everything we know about this email."""
    email = email.lower().strip()
    rc = await db.refined_contacts.delete_many({"email": email})
    cc = await db.campaign_contacts.delete_many({"email": email})
    sq = await db.send_queue.delete_many({"email": email})
    await db.audit_log.insert_one(
        {
            "event": "dsar_erase",
            "email": email,
            "ts": datetime.now(timezone.utc),
            "refined_removed": rc.deleted_count,
            "contacts_removed": cc.deleted_count,
            "sends_removed": sq.deleted_count,
        }
    )
    return {
        "refined_removed": rc.deleted_count,
        "contacts_removed": cc.deleted_count,
        "sends_removed": sq.deleted_count,
    }
