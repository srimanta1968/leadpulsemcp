"""Watchdog-aware health endpoint. Returns 503 when unhealthy so ECS replaces the task."""
from __future__ import annotations

from fastapi import APIRouter, Response, status

from app.agents.supervisor import supervisor
from app.core.runtime_config import runtime_config
from app.services.runtime_probe import runtime_probe

router = APIRouter()


@router.get("/health")
async def health(response: Response) -> dict:
    configured = runtime_config.is_configured()
    snap = runtime_probe.last
    healthy = True
    if configured and snap is not None:
        healthy = snap.healthy and not supervisor.quarantined()

    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {
        "status": "ok" if healthy else "unhealthy",
        "configured": configured,
        "quarantined": supervisor.quarantined(),
        "probe": None
        if snap is None
        else {
            "rss_mb": snap.rss_mb,
            "event_loop_lag_ms": snap.event_loop_lag_ms,
            "mongo_latency_ms": snap.mongo_latency_ms,
            "messages": snap.messages,
        },
    }
