"""Mongo-backed leases for the extraction and hygiene agents.

- Campaign leases: TTL 10 min, renewed every 5 min while the agent is working.
- Hygiene lease: TTL 90s, renewed every 30s; single-writer election.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError

CAMPAIGN_LEASE_TTL_SECONDS = 600
CAMPAIGN_LEASE_RENEW_SECONDS = 300
HYGIENE_LEASE_TTL_SECONDS = 90
HYGIENE_LEASE_RENEW_SECONDS = 30


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def acquire_campaign_lease(
    db: AsyncIOMotorDatabase, campaign_id: str, container_id: str
) -> bool:
    """Try to acquire a 10-minute lease on this campaign. Returns True on success.

    Uses upsert with filter on (expired OR already held by us) — so if an
    expired lease exists we take it; if someone else holds an active lease
    we return False.
    """
    now = _now()
    expires = now + timedelta(seconds=CAMPAIGN_LEASE_TTL_SECONDS)
    result = await db.campaign_leases.update_one(
        {
            "_id": campaign_id,
            "$or": [
                {"expires_at": {"$lt": now}},
                {"held_by": container_id},
            ],
        },
        {"$set": {"held_by": container_id, "acquired_at": now, "expires_at": expires}},
        upsert=False,
    )
    if result.matched_count:
        return True

    # No existing doc — insert a fresh one. If another container races us, one
    # wins via the unique _id.
    try:
        await db.campaign_leases.insert_one(
            {
                "_id": campaign_id,
                "held_by": container_id,
                "acquired_at": now,
                "expires_at": expires,
            }
        )
        return True
    except DuplicateKeyError:
        return False


async def renew_campaign_lease(
    db: AsyncIOMotorDatabase, campaign_id: str, container_id: str
) -> bool:
    now = _now()
    expires = now + timedelta(seconds=CAMPAIGN_LEASE_TTL_SECONDS)
    result = await db.campaign_leases.update_one(
        {"_id": campaign_id, "held_by": container_id},
        {"$set": {"expires_at": expires}},
    )
    return result.modified_count > 0


async def release_campaign_lease(
    db: AsyncIOMotorDatabase, campaign_id: str, container_id: str
) -> None:
    await db.campaign_leases.delete_one({"_id": campaign_id, "held_by": container_id})


async def try_acquire_hygiene_lease(
    db: AsyncIOMotorDatabase, container_id: str
) -> bool:
    now = _now()
    expires = now + timedelta(seconds=HYGIENE_LEASE_TTL_SECONDS)
    result = await db.mcp_hygiene_lease.update_one(
        {
            "_id": "singleton",
            "$or": [
                {"expires_at": {"$lt": now}},
                {"held_by": container_id},
            ],
        },
        {"$set": {"held_by": container_id, "acquired_at": now, "expires_at": expires}},
    )
    if result.matched_count:
        return True
    try:
        await db.mcp_hygiene_lease.insert_one(
            {
                "_id": "singleton",
                "held_by": container_id,
                "acquired_at": now,
                "expires_at": expires,
            }
        )
        return True
    except DuplicateKeyError:
        return False


async def renew_hygiene_lease(db: AsyncIOMotorDatabase, container_id: str) -> bool:
    now = _now()
    expires = now + timedelta(seconds=HYGIENE_LEASE_TTL_SECONDS)
    result = await db.mcp_hygiene_lease.update_one(
        {"_id": "singleton", "held_by": container_id},
        {"$set": {"expires_at": expires}},
    )
    return result.modified_count > 0
