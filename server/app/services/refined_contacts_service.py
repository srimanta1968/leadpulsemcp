"""Operations on ``refined_contacts`` and ``campaign_contacts``.

Implements the cross-tenant merge policy from the design doc section 6.1 via
Mongo update pipelines (so merges are atomic and server-side).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import UpdateOne


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def upsert_campaign_contact(
    db: AsyncIOMotorDatabase,
    campaign_id: str,
    tenant_user_id: str,
    row: dict[str, Any],
    source_file_id: str | None,
    source_row_number: int,
) -> str:
    email = str(row["email"]).lower().strip()
    now = _now()
    doc = {
        "_id": ObjectId(),
        "campaign_id": campaign_id,
        "tenant_user_id": tenant_user_id,
        "email": email,
        "first_name": row.get("first_name", ""),
        "last_name": row.get("last_name", ""),
        "phone": row.get("phone", ""),
        "company": row.get("company", ""),
        "company_url": row.get("company_url", ""),
        "job_title": row.get("job_title", ""),
        "custom_fields": row.get("custom_fields", {}),
        "source_file_id": source_file_id,
        "source_row_number": source_row_number,
        "imported_at": now,
        "status": "active",
        "exclusion_reason": None,
    }
    await db.campaign_contacts.update_one(
        {"campaign_id": campaign_id, "email": email},
        {"$setOnInsert": doc},
        upsert=True,
    )
    stored = await db.campaign_contacts.find_one(
        {"campaign_id": campaign_id, "email": email}, {"_id": 1}
    )
    return str(stored["_id"]) if stored else ""


async def upsert_refined_contact(
    db: AsyncIOMotorDatabase,
    campaign_id: str,
    tenant_user_id: str,
    row: dict[str, Any],
) -> None:
    """Merge-policy upsert. Uses an aggregation-pipeline update so logic runs
    server-side atomically. Implements section 6.1 field precedence.
    """
    email = str(row["email"]).lower().strip()
    now = _now()
    incoming = {
        "first_name": row.get("first_name") or None,
        "last_name": row.get("last_name") or None,
        "phone": row.get("phone") or None,
        "company": row.get("company") or None,
        "company_url": row.get("company_url") or None,
        "company_domain": _extract_domain(row.get("company_url")) or None,
        "job_title": row.get("job_title") or None,
    }

    pipeline = [
        {
            "$set": {
                "email": email,
                "first_seen_at": {"$ifNull": ["$first_seen_at", now]},
                "last_seen_at": now,
                "bounce_count": {"$ifNull": ["$bounce_count", 0]},
                "soft_bounce_count_30d": {"$ifNull": ["$soft_bounce_count_30d", 0]},
                "hard_bounce": {"$ifNull": ["$hard_bounce", False]},
                "hard_bounced_at": {"$ifNull": ["$hard_bounced_at", None]},
                "unsubscribed_global": {"$ifNull": ["$unsubscribed_global", False]},
                "unsubscribed_at": {"$ifNull": ["$unsubscribed_at", None]},
                "deliverability_score": {"$ifNull": ["$deliverability_score", 100]},
                "verification_status": {"$ifNull": ["$verification_status", "unverified"]},
                "last_verified_at": {"$ifNull": ["$last_verified_at", None]},
                # primary.* merged with precedence rules
                "primary.first_name": _prefer_longer("$primary.first_name", incoming["first_name"]),
                "primary.last_name": _prefer_longer("$primary.last_name", incoming["last_name"]),
                "primary.phone": _prefer_newest("$primary.phone", incoming["phone"]),
                "primary.company": _prefer_newest("$primary.company", incoming["company"]),
                "primary.company_url": _prefer_newest("$primary.company_url", incoming["company_url"]),
                "primary.company_domain": _prefer_newest("$primary.company_domain", incoming["company_domain"]),
                "primary.job_title": _prefer_newest("$primary.job_title", incoming["job_title"]),
                "seen_in_campaigns": {
                    "$setUnion": [{"$ifNull": ["$seen_in_campaigns", []]}, [campaign_id]]
                },
                "seen_in_tenants": {
                    "$setUnion": [{"$ifNull": ["$seen_in_tenants", []]}, [tenant_user_id]]
                },
            }
        }
    ]
    await db.refined_contacts.update_one(
        {"email": email}, pipeline, upsert=True
    )


def _prefer_longer(existing_path: str, incoming: str | None) -> dict[str, Any]:
    if incoming is None:
        return {"$ifNull": [existing_path, None]}
    return {
        "$cond": [
            {
                "$gt": [
                    {"$strLenCP": {"$ifNull": [incoming, ""]}},
                    {"$strLenCP": {"$ifNull": [existing_path, ""]}},
                ]
            },
            incoming,
            {"$ifNull": [existing_path, incoming]},
        ]
    }


def _prefer_newest(existing_path: str, incoming: str | None) -> Any:
    # Newest wins when incoming is non-null; otherwise keep existing.
    if incoming is None:
        return {"$ifNull": [existing_path, None]}
    return incoming


def _extract_domain(url: str | None) -> str | None:
    if not url:
        return None
    import re

    m = re.search(r"https?://([^/]+)", url)
    if m:
        return m.group(1).lower()
    if "." in url and "/" not in url:
        return url.lower()
    return None


async def bulk_upsert(
    db: AsyncIOMotorDatabase,
    campaign_id: str,
    tenant_user_id: str,
    rows: list[dict[str, Any]],
    source_file_id: str | None,
) -> dict[str, int]:
    """Used by the extraction agent for batch parsed rows. Falls back to per-row
    refined merge (pipeline updates aren't trivially bulk_write-compatible
    across all Mongo versions); campaign_contacts is bulk-written.
    """
    if not rows:
        return {"campaign_inserted": 0, "campaign_matched": 0, "refined_touched": 0, "errors": 0}

    campaign_ops: list[UpdateOne] = []
    errors = 0
    for idx, row in enumerate(rows):
        try:
            email = str(row["email"]).lower().strip()
            doc = {
                "_id": ObjectId(),
                "campaign_id": campaign_id,
                "tenant_user_id": tenant_user_id,
                "email": email,
                "first_name": row.get("first_name", ""),
                "last_name": row.get("last_name", ""),
                "phone": row.get("phone", ""),
                "company": row.get("company", ""),
                "company_url": row.get("company_url", ""),
                "job_title": row.get("job_title", ""),
                "custom_fields": row.get("custom_fields", {}),
                "source_file_id": source_file_id,
                "source_row_number": idx + 1,
                "imported_at": _now(),
                "status": "active",
                "exclusion_reason": None,
            }
            campaign_ops.append(
                UpdateOne(
                    {"campaign_id": campaign_id, "email": email},
                    {"$setOnInsert": doc},
                    upsert=True,
                )
            )
        except Exception:  # noqa: BLE001
            errors += 1

    cc_inserted = 0
    cc_matched = 0
    if campaign_ops:
        result = await db.campaign_contacts.bulk_write(campaign_ops, ordered=False)
        cc_inserted = result.upserted_count
        cc_matched = result.matched_count

    refined_touched = 0
    for row in rows:
        try:
            await upsert_refined_contact(db, campaign_id, tenant_user_id, row)
            refined_touched += 1
        except Exception:  # noqa: BLE001
            errors += 1

    return {
        "campaign_inserted": cc_inserted,
        "campaign_matched": cc_matched,
        "refined_touched": refined_touched,
        "errors": errors,
    }


async def mark_bounce(
    db: AsyncIOMotorDatabase, email: str, bounce_type: str, decay_points: int
) -> None:
    """Called by hygiene agent per bounce event."""
    email = email.lower().strip()
    now = _now()
    update: dict[str, Any] = {"$inc": {"bounce_count": 1}}
    if bounce_type == "hard":
        update["$set"] = {
            "hard_bounce": True,
            "hard_bounced_at": now,
            "deliverability_score": 0,
        }
    else:
        update["$inc"]["soft_bounce_count_30d"] = 1
        update["$max"] = {"deliverability_score": 0}
        update["$set"] = {}
        update["$set"]["last_soft_bounce_at"] = now
    await db.refined_contacts.update_one({"email": email}, update, upsert=True)


async def mark_unsubscribed_global(db: AsyncIOMotorDatabase, email: str) -> None:
    email = email.lower().strip()
    now = _now()
    await db.refined_contacts.update_one(
        {"email": email},
        {"$set": {"unsubscribed_global": True, "unsubscribed_at": now}},
        upsert=True,
    )


async def cascade_unsubscribe_to_campaigns(
    db: AsyncIOMotorDatabase, email: str, campaign_id: str | None
) -> dict[str, int]:
    email = email.lower().strip()
    cc_filter: dict[str, Any] = {"email": email}
    sq_filter: dict[str, Any] = {"email": email, "status": "pending"}
    if campaign_id:
        cc_filter["campaign_id"] = campaign_id
        sq_filter["campaign_id"] = campaign_id
    cc_res = await db.campaign_contacts.update_many(
        cc_filter,
        {"$set": {"status": "excluded", "exclusion_reason": "unsubscribed"}},
    )
    sq_res = await db.send_queue.update_many(
        sq_filter, {"$set": {"status": "skipped_unsubscribed"}}
    )
    return {"contacts_excluded": cc_res.modified_count, "sends_skipped": sq_res.modified_count}


async def is_blocked(db: AsyncIOMotorDatabase, email: str) -> bool:
    """Check hygiene state used by the sender agent's pre-send gate."""
    doc = await db.refined_contacts.find_one(
        {"email": email.lower().strip()}, {"unsubscribed_global": 1, "hard_bounce": 1}
    )
    if doc is None:
        return False
    return bool(doc.get("unsubscribed_global")) or bool(doc.get("hard_bounce"))
