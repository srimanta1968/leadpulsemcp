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
