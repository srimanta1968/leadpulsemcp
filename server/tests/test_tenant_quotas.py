"""Unit tests for #49: tenant_quotas store refresh + sender consumption."""
from __future__ import annotations

import pytest

from app.services.tenant_quotas import _TenantQuotaStore, TenantQuota


class _FakeClient:
    def __init__(self, response) -> None:
        self.response = response
        self.calls = 0

    async def get_tenant_quotas(self):
        self.calls += 1
        return self.response


@pytest.mark.asyncio
async def test_refresh_once_returns_minus_one_on_missing_endpoint(monkeypatch) -> None:
    from app.services import leadpulse_client as lpc_mod

    fake = _FakeClient(response=None)
    monkeypatch.setattr(lpc_mod, "leadpulse_client", fake)

    store = _TenantQuotaStore()
    result = await store.refresh_once()
    assert result == -1
    assert store.snapshot() == {}


@pytest.mark.asyncio
async def test_refresh_once_parses_array_shape(monkeypatch) -> None:
    from app.services import leadpulse_client as lpc_mod

    fake = _FakeClient(response={
        "data": [
            {"tenant_user_id": "t1", "daily_cap": 5000, "per_hour_cap": 500, "plan_tier": "growth"},
            {"tenant_user_id": "t2", "daily_cap": 200, "per_hour_cap": 50, "plan_tier": "starter"},
        ]
    })
    monkeypatch.setattr(lpc_mod, "leadpulse_client", fake)

    store = _TenantQuotaStore()
    assert await store.refresh_once() == 2
    assert store.get("t1") == TenantQuota(daily_cap=5000, per_hour_cap=500, plan_tier="growth")
    assert store.get("t2").plan_tier == "starter"
    assert store.get("missing") is None


@pytest.mark.asyncio
async def test_refresh_once_parses_envelope_shape(monkeypatch) -> None:
    from app.services import leadpulse_client as lpc_mod

    fake = _FakeClient(response={
        "data": {"quotas": [
            {"tenant_user_id": "t1", "daily_cap": 10, "per_hour_cap": 5, "plan_tier": "pro"},
        ]}
    })
    monkeypatch.setattr(lpc_mod, "leadpulse_client", fake)

    store = _TenantQuotaStore()
    assert await store.refresh_once() == 1
    assert store.get("t1").plan_tier == "pro"


@pytest.mark.asyncio
async def test_refresh_once_ignores_rows_without_tenant_id(monkeypatch) -> None:
    from app.services import leadpulse_client as lpc_mod

    fake = _FakeClient(response={
        "data": [
            {"daily_cap": 5000, "per_hour_cap": 500},  # no tenant_user_id
            {"tenant_user_id": "t1", "daily_cap": 100, "per_hour_cap": 10, "plan_tier": "starter"},
        ]
    })
    monkeypatch.setattr(lpc_mod, "leadpulse_client", fake)

    store = _TenantQuotaStore()
    assert await store.refresh_once() == 1
    assert store.snapshot().keys() == {"t1"}
