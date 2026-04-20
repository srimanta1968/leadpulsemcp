"""Send-queue operations: enqueue, lease, mark status, sweep expired leases."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument, ReturnDocument as _RD

from app.core.logging import get_logger
from app.db.mongodb import tenant_shard_key

log = get_logger(__name__)

SEND_LEASE_SECONDS = 300
_TENANT_CURSOR_ID = "global"  # single shared cursor across the fleet


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def enqueue_send(
    db: AsyncIOMotorDatabase,
    *,
    campaign_id: str,
    tenant_user_id: str,
    contact_id: Any,
    email: str,
    step_index: int,
    tracker_id: str,
    scheduled_for: datetime,
) -> bool:
    """Upsert a single (campaign, email, step) send_queue doc. Returns True on insert."""
    doc = {
        "_id": ObjectId(),
        "campaign_id": campaign_id,
        "tenant_user_id": tenant_user_id,
        "shard_key": tenant_shard_key(tenant_user_id),
        "contact_id": contact_id,
        "email": email.lower().strip(),
        "step_index": step_index,
        "tracker_id": tracker_id,
        "scheduled_for": scheduled_for,
        "status": "pending",
        "attempts": 0,
        "created_at": _now(),
        "updated_at": _now(),
    }
    res = await db.send_queue.update_one(
        {"campaign_id": campaign_id, "email": doc["email"], "step_index": step_index},
        {"$setOnInsert": doc, "$set": {"updated_at": _now()}},
        upsert=True,
    )
    return res.upserted_id is not None


async def _next_tenant_in_round_robin(
    db: AsyncIOMotorDatabase,
    active_campaign_ids: list[str],
) -> str | None:
    """Advance the shared tenant_cursors doc and return the next tenant
    with pending due work. Skips tenants that have no eligible docs so an
    idle tenant never starves a busy one.

    Single cursor keyed ``_id="global"`` — shared by every container in
    the fleet (Phase 1 is single-pool; see docs/3phase_implementation.md).
    """
    now = _now()
    cursor = await db.tenant_cursors.find_one_and_update(
        {"_id": _TENANT_CURSOR_ID},
        {"$setOnInsert": {"_id": _TENANT_CURSOR_ID, "last_picked_at": now}},
        upsert=True,
        return_document=_RD.AFTER,
    )
    last_tenant = (cursor or {}).get("last_tenant_id")

    # All tenant_user_ids with due pending work in the active campaign set.
    tenants = await db.send_queue.distinct(
        "tenant_user_id",
        {
            "status": "pending",
            "scheduled_for": {"$lte": now},
            "campaign_id": {"$in": active_campaign_ids},
        },
    )
    if not tenants:
        return None
    tenants.sort()
    # Pick the first tenant strictly greater than last_tenant; wrap around.
    nxt = next((t for t in tenants if last_tenant is None or t > last_tenant), tenants[0])
    await db.tenant_cursors.update_one(
        {"_id": _TENANT_CURSOR_ID},
        {"$set": {"last_tenant_id": nxt, "last_picked_at": now}},
    )
    return nxt


async def lease_batch(
    db: AsyncIOMotorDatabase,
    agent_uid: str,
    active_campaign_ids: list[str],
    batch_size: int = 10,
) -> list[dict[str, Any]]:
    """Atomically claim up to ``batch_size`` due pending docs from a single
    tenant picked by the round-robin cursor. One tenant per batch prevents
    whales from starving small tenants: the cursor advances so the next
    poll hits a different tenant.
    """
    if not active_campaign_ids:
        return []

    tenant = await _next_tenant_in_round_robin(db, active_campaign_ids)
    if tenant is None:
        return []

    now = _now()
    expires = now + timedelta(seconds=SEND_LEASE_SECONDS)
    leased: list[dict[str, Any]] = []
    for _ in range(batch_size):
        doc = await db.send_queue.find_one_and_update(
            {
                "status": "pending",
                "scheduled_for": {"$lte": now},
                "campaign_id": {"$in": active_campaign_ids},
                "tenant_user_id": tenant,
            },
            {
                "$set": {
                    "status": "leased",
                    "leased_by": agent_uid,
                    "lease_expires_at": expires,
                    "updated_at": now,
                },
                "$inc": {"attempts": 1},
            },
            sort=[("scheduled_for", 1)],
            return_document=ReturnDocument.AFTER,
        )
        if doc is None:
            break
        leased.append(doc)
    return leased


async def mark_status(
    db: AsyncIOMotorDatabase,
    doc_id: Any,
    status: str,
    *,
    provider_message_id: str | None = None,
    provider: str | None = None,
    last_error: str | None = None,
) -> None:
    update: dict[str, Any] = {"status": status, "updated_at": _now()}
    if status == "sent":
        update["sent_at"] = _now()
    if provider_message_id:
        update["provider_message_id"] = provider_message_id
    if provider:
        update["provider"] = provider
    if last_error:
        update["last_error"] = last_error
    await db.send_queue.update_one({"_id": doc_id}, {"$set": update})


async def sweep_expired_leases(db: AsyncIOMotorDatabase) -> int:
    """Revert send_queue docs whose lease expired back to pending. Returns count."""
    now = _now()
    result = await db.send_queue.update_many(
        {"status": "leased", "lease_expires_at": {"$lt": now}},
        {"$set": {"status": "pending", "updated_at": now}, "$unset": {"leased_by": "", "lease_expires_at": ""}},
    )
    if result.modified_count:
        log.warning(
            "send_queue_lease_swept", extra={"extra_payload": {"count": result.modified_count}}
        )
    return result.modified_count


async def any_pending_for_campaign(db: AsyncIOMotorDatabase, campaign_id: str) -> bool:
    return (
        await db.send_queue.count_documents(
            {"campaign_id": campaign_id, "status": {"$in": ["pending", "leased"]}}, limit=1
        )
        > 0
    )


async def pending_depth(db: AsyncIOMotorDatabase, campaign_id: str) -> int:
    return await db.send_queue.count_documents(
        {"campaign_id": campaign_id, "status": "pending"}
    )
