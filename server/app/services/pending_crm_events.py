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


_MAX_ATTEMPTS = 20
_PERMANENT_STATUS_MARKERS = ("'404", "'410", "'400")


def _is_permanent(err_text: str) -> bool:
    return any(marker in err_text for marker in _PERMANENT_STATUS_MARKERS)


async def drain_once(
    db: AsyncIOMotorDatabase,
    poster: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]],
    batch_size: int = 50,
) -> int:
    """Send up to ``batch_size`` buffered events. Returns count successfully sent.

    Permanently-failing events (CRM 400/404/410 — typically from a file the
    user deleted in the UI) are dropped to the dead-letter collection so
    they don't head-of-line-block newer events. Transient failures break
    the loop and are retried on the next tick.
    """
    sent = 0
    cursor = db.pending_crm_events.find({}).sort("queued_at", 1).limit(batch_size)
    async for doc in cursor:
        try:
            await poster(doc["endpoint"], doc["payload"])
            await db.pending_crm_events.delete_one({"_id": doc["_id"]})
            sent += 1
        except Exception as exc:  # noqa: BLE001
            err_text = str(exc)
            attempts = int(doc.get("attempts") or 0) + 1
            if _is_permanent(err_text) or attempts >= _MAX_ATTEMPTS:
                await db.pending_crm_events.delete_one({"_id": doc["_id"]})
                await db.pending_crm_events_dlq.insert_one(
                    {
                        **{k: v for k, v in doc.items() if k != "_id"},
                        "attempts": attempts,
                        "last_error": err_text[:300],
                        "dropped_at": datetime.now(timezone.utc),
                    }
                )
                log.warning(
                    "pending_event_dead_lettered",
                    extra={
                        "extra_payload": {
                            "endpoint": doc.get("endpoint"),
                            "file_id": (doc.get("payload") or {}).get("file_id"),
                            "attempts": attempts,
                            "permanent": _is_permanent(err_text),
                        }
                    },
                )
                continue
            await db.pending_crm_events.update_one(
                {"_id": doc["_id"]},
                {"$inc": {"attempts": 1}, "$set": {"last_error": err_text[:300]}},
            )
            break
    if sent:
        log.info("pending_events_drained", extra={"extra_payload": {"count": sent}})
    return sent
