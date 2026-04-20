"""Admin endpoints the CRM hits for fleet control: scale-hint, live-stats.

Both require HMAC verification.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel

from app.agents.supervisor import supervisor
from app.api.deps import require_db, verify_crm_hmac
from app.core.logging import get_logger
from app.services import throttle_service

# @governance-tracked — API definitions added: POST /api/v1/admin/scale-hint, GET /api/v1/admin/live-stats/:campaign_id

log = get_logger(__name__)
router = APIRouter()


class _Envelope(BaseModel):
    success: bool = True
    data: dict


class ScaleHintBody(BaseModel):
    action: Literal["drain", "resume"]


@router.post("/scale-hint", response_model=_Envelope, dependencies=[Depends(verify_crm_hmac)])
async def scale_hint(body: ScaleHintBody) -> _Envelope:
    if body.action == "drain":
        # Disable all loops so this container stops pulling new work.
        # The supervisor state is checked by agents on each tick.
        for state in supervisor._loops.values():  # type: ignore[attr-defined]
            state.enabled = False
        log.warning("admin_drain_requested")
        return _Envelope(success=True, data={"action": "drain", "loops_disabled": True})

    supervisor.reset_quarantine()
    log.info("admin_resume_requested")
    return _Envelope(success=True, data={"action": "resume", "restored": True})


@router.get("/live-stats/{campaign_id}", response_model=_Envelope, dependencies=[Depends(verify_crm_hmac)])
async def live_stats(
    campaign_id: str, db: AsyncIOMotorDatabase = Depends(require_db)
) -> _Envelope:
    today = await throttle_service.today_rollup(db, campaign_id)
    pending = await db.send_queue.count_documents({"campaign_id": campaign_id, "status": "pending"})
    leased = await db.send_queue.count_documents({"campaign_id": campaign_id, "status": "leased"})
    sent = await db.send_queue.count_documents({"campaign_id": campaign_id, "status": "sent"})
    bounced = await db.send_queue.count_documents({"campaign_id": campaign_id, "status": "bounced"})
    return _Envelope(
        success=True,
        data={
            "campaign_id": campaign_id,
            "today": today,
            "queue": {"pending": pending, "leased": leased, "sent": sent, "bounced": bounced},
        },
    )
