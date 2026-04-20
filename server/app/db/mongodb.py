"""Async MongoDB client + schema provisioning for the MCP.

All operational collections defined by the design doc (section 4) are created
with their indexes and TTLs here. Capped collections are used for hot-streaming
operational data (bounce_events, pending_crm_events).
"""
from __future__ import annotations

import hashlib

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from app.core.logging import get_logger
from app.core.runtime_config import runtime_config


def tenant_shard_key(tenant_user_id: str) -> str:
    """Deterministic hash used as the Mongo shard key on multi-tenant
    collections. Pre-declared even before sharding is enabled so Phase 2
    sharding needs zero backfill (see docs/3phase_implementation.md).
    """
    return hashlib.sha256(str(tenant_user_id).encode("utf-8")).hexdigest()[:16]

log = get_logger(__name__)

_client: AsyncIOMotorClient | None = None
_db: AsyncIOMotorDatabase | None = None

_CAPPED_COLLECTIONS = {
    "bounce_events": {"size": 100 * 1024 * 1024, "max": 1_000_000},
    "pending_crm_events": {"size": 200 * 1024 * 1024, "max": 5_000_000},
}


async def _ensure_capped(db: AsyncIOMotorDatabase, name: str, spec: dict) -> None:
    existing = await db.list_collection_names(filter={"name": name})
    if name in existing:
        return
    await db.create_collection(name, capped=True, size=spec["size"], max=spec["max"])


async def _ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    # campaign_contacts
    await db.campaign_contacts.create_index(
        [("campaign_id", 1), ("email", 1)], unique=True, name="uniq_campaign_email"
    )
    await db.campaign_contacts.create_index("email", name="by_email")
    await db.campaign_contacts.create_index(
        [("campaign_id", 1), ("status", 1)], name="by_campaign_status"
    )

    # refined_contacts
    await db.refined_contacts.create_index("email", unique=True, name="uniq_email")
    await db.refined_contacts.create_index(
        [("primary.job_title", 1), ("primary.company_domain", 1)], name="commerce_query"
    )
    await db.refined_contacts.create_index(
        [("deliverability_score", -1), ("hard_bounce", 1)], name="hygiene_rank"
    )
    await db.refined_contacts.create_index("last_verified_at", name="verify_sweep")

    # send_queue
    await db.send_queue.create_index(
        [("campaign_id", 1), ("email", 1), ("step_index", 1)],
        unique=True,
        name="uniq_send_triple",
    )
    await db.send_queue.create_index(
        [("status", 1), ("scheduled_for", 1)], name="hot_due_work"
    )
    await db.send_queue.create_index(
        [("tenant_user_id", 1), ("status", 1), ("scheduled_for", 1)],
        name="tenant_due_work",
    )
    await db.send_queue.create_index("lease_expires_at", name="lease_sweep")

    # campaign_leases: TTL on expires_at (Mongo auto-expires docs)
    await db.campaign_leases.create_index(
        "expires_at", expireAfterSeconds=0, name="ttl_expires"
    )

    # campaign_stats
    await db.campaign_stats.create_index(
        [("campaign_id", 1), ("date", 1)], unique=True, name="uniq_campaign_day"
    )
    await db.campaign_stats.create_index(
        [("tenant_user_id", 1), ("date", 1)], name="tenant_day_rollup"
    )

    # campaign_hourly_stats (per-hour throttle bucket)
    await db.campaign_hourly_stats.create_index(
        [("campaign_id", 1), ("date", 1), ("hour_utc", 1)],
        unique=True,
        name="uniq_campaign_hour",
    )
    await db.campaign_hourly_stats.create_index(
        [("tenant_user_id", 1), ("date", 1), ("hour_utc", 1)],
        name="tenant_hour_rollup",
    )

    # mcp_instance_registry: TTL so dead containers disappear after 10 minutes of no heartbeat
    await db.mcp_instance_registry.create_index(
        "last_heartbeat_at", expireAfterSeconds=600, name="ttl_heartbeat"
    )

    # mcp_hygiene_lease: TTL on expires_at (90s)
    await db.mcp_hygiene_lease.create_index(
        "expires_at", expireAfterSeconds=0, name="ttl_hygiene_lease"
    )

    # ingest_errors / audit_log: only by campaign+ts
    await db.ingest_errors.create_index([("campaign_id", 1), ("ts", -1)], name="by_campaign")
    await db.audit_log.create_index([("ts", -1)], name="recent")

    # token bucket
    await db.provider_rate_buckets.create_index(
        [("user_id", 1), ("provider", 1)], unique=True, name="uniq_bucket"
    )

    # tenant_cursors — single global doc used by send_queue.lease_batch()
    # to round-robin across tenants. One cursor shared by every container
    # in the fleet; no pool_id field.
    await db.tenant_cursors.create_index("_id", name="by_id")

    # tenant_stats_daily — plan-tier per-tenant cap, separate from the
    # per-campaign daily cap in campaign_stats.
    await db.tenant_stats_daily.create_index(
        [("tenant_user_id", 1), ("date", 1)], unique=True, name="uniq_tenant_day"
    )

    # Shard-key indexes on multi-tenant collections. Pre-declared now so
    # Phase 2 sharding needs zero backfill.
    await db.send_queue.create_index("shard_key", name="shard_key")
    await db.campaign_contacts.create_index("shard_key", name="shard_key")
    await db.refined_contacts.create_index("shard_key", name="shard_key")
    await db.campaign_stats.create_index("shard_key", name="shard_key")


async def connect_to_mongo() -> AsyncIOMotorDatabase:
    """Open Mongo using the runtime config injected by LeadPulse (idempotent)."""
    global _client, _db
    cfg = runtime_config.get()
    if _client is not None and _db is not None:
        return _db

    _client = AsyncIOMotorClient(cfg.mongodb_url, uuidRepresentation="standard")
    _db = _client[cfg.mongodb_db]

    for name, spec in _CAPPED_COLLECTIONS.items():
        await _ensure_capped(_db, name, spec)
    await _ensure_indexes(_db)
    log.info("mongo_connected", extra={"extra_payload": {"db": cfg.mongodb_db}})
    return _db


async def close_mongo_connection() -> None:
    global _client, _db
    if _client is not None:
        _client.close()
    _client = None
    _db = None


def get_db() -> AsyncIOMotorDatabase:
    if _db is None:
        raise RuntimeError(
            "MongoDB is not connected. The MCP must be bootstrapped by LeadPulse first."
        )
    return _db


async def ping() -> float:
    """Measure Mongo ping latency in ms. Used by runtime probe."""
    import time

    db = get_db()
    t0 = time.perf_counter()
    await db.command("ping")
    return (time.perf_counter() - t0) * 1000.0
