"""Runtime configuration supplied by LeadPulse CRM at container start.

The MCP container boots in an **unconfigured** state. LeadPulse CRM calls
``POST /api/v1/bootstrap`` to inject:

- ``mongodb_url``   — Mongo connection string for this MCP instance
- ``mongodb_db``    — target database name
- ``leadpulse_url`` — base URL of the CRM API (for callbacks)
- ``leadpulse_token`` — bearer token used for MCP->CRM authenticated calls.
                        The MCP auto-refreshes this via /api/mcp/refresh-token
                        when it detects a 401 or a JWT-exp nearing expiry.
- ``instance_id``   — ECS task id assigned by the CRM (also used for heartbeat)
- ``hmac_secret``   — HMAC-SHA256 secret issued at /api/mcp/register. Used for
                      both outbound and inbound signature verification.

Values live in memory only; never persisted.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from typing import Any


@dataclass(frozen=True)
class RuntimeConfig:
    mongodb_url: str
    mongodb_db: str
    leadpulse_url: str
    leadpulse_token: str
    instance_id: str
    sender_agents_per_container: int = 4
    hmac_secret: str | None = None
    hmac_secret_previous: str | None = None  # during rotation
    mcp_bootstrap_key: str | None = None  # used only for the one-time POST /api/mcp/register

    def redacted(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "leadpulse_url": self.leadpulse_url,
            "mongodb_db": self.mongodb_db,
            "sender_agents_per_container": self.sender_agents_per_container,
            "configured": True,
            "has_hmac_secret": self.hmac_secret is not None,
        }


class RuntimeConfigStore:
    def __init__(self) -> None:
        self._cfg: RuntimeConfig | None = None
        self._lock = asyncio.Lock()
        self._ready = asyncio.Event()

    async def set(self, cfg: RuntimeConfig) -> None:
        async with self._lock:
            self._cfg = cfg
            self._ready.set()

    async def rotate_token(self, new_token: str) -> None:
        async with self._lock:
            if self._cfg is None:
                raise RuntimeError("Cannot rotate token before bootstrap")
            self._cfg = replace(self._cfg, leadpulse_token=new_token)

    async def rotate_hmac(self, new_secret: str) -> None:
        async with self._lock:
            if self._cfg is None:
                raise RuntimeError("Cannot rotate HMAC before bootstrap")
            self._cfg = replace(
                self._cfg,
                hmac_secret=new_secret,
                hmac_secret_previous=self._cfg.hmac_secret,
            )

    def get(self) -> RuntimeConfig:
        if self._cfg is None:
            raise RuntimeError("MCP is not configured yet. Call POST /api/v1/bootstrap first.")
        return self._cfg

    def is_configured(self) -> bool:
        return self._cfg is not None

    async def wait_until_ready(self) -> RuntimeConfig:
        await self._ready.wait()
        assert self._cfg is not None
        return self._cfg


runtime_config = RuntimeConfigStore()
