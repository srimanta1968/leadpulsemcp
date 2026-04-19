"""Inbound CRM -> MCP endpoints for contact lookup, conversion, unsubscribe.

All calls are HMAC-verified via ``verify_crm_hmac`` before hitting the handler.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, EmailStr

from app.agents import hygiene
from app.api.deps import require_db, verify_crm_hmac

router = APIRouter()


class _Envelope(BaseModel):
    success: bool = True
    data: dict


class MarkConvertedBody(BaseModel):
    campaignId: str
    email: EmailStr
    leadId: str


class MarkUnsubscribedBody(BaseModel):
    email: EmailStr
    campaignId: str | None = None
    reason: str | None = None


@router.get("/lookup", response_model=_Envelope, dependencies=[Depends(verify_crm_hmac)])
async def lookup(
    campaignId: str = Query(min_length=1),
    email: EmailStr = Query(...),
    db: AsyncIOMotorDatabase = Depends(require_db),
) -> _Envelope:
    email_normalized = str(email).lower().strip()
    contact = await db.campaign_contacts.find_one(
        {"campaign_id": campaignId, "email": email_normalized}
    )
    refined = await db.refined_contacts.find_one({"email": email_normalized}, {"primary": 1})
    if contact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contact not found")
    merged = _public_contact(contact, refined)
    return _Envelope(success=True, data={"contact": merged})


@router.post("/mark-converted", response_model=_Envelope, dependencies=[Depends(verify_crm_hmac)])
async def mark_converted(
    body: MarkConvertedBody, db: AsyncIOMotorDatabase = Depends(require_db)
) -> _Envelope:
    email = str(body.email).lower().strip()
    now = datetime.now(timezone.utc)
    upd = await db.campaign_contacts.update_one(
        {"campaign_id": body.campaignId, "email": email},
        {"$set": {"status": "converted", "converted_lead_id": body.leadId, "converted_at": now}},
    )
    await db.send_queue.update_many(
        {"campaign_id": body.campaignId, "email": email, "status": "pending"},
        {"$set": {"status": "skipped_converted", "updated_at": now}},
    )
    await db.audit_log.insert_one(
        {"event": "mark_converted", "campaign_id": body.campaignId, "email": email,
         "lead_id": body.leadId, "ts": now}
    )
    if upd.modified_count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contact not found")
    return _Envelope(success=True, data={"converted": True})


@router.post("/mark-unsubscribed", response_model=_Envelope, dependencies=[Depends(verify_crm_hmac)])
async def mark_unsubscribed(
    body: MarkUnsubscribedBody, db: AsyncIOMotorDatabase = Depends(require_db)
) -> _Envelope:
    stats = await hygiene.handle_unsubscribe(
        db, str(body.email), body.campaignId, body.reason
    )
    return _Envelope(
        success=True,
        data={"email": str(body.email).lower(), "campaignId": body.campaignId, **stats},
    )


def _public_contact(contact: dict[str, Any], refined: dict[str, Any] | None) -> dict[str, Any]:
    primary = (refined or {}).get("primary") or {}
    return {
        "id": str(contact["_id"]),
        "campaign_id": contact["campaign_id"],
        "tenant_user_id": contact.get("tenant_user_id"),
        "email": contact["email"],
        "first_name": contact.get("first_name") or primary.get("first_name", ""),
        "last_name": contact.get("last_name") or primary.get("last_name", ""),
        "phone": contact.get("phone") or primary.get("phone", ""),
        "company": contact.get("company") or primary.get("company", ""),
        "company_url": contact.get("company_url") or primary.get("company_url", ""),
        "job_title": contact.get("job_title") or primary.get("job_title", ""),
        "custom_fields": contact.get("custom_fields", {}),
        "status": contact.get("status", "active"),
    }
