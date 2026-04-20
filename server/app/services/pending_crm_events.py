"""Buffer + replay for MCP->CRM events when the CRM is unreachable.

Used by the leadpulse_client on circuit-open; drained by a background worker
when the breaker closes again.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.logging import get_logger

log = get_logger(__name__)

# pending_crm_events is a capped collection (see db/mongodb.py — max 5M docs).
# When it climbs above this fraction of capacity, the sender agent pauses new
# leases to let the replay worker catch up, preventing cascading failure when
# the CRM is down/slow. Phase 1 change #7 in docs/3phase_implementation.md.
_BACKPRESSURE_FILL_FRACTION = 0.8
_CAPPED_MAX = 5_000_000
_BACKPRESSURE_THRESHOLD = int(_CAPPED_MAX * _BACKPRESSURE_FILL_FRACTION)


async def is_under_backpressure(db: AsyncIOMotorDatabase) -> bool:
    """True when the pending_crm_events capped collection is > 80% full.

    Callers (sender loop) should pause new send_queue leases until this
    flips back to False on the next tick.
    """
    count = await db.pending_crm_events.estimated_document_count()
    return count >= _BACKPRESSURE_THRESHOLD


async def buffer_event(
    db: AsyncIOMotorDatabase, endpoint: str, payload: dict[str, Any]
) -> None:
    await db.pending_crm_events.insert_one(
        {
            "_id": ObjectId(),
            "endpoint": endpoint,
            "payload": payload,
            "queued_at": datetime.now(timezone.utc),
            "attempts": 0,
        }
    )


async def drain_once(
    db: AsyncIOMotorDatabase,
    poster: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]],
    batch_size: int = 50,
) -> int:
    """Send up to ``batch_size`` buffered events. Returns count successfully sent."""
    sent = 0
    cursor = db.pending_crm_events.find({}).sort("queued_at", 1).limit(batch_size)
    async for doc in cursor:
        try:
            await poster(doc["endpoint"], doc["payload"])
            await db.pending_crm_events.delete_one({"_id": doc["_id"]})
            sent += 1
        except Exception as exc:  # noqa: BLE001
            await db.pending_crm_events.update_one(
                {"_id": doc["_id"]},
                {"$inc": {"attempts": 1}, "$set": {"last_error": str(exc)[:300]}},
            )
            break
    if sent:
        log.info("pending_events_drained", extra={"extra_payload": {"count": sent}})
    return sent
