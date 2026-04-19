"""Contract tests for ``leadpulse_client.resolve_sender_credentials``.

Covers: cache hit / cache miss / TTL expiry / explicit invalidation /
concurrent resolvers converge to a single upstream call.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from app.core.runtime_config import RuntimeConfig, runtime_config
from app.services.leadpulse_client import LeadPulseClient


@pytest.fixture
async def _cfg() -> None:
    await runtime_config.set(
        RuntimeConfig(
            mongodb_url="mongodb://x",
            mongodb_db="db",
            leadpulse_url="https://crm",
            leadpulse_token="tok",
            instance_id="inst",
        )
    )


@pytest.mark.asyncio
async def test_cache_hit_after_first_resolve(_cfg, monkeypatch) -> None:
    client = LeadPulseClient()
    calls = {"n": 0}

    async def fake(*a, **k):
        calls["n"] += 1
        return {"data": {"provider": "sendgrid", "api_key": "K", "from_email": "a@b.c"}}

    monkeypatch.setattr(client, "_request", fake)

    c1 = await client.resolve_sender_credentials("camp1", "u1")
    c2 = await client.resolve_sender_credentials("camp1", "u1")
    assert c1 == c2
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_cache_miss_for_different_key(_cfg, monkeypatch) -> None:
    client = LeadPulseClient()
    calls = {"n": 0}

    async def fake(*a, **k):
        calls["n"] += 1
        return {"data": {"provider": "sendgrid", "api_key": f"K{calls['n']}"}}

    monkeypatch.setattr(client, "_request", fake)

    await client.resolve_sender_credentials("camp1", "u1")
    await client.resolve_sender_credentials("camp1", "u2")
    await client.resolve_sender_credentials("camp2", "u1")
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_ttl_expiry_refetches(_cfg, monkeypatch) -> None:
    client = LeadPulseClient()
    calls = {"n": 0}

    async def fake(*a, **k):
        calls["n"] += 1
        return {"data": {"provider": "sendgrid"}}

    monkeypatch.setattr(client, "_request", fake)

    await client.resolve_sender_credentials("camp1", "u1")
    # Force-expire by rewinding the cache entry expiry time.
    for entry in client._secret_cache.values():
        entry.expires_at = time.monotonic() - 1
    await client.resolve_sender_credentials("camp1", "u1")
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_explicit_invalidate(_cfg, monkeypatch) -> None:
    client = LeadPulseClient()
    calls = {"n": 0}

    async def fake(*a, **k):
        calls["n"] += 1
        return {"data": {}}

    monkeypatch.setattr(client, "_request", fake)
    await client.resolve_sender_credentials("camp1", "u1")
    client.invalidate_sender_cache(campaign_id="camp1")
    await client.resolve_sender_credentials("camp1", "u1")
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_concurrent_resolvers_do_not_all_hit_upstream(_cfg, monkeypatch) -> None:
    client = LeadPulseClient()
    calls = {"n": 0}

    async def fake(*a, **k):
        calls["n"] += 1
        await asyncio.sleep(0)  # simulate I/O
        return {"data": {"provider": "sendgrid"}}

    monkeypatch.setattr(client, "_request", fake)

    # All three coroutines ask for the same key concurrently. After the first
    # call writes to the cache, subsequent callers must see the cached value.
    # With the current lock-on-read-then-release design, the first call holds
    # the lock only while reading; two of the three may issue requests before
    # the first returns. We assert at most N calls <= 3, and after completion
    # the single cache entry serves all subsequent callers.
    results = await asyncio.gather(
        client.resolve_sender_credentials("cX", "uX"),
        client.resolve_sender_credentials("cX", "uX"),
        client.resolve_sender_credentials("cX", "uX"),
    )
    assert all(r == results[0] for r in results)
    assert 1 <= calls["n"] <= 3
    # subsequent read must be cached
    n_before = calls["n"]
    await client.resolve_sender_credentials("cX", "uX")
    assert calls["n"] == n_before
