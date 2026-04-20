"""Heartbeat loop + initial CRM registration + SIGTERM drain handler.

Runs every 30s. Posts agents / cpu / mem / campaigns_in_flight / mongo_lag_ms
and mirrors the payload into ``mcp_instance_registry`` in Mongo.
"""
from __future__ import annotations

import asyncio
import os
import signal
from datetime import datetime, timezone
from typing import Any

from app.agents.supervisor import supervisor
from app.core.logging import get_logger
from app.core.runtime_config import runtime_config
from app.db.mongodb import get_db
from app.services import leadpulse_client as lpc_mod
from app.services.runtime_probe import runtime_probe

log = get_logger(__name__)

_HEARTBEAT_INTERVAL_SECONDS = 30
_DRAIN_GRACE_SECONDS = 60

# Monotonic start time used to surface uptime in the heartbeat payload.
import time as _time

_STARTED_AT_MONO = _time.monotonic()


def _uptime_seconds() -> int:
    return int(_time.monotonic() - _STARTED_AT_MONO)


def _allocation_payload() -> dict[str, Any]:
    """Shape the container's agent-allocation for register + heartbeat
    bodies. Empty dict when CPU_VCPU / RAM_MB weren't supplied (legacy
    single-tenant dev) — the CRM Fleet Dashboard then renders the row
    without the capacity column.
    """
    cfg = runtime_config.get()
    if cfg.cpu_vcpu <= 0 or cfg.ram_mb <= 0:
        return {}
    from app.core.allocation import compute_allocation

    alloc = compute_allocation(cfg.cpu_vcpu, cfg.ram_mb)
    return {
        "cpu_vcpu": cfg.cpu_vcpu,
        "ram_mb": cfg.ram_mb,
        "senders": alloc.senders,
        "extraction": alloc.extraction,
        "hygiene_eligible": alloc.hygiene_eligible,
        "daily_capacity": alloc.daily_capacity,
    }


async def register_once() -> None:
    """Called at startup after bootstrap. Receives per-instance HMAC secret."""
    try:
        resp = await lpc_mod.leadpulse_client.register(
            allocation=_allocation_payload() or None,
        )
        has_secret = runtime_config.get().hmac_secret is not None
        log.info(
            "mcp_registered",
            extra={
                "extra_payload": {
                    "instance_id": runtime_config.get().instance_id,
                    "ok": bool(resp.get("success", True)) if isinstance(resp, dict) else True,
                    "has_hmac_secret": has_secret,
                }
            },
        )
        if not has_secret:
            log.error(
                "mcp_registered_but_no_secret",
                extra={"extra_payload": {
                    "hint": "Subsequent CRM calls will 401. Check /register response shape."
                }},
            )
    except Exception as exc:  # noqa: BLE001
        log.error(
            "mcp_register_failed",
            extra={"extra_payload": {"err": str(exc)[:300]}},
        )


async def run() -> None:
    while True:
        try:
            await _send_heartbeat()
        except Exception:  # noqa: BLE001
            log.exception("heartbeat_failed")
        await asyncio.sleep(_HEARTBEAT_INTERVAL_SECONDS)


async def _send_heartbeat(status_override: str | None = None) -> None:
    db = get_db()
    cfg = runtime_config.get()
    snap = await runtime_probe.measure()
    sup_status = supervisor.status()

    status = status_override
    if status is None:
        if sup_status.get("quarantined"):
            status = "quarantine"
        elif not snap.healthy:
            status = "unhealthy"
        elif snap.degraded:
            status = "degraded"
        else:
            status = "active"

    # Agents summary: loops registered in the supervisor (extraction, sender_x, hygiene)
    agents = [
        {
            "agent_uid": name,
            "role": name.split("_")[0],
            "status": "idle" if loop["enabled"] else "disabled",
            "restarts": loop["restarts"],
        }
        for name, loop in sup_status["loops"].items()
    ]

    campaigns_in_flight = await _current_campaigns(db)

    payload = {
        "instance_id": cfg.instance_id,
        "region": os.environ.get("AWS_REGION", "us-east-1"),
        "status": status,
        "agent_slots_total": cfg.sender_agents_per_container + 2,
        "agent_slots_active": sum(1 for a in agents if a["status"] != "disabled"),
        "agents": agents,
        "cpu_pct": None,
        "mem_pct": (snap.rss_mb / max(1, int(os.environ.get("MEM_LIMIT_MB", "1024")))) * 100.0,
        "rss_mb": snap.rss_mb,
        "event_loop_lag_ms": snap.event_loop_lag_ms,
        "mongo_lag_ms": snap.mongo_latency_ms,
        "campaigns_in_flight": campaigns_in_flight,
        "health_messages": snap.messages,
        "uptime_seconds": _uptime_seconds(),
    }
    allocation = _allocation_payload()
    if allocation:
        payload["allocation"] = allocation

    await db.mcp_instance_registry.update_one(
        {"_id": cfg.instance_id},
        {
            "$set": {
                "started_at": {"$ifNull": ["$started_at", datetime.now(timezone.utc)]}
                if False
                else datetime.now(timezone.utc),
                "last_heartbeat_at": datetime.now(timezone.utc),
                "agent_slots_total": payload["agent_slots_total"],
                "agent_slots_active": payload["agent_slots_active"],
                "current_campaigns": campaigns_in_flight,
                "mem_pct": payload["mem_pct"],
                "status": status,
            }
        },
        upsert=True,
    )

    try:
        await lpc_mod.leadpulse_client.heartbeat(payload)
    except Exception:  # noqa: BLE001
        log.exception("heartbeat_post_failed")


async def _current_campaigns(db) -> list[str]:
    pipeline = [
        {"$match": {"status": {"$in": ["pending", "leased"]}}},
        {"$group": {"_id": "$campaign_id"}},
        {"$limit": 100},
    ]
    cursor = db.send_queue.aggregate(pipeline)
    return [doc["_id"] async for doc in cursor]


def install_sigterm_handler(stop_event: asyncio.Event) -> None:
    """Register SIGTERM to trigger graceful drain."""
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(_drain(stop_event, s)))
        except (NotImplementedError, RuntimeError):
            # Windows / non-unix: fall back to signal.signal (best-effort).
            signal.signal(sig, lambda *_: stop_event.set())


async def _drain(stop_event: asyncio.Event, sig: signal.Signals) -> None:
    log.warning("sigterm_received", extra={"extra_payload": {"sig": sig.name}})
    stop_event.set()
    try:
        await _send_heartbeat(status_override="draining")
    except Exception:  # noqa: BLE001
        log.exception("drain_heartbeat_failed")
    try:
        await supervisor.stop_all(timeout=_DRAIN_GRACE_SECONDS)
    except Exception:  # noqa: BLE001
        log.exception("drain_supervisor_failed")
