"""Daily-rollup flusher loop.

Every 5 minutes, push today's and yesterday's per-campaign counters from
``db.campaign_stats`` (incremented by ``throttle_service.increment_stat``)
up to the CRM via POST /api/mcp/daily-rollup. Without this, the CRM's
``campaigns.stats_daily`` column stays empty even after the MCP delivers
mail — which makes the forecast believe ``today_sent=0`` and keeps the
fleet warm during send hours even when there's nothing left to send.

The CRM endpoint is idempotent (``jsonb_set`` overwrite), so a fixed
recent-days window is safer than a watermark — late-arriving bounce
events for yesterday still propagate without bookkeeping.

We also mirror ``delivered`` into ``sends`` so the CRM forecast (which
reads ``stats_daily[date].sends``) sees the actual send count even when
the per-event tracker-event POST drops something.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from app.agents.supervisor import supervisor
from app.core.logging import get_logger
from app.db.mongodb import get_db
from app.services import leadpulse_client as lpc_mod

log = get_logger(__name__)

_FLUSH_INTERVAL_SECONDS = 5 * 60
_LOOKBACK_DAYS = 2  # today + yesterday (UTC) covers late deliveries

# Keys we forward to the CRM. Names match the whitelist in
# mcp-feedback.service.ts applyDailyRollup; anything else is dropped.
_ALLOWED_COUNTERS = (
    "sends",
    "delivered",
    "opens",
    "clicks",
    "bounces_hard",
    "bounces_soft",
    "unsubscribes",
    "spam_reports",
    "leads_created",
    "appointments_booked",
)


async def run() -> None:
    while True:
        if supervisor.quarantined():
            await asyncio.sleep(_FLUSH_INTERVAL_SECONDS)
            continue
        try:
            await _flush_once()
        except Exception:  # noqa: BLE001
            log.exception("rollup_flush_failed")
        await asyncio.sleep(_FLUSH_INTERVAL_SECONDS)


async def _flush_once() -> None:
    db = get_db()
    today = datetime.now(timezone.utc).date()
    dates = [(today - timedelta(days=i)).isoformat() for i in range(_LOOKBACK_DAYS)]

    cursor = db.campaign_stats.find({"date": {"$in": dates}})
    pushed = 0
    async for doc in cursor:
        counters: dict[str, int] = {}
        for key in _ALLOWED_COUNTERS:
            val = doc.get(key)
            if isinstance(val, (int, float)) and val >= 0:
                counters[key] = int(val)
        # Mirror delivered → sends when sends is missing or zero. The CRM
        # forecast reads stats_daily[date].sends to compute remaining work;
        # without this the forecast thinks zero have shipped even after
        # successful delivery, and the fleet won't scale down.
        if counters.get("delivered", 0) > 0 and counters.get("sends", 0) == 0:
            counters["sends"] = counters["delivered"]
        if not counters:
            continue
        try:
            await lpc_mod.leadpulse_client.post_daily_rollup(
                {
                    "campaign_id": doc.get("campaign_id"),
                    "date": doc.get("date"),
                    "counters": counters,
                }
            )
            pushed += 1
        except Exception:  # noqa: BLE001
            log.exception(
                "rollup_push_failed",
                extra={"extra_payload": {
                    "campaign_id": doc.get("campaign_id"),
                    "date": doc.get("date"),
                }},
            )
    if pushed:
        log.info("rollup_flush_done", extra={"extra_payload": {"pushed": pushed}})
