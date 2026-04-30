"""Send-queue operations: enqueue, lease, mark status, sweep expired leases."""
from __future__ import annotations

import hashlib
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


def compute_idempotency_key(campaign_id: str, email: str, step_index: int) -> str:
    # Stable across retries so providers/recipients can dedupe if a crash
    # between SMTP success and status write causes a re-send.
    payload = f"{campaign_id}|{email.lower().strip()}|{step_index}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


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
    organization_id: str | None = None,
) -> bool:
    """Upsert a single (campaign, email, step) send_queue doc. Returns True on insert.

    TK-2833: ``organization_id`` is stamped from the campaign manifest so
    per-team analytics can group sends by workspace. Idempotency key
    intentionally still uses ``(campaign_id, email, step_index)`` — adding
    org_id would never differ since campaign_id already implies the org.
    """
    normalized_email = email.lower().strip()
    # updated_at lives only in $set — keeping it here too would make Mongo
    # reject the upsert with "Updating the path 'updated_at' would create
    # a conflict at 'updated_at'". $set runs on both insert and update so
    # the field still gets a fresh timestamp on creation.
    doc = {
        "_id": ObjectId(),
        "campaign_id": campaign_id,
        "tenant_user_id": tenant_user_id,
        "organization_id": organization_id,
        "shard_key": tenant_shard_key(tenant_user_id),
        "contact_id": contact_id,
        "email": normalized_email,
        "step_index": step_index,
        "tracker_id": tracker_id,
        "scheduled_for": scheduled_for,
        "status": "pending",
        "attempts": 0,
        "idempotency_key": compute_idempotency_key(campaign_id, normalized_email, step_index),
        "created_at": _now(),
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
        # Lazy-backfill idempotency_key for docs enqueued before the field existed.
        if not doc.get("idempotency_key"):
            key = compute_idempotency_key(
                doc["campaign_id"], doc["email"], int(doc.get("step_index", 0))
            )
            await db.send_queue.update_one(
                {"_id": doc["_id"]}, {"$set": {"idempotency_key": key}}
            )
            doc["idempotency_key"] = key
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


async def campaign_progress_snapshot(
    db: AsyncIOMotorDatabase, campaign_ids: list[str]
) -> list[dict[str, Any]]:
    """Per-campaign totals + per-step breakdown for the CRM campaign dashboard.

    One aggregation pass over send_queue with `(campaign, status)` and
    `(campaign, step, status)` groupings. Returns one doc per campaign
    containing:
        {
          campaign_id,
          totals: {pending, leased, sent, failed, bounced, skipped},
          by_step: [{step_index, pending, leased, sent, failed, bounced, skipped}],
        }
    Skipped = skipped_hygiene + skipped_converted — surface as one bucket
    since the UI doesn't differentiate and summing keeps the payload
    small.

    The CRM stores the latest snapshot per (campaign, instance) and
    aggregates across containers for the dashboard.
    """
    if not campaign_ids:
        return []

    # One $group that rolls everything up; we split totals vs by_step in
    # Python so the index on (campaign_id, status) still does the heavy
    # lifting and we avoid a double-pass.
    pipeline = [
        {"$match": {"campaign_id": {"$in": campaign_ids}}},
        {
            "$group": {
                "_id": {
                    "campaign_id": "$campaign_id",
                    "step_index": "$step_index",
                    "status": "$status",
                },
                "count": {"$sum": 1},
            }
        },
    ]

    def _bucket(status: str) -> str:
        if status == "sent":
            return "sent"
        if status == "bounced":
            return "bounced"
        if status == "failed":
            return "failed"
        if status in ("skipped_hygiene", "skipped_converted"):
            return "skipped"
        if status == "leased":
            return "leased"
        return "pending"

    per_campaign: dict[str, dict[str, Any]] = {}
    async for doc in db.send_queue.aggregate(pipeline):
        cid = doc["_id"]["campaign_id"]
        step = int(doc["_id"].get("step_index", 0) or 0)
        bucket = _bucket(str(doc["_id"]["status"]))
        count = int(doc["count"])
        entry = per_campaign.setdefault(
            cid,
            {
                "campaign_id": cid,
                "totals": {
                    "pending": 0,
                    "leased": 0,
                    "sent": 0,
                    "failed": 0,
                    "bounced": 0,
                    "skipped": 0,
                },
                "by_step": {},
            },
        )
        entry["totals"][bucket] += count
        step_entry = entry["by_step"].setdefault(
            step,
            {
                "step_index": step,
                "pending": 0,
                "leased": 0,
                "sent": 0,
                "failed": 0,
                "bounced": 0,
                "skipped": 0,
            },
        )
        step_entry[bucket] += count

    out: list[dict[str, Any]] = []
    for cid in campaign_ids:
        entry = per_campaign.get(cid)
        if entry is None:
            continue
        # Flatten by_step into a sorted list.
        entry["by_step"] = sorted(entry["by_step"].values(), key=lambda s: s["step_index"])
        out.append(entry)
    return out
