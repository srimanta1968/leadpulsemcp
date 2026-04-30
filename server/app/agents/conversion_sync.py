"""Conversion sync loop.

Polls CRM's ``GET /api/mcp/conversions?since=...`` every 60s. For each
returned (campaign_id, email, lead_id, kind) event, updates the local
Mongo state so the sender stops delivering remaining sequence steps:

  - campaign_contacts.status -> 'converted' with converted_lead_id + converted_at
  - send_queue.status -> 'skipped_converted' for all pending items in that
    (campaign, email) pair

Cursor (``last_converted_at``) is persisted in Mongo ``mcp_sync_cursors``
so restarts don't re-drain the entire history. Idempotent: re-applying a
conversion is a no-op (modified_count == 0).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger
from app.db.mongodb import get_db
from app.services import leadpulse_client as lpc_mod

log = get_logger(__name__)

_POLL_INTERVAL_SECONDS = 60
_CURSOR_ID = "conversion_sync"


async def _load_cursor() -> str | None:
    db = get_db()
    doc = await db.mcp_sync_cursors.find_one({"_id": _CURSOR_ID})
    return doc.get("since") if doc else None


async def _save_cursor(since_iso: str) -> None:
    db = get_db()
    await db.mcp_sync_cursors.update_one(
        {"_id": _CURSOR_ID},
        {"$set": {"since": since_iso, "updated_at": datetime.now(timezone.utc)}},
        upsert=True,
    )


async def _apply_conversion(event: dict[str, Any]) -> None:
    db = get_db()
    campaign_id = event["campaign_id"]
    email = str(event["email"]).lower().strip()
    lead_id = event.get("lead_id")
    now = datetime.now(timezone.utc)

    await db.campaign_contacts.update_one(
        {"campaign_id": campaign_id, "email": email},
        {
            "$set": {
                "status": "converted",
                "converted_lead_id": lead_id,
                "converted_at": now,
            }
        },
    )
    await db.send_queue.update_many(
        {"campaign_id": campaign_id, "email": email, "status": "pending"},
        {"$set": {"status": "skipped_converted", "updated_at": now}},
    )


async def run() -> None:
    since = await _load_cursor()
    while True:
        try:
            resp = await lpc_mod.leadpulse_client.get_conversions(since=since, limit=200)
            events = (resp.get("data") or {}).get("conversions", [])
            max_ts: str | None = since
            for ev in events:
                await _apply_conversion(ev)
                ts = ev.get("converted_at")
                if ts and (max_ts is None or ts > max_ts):
                    max_ts = ts
            if max_ts and max_ts != since:
                since = max_ts
                await _save_cursor(since)
            if events:
                log.info(
                    "conversion_sync_drained",
                    extra={"extra_payload": {"count": len(events), "cursor": since}},
                )
        except Exception as exc:  # noqa: BLE001
            # Never crash the loop — CRM flakiness is normal and the
            # supervisor will restart us if we do. Just log and back off.
            log.warning(
                "conversion_sync_error",
                extra={"extra_payload": {"err": str(exc)[:200]}},
            )
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)
