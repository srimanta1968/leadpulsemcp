"""Unit tests for task #8: tenant_shard_key helper."""
from __future__ import annotations

from app.db.mongodb import tenant_shard_key


def test_tenant_shard_key_is_deterministic() -> None:
    a = tenant_shard_key("tenant-42")
    b = tenant_shard_key("tenant-42")
    assert a == b


def test_tenant_shard_key_is_16_hex_chars() -> None:
    key = tenant_shard_key("some-tenant-uuid")
    assert len(key) == 16
    assert all(c in "0123456789abcdef" for c in key)


def test_tenant_shard_key_differs_across_tenants() -> None:
    assert tenant_shard_key("tenant-a") != tenant_shard_key("tenant-b")
