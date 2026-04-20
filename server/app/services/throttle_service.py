"""Per-campaign daily cap + per-(user_id, provider) token-bucket rate limiter.

State persists in Mongo so all sender agents across the fleet share the
same counters atomically.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.db.mongodb import tenant_shard_key


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _current_hour_utc() -> int:
    return datetime.now(timezone.utc).hour


async def try_consume_tenant_daily_cap(
    db: AsyncIOMotorDatabase, tenant_user_id: str, daily_cap: int
) -> bool:
    """Atomically increment ``tenant_stats_daily.sends`` for today iff below
    the plan-tier cap. Separate from the per-campaign cap so a tenant
    running multiple campaigns sees one shared budget.

    Returns True when a slot was consumed or ``daily_cap<=0`` (no-op).
    The plan-tier cap itself arrives from the CRM's
    ``GET /api/mcp/tenant-quotas`` endpoint (still CRM backlog TK-2655);
    until that ships, callers pass 0 and this is a no-op.
    """
    if daily_cap <= 0:
        return True
    today = _today_utc()
    doc = await db.tenant_stats_daily.find_one_and_update(
        {"tenant_user_id": tenant_user_id, "date": today, "sends": {"$lt": daily_cap}},
        {
            "$inc": {"sends": 1},
            "$set": {"last_updated_at": datetime.now(timezone.utc)},
            "$setOnInsert": {
                "tenant_user_id": tenant_user_id,
                "shard_key": tenant_shard_key(tenant_user_id),
                "date": today,
            },
        },
        upsert=True,
        return_document=False,
    )
    if doc is None:
        fresh = await db.tenant_stats_daily.find_one(
            {"tenant_user_id": tenant_user_id, "date": today}, {"sends": 1}
        )
        return bool(fresh and fresh.get("sends", 0) <= daily_cap)
    return True


async def release_tenant_daily_cap(
    db: AsyncIOMotorDatabase, tenant_user_id: str
) -> None:
    today = _today_utc()
    await db.tenant_stats_daily.update_one(
        {"tenant_user_id": tenant_user_id, "date": today, "sends": {"$gt": 0}},
        {"$inc": {"sends": -1}},
    )


async def try_consume_hourly_cap(
    db: AsyncIOMotorDatabase,
    campaign_id: str,
    tenant_user_id: str,
    per_hour_cap: int,
) -> bool:
    """Atomically increment campaign_hourly_stats.sends for the current UTC
    hour iff below the per-hour cap.

    Uses a separate collection (campaign_hourly_stats) keyed by
    (campaign_id, date, hour_utc) so the daily-rollup schema stays unchanged.
    """
    if per_hour_cap <= 0:
        return True
    today = _today_utc()
    hour = _current_hour_utc()
    doc = await db.campaign_hourly_stats.find_one_and_update(
        {
            "campaign_id": campaign_id,
            "date": today,
            "hour_utc": hour,
            "sends": {"$lt": per_hour_cap},
        },
        {
            "$inc": {"sends": 1},
            "$set": {"last_updated_at": datetime.now(timezone.utc)},
            "$setOnInsert": {
                "campaign_id": campaign_id,
                "tenant_user_id": tenant_user_id,
                "date": today,
                "hour_utc": hour,
            },
        },
        upsert=True,
        return_document=False,
    )
    if doc is None:
        fresh = await db.campaign_hourly_stats.find_one(
            {"campaign_id": campaign_id, "date": today, "hour_utc": hour},
            {"sends": 1},
        )
        return bool(fresh and fresh.get("sends", 0) <= per_hour_cap)
    return True


async def release_hourly_cap(
    db: AsyncIOMotorDatabase, campaign_id: str, tenant_user_id: str
) -> None:
    today = _today_utc()
    hour = _current_hour_utc()
    await db.campaign_hourly_stats.update_one(
        {
            "campaign_id": campaign_id,
            "tenant_user_id": tenant_user_id,
            "date": today,
            "hour_utc": hour,
            "sends": {"$gt": 0},
        },
        {"$inc": {"sends": -1}},
    )


async def try_consume_daily_cap(
    db: AsyncIOMotorDatabase, campaign_id: str, tenant_user_id: str, daily_cap: int
) -> bool:
    """Atomically increment campaign_stats.sends for today iff below the cap."""
    if daily_cap <= 0:
        return True
    today = _today_utc()
    doc = await db.campaign_stats.find_one_and_update(
        {"campaign_id": campaign_id, "date": today, "sends": {"$lt": daily_cap}},
        {
            "$inc": {"sends": 1},
            "$set": {"last_updated_at": datetime.now(timezone.utc)},
            "$setOnInsert": {
                "campaign_id": campaign_id,
                "tenant_user_id": tenant_user_id,
                "shard_key": tenant_shard_key(tenant_user_id),
                "date": today,
            },
        },
        upsert=True,
        return_document=False,
    )
    # If we upserted a fresh doc, doc is None but we still consumed 1.
    if doc is None:
        # Check if the upsert happened (doc now has sends:1)
        fresh = await db.campaign_stats.find_one(
            {"campaign_id": campaign_id, "date": today}, {"sends": 1}
        )
        return bool(fresh and fresh.get("sends", 0) <= daily_cap)
    return True


async def release_daily_cap(
    db: AsyncIOMotorDatabase, campaign_id: str, tenant_user_id: str
) -> None:
    """Decrement counter if a send that reserved a slot ultimately failed."""
    today = _today_utc()
    await db.campaign_stats.update_one(
        {
            "campaign_id": campaign_id,
            "tenant_user_id": tenant_user_id,
            "date": today,
            "sends": {"$gt": 0},
        },
        {"$inc": {"sends": -1}},
    )


async def try_consume_token_bucket(
    db: AsyncIOMotorDatabase,
    user_id: str,
    provider: str,
    refill_per_minute: int,
    capacity: int,
) -> bool:
    """Return True if a token was consumed; False if the bucket is empty."""
    now = datetime.now(timezone.utc)
    bucket = await db.provider_rate_buckets.find_one_and_update(
        {"user_id": user_id, "provider": provider},
        {
            "$setOnInsert": {
                "user_id": user_id,
                "provider": provider,
                "tokens": capacity,
                "capacity": capacity,
                "refill_per_minute": refill_per_minute,
                "last_refill_at": now,
            }
        },
        upsert=True,
        return_document=True,
    )
    if bucket is None:
        return True

    # Refill based on elapsed minutes since last refill.
    last_refill: datetime = bucket.get("last_refill_at", now)
    if last_refill.tzinfo is None:
        last_refill = last_refill.replace(tzinfo=timezone.utc)
    elapsed_minutes = max(0.0, (now - last_refill).total_seconds() / 60.0)
    add_tokens = int(elapsed_minutes * refill_per_minute)
    new_tokens = min(capacity, bucket.get("tokens", capacity) + add_tokens)

    if new_tokens <= 0:
        await db.provider_rate_buckets.update_one(
            {"user_id": user_id, "provider": provider},
            {"$set": {"tokens": new_tokens, "last_refill_at": now, "capacity": capacity}},
        )
        return False

    res = await db.provider_rate_buckets.update_one(
        {"user_id": user_id, "provider": provider, "tokens": {"$gt": 0}},
        {
            "$inc": {"tokens": -1},
            "$set": {
                "tokens_after_refill_snapshot": new_tokens - 1,
                "last_refill_at": now,
                "capacity": capacity,
                "refill_per_minute": refill_per_minute,
            },
        },
    )
    return res.modified_count > 0


async def increment_stat(
    db: AsyncIOMotorDatabase,
    campaign_id: str,
    tenant_user_id: str,
    counter: str,
    delta: int = 1,
) -> None:
    """Generic daily-rollup counter increment (opens, clicks, bounces, etc.)."""
    today = _today_utc()
    await db.campaign_stats.update_one(
        {"campaign_id": campaign_id, "date": today},
        {
            "$inc": {counter: delta},
            "$set": {"last_updated_at": datetime.now(timezone.utc)},
            "$setOnInsert": {
                "campaign_id": campaign_id,
                "tenant_user_id": tenant_user_id,
                "shard_key": tenant_shard_key(tenant_user_id),
                "date": today,
            },
        },
        upsert=True,
    )


async def today_rollup(
    db: AsyncIOMotorDatabase, campaign_id: str, tenant_user_id: str | None = None
) -> dict[str, Any]:
    today = _today_utc()
    query: dict[str, Any] = {"campaign_id": campaign_id, "date": today}
    if tenant_user_id:
        query["tenant_user_id"] = tenant_user_id
    doc = await db.campaign_stats.find_one(query) or {}
    doc.pop("_id", None)
    return doc
