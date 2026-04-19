from __future__ import annotations

import asyncio
import base64
import json

import pytest

from app.core.runtime_config import RuntimeConfig, runtime_config
from app.services.leadpulse_client import _jwt_exp_seconds_until_expiry


def _make_jwt(exp_epoch: int) -> str:
    header = base64.urlsafe_b64encode(json.dumps({"alg": "HS256"}).encode()).rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(json.dumps({"exp": exp_epoch}).encode()).rstrip(b"=").decode()
    return f"{header}.{payload}.sig"


def test_jwt_exp_parsing() -> None:
    future = _make_jwt(exp_epoch=2_000_000_000)
    remaining = _jwt_exp_seconds_until_expiry(future)
    assert remaining is not None and remaining > 0


def test_jwt_parsing_returns_none_for_opaque_token() -> None:
    assert _jwt_exp_seconds_until_expiry("opaque") is None


@pytest.mark.asyncio
async def test_rotate_token_updates_config() -> None:
    cfg = RuntimeConfig(
        mongodb_url="mongodb://x",
        mongodb_db="db",
        leadpulse_url="https://crm",
        leadpulse_token="original",
        instance_id="inst-1",
    )
    await runtime_config.set(cfg)
    await runtime_config.rotate_token("fresh")
    assert runtime_config.get().leadpulse_token == "fresh"
