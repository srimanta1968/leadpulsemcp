"""Watchdog-aware health endpoint. Returns 503 when unhealthy so ECS replaces the task."""
from __future__ import annotations

from fastapi import APIRouter, Response, status

from app.agents import crm_connectivity
from app.agents.supervisor import supervisor
from app.core.runtime_config import runtime_config
from app.services.runtime_probe import runtime_probe

router = APIRouter()


@router.get("/health")
async def health(response: Response) -> dict:
    configured = runtime_config.is_configured()
    snap = runtime_probe.last
    # crm_connectivity flips to ISOLATED when heartbeats have been failing
    # for 10+ minutes — at that point the heartbeat loop has already
    # called supervisor.stop_all() and the container is decommissioned.
    # Report 503 so ECS's container health check fails → ECS stops the
    # task → the service launches a fresh replacement. Without this, the
    # container would keep serving /health 200 forever even though every
    # agent loop is cancelled.
    isolated = crm_connectivity.should_self_terminate()
    healthy = not isolated
    if configured and snap is not None:
        healthy = healthy and snap.healthy and not supervisor.quarantined()

    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {
        "status": "ok" if healthy else ("isolated" if isolated else "unhealthy"),
        "configured": configured,
        "quarantined": supervisor.quarantined(),
        "crm_connectivity": crm_connectivity.snapshot(),
        "probe": None
        if snap is None
        else {
            "rss_mb": snap.rss_mb,
            "event_loop_lag_ms": snap.event_loop_lag_ms,
            "mongo_latency_ms": snap.mongo_latency_ms,
            "messages": snap.messages,
        },
    }
