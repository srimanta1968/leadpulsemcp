"""In-memory cache of plan-tier per-tenant quotas, refreshed from the CRM
every 15 minutes.

Data shape (from GET /api/mcp/tenant-quotas — CRM TK-2655):
    [{ "tenant_user_id": "<uuid>",
       "daily_cap": 5000,
       "per_hour_cap": 500,
       "plan_tier": "growth" }]

Consumers: sender._process_one reads ``get(tenant_user_id)`` and passes
``daily_cap`` to throttle_service.try_consume_tenant_daily_cap before
consuming a send slot.

Graceful degradation: until the CRM endpoint ships, ``get()`` returns an
empty dict and callers treat that as no plan-tier cap (the per-campaign
daily_cap + throttle_per_hour from config_snapshot still apply).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from app.core.logging import get_logger
from app.services import leadpulse_client as lpc_mod

log = get_logger(__name__)

REFRESH_INTERVAL_SECONDS = 15 * 60


@dataclass(frozen=True)
class TenantQuota:
    daily_cap: int
    per_hour_cap: int
    plan_tier: str


class _TenantQuotaStore:
    def __init__(self) -> None:
        self._quotas: dict[str, TenantQuota] = {}
        self._lock = asyncio.Lock()

    def get(self, tenant_user_id: str) -> TenantQuota | None:
        return self._quotas.get(tenant_user_id)

    def snapshot(self) -> dict[str, TenantQuota]:
        return dict(self._quotas)

    async def refresh_once(self) -> int:
        """Pull the latest quotas from CRM. Returns the row count on
        success, -1 when the endpoint isn't available yet.
        """
        resp = await lpc_mod.leadpulse_client.get_tenant_quotas()
        if resp is None:
            return -1
        data = resp.get("data") if isinstance(resp, dict) else None
        items: list[dict[str, Any]] = []
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict) and isinstance(data.get("quotas"), list):
            items = data["quotas"]
        parsed: dict[str, TenantQuota] = {}
        for row in items:
            tid = str(row.get("tenant_user_id") or "").strip()
            if not tid:
                continue
            parsed[tid] = TenantQuota(
                daily_cap=int(row.get("daily_cap", 0) or 0),
                per_hour_cap=int(row.get("per_hour_cap", 0) or 0),
                plan_tier=str(row.get("plan_tier", "") or ""),
            )
        async with self._lock:
            self._quotas = parsed
        return len(parsed)


tenant_quotas = _TenantQuotaStore()


async def run() -> None:
    """Background loop: refresh quotas every 15 minutes. Idempotent.

    Runs under the supervisor so a crash restarts the loop with backoff.
    """
    while True:
        try:
            count = await tenant_quotas.refresh_once()
            if count >= 0:
                log.info(
                    "tenant_quotas_refreshed",
                    extra={"extra_payload": {"count": count}},
                )
            else:
                # Endpoint not yet implemented on CRM side — don't spam logs.
                log.debug("tenant_quotas_endpoint_unavailable")
        except Exception:  # noqa: BLE001
            log.exception("tenant_quotas_refresh_failed")
        await asyncio.sleep(REFRESH_INTERVAL_SECONDS)
