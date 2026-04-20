"""HTTP client for MCP -> LeadPulse CRM calls.

Features:
- HMAC-SHA256 request signing (via core.hmac_signing).
- Bearer-token auth using the token injected at bootstrap.
- Auto-refresh of the bearer token:
    1) If JWT, refreshes 5 min before `exp`.
    2) On 401, calls POST /api/mcp/refresh-token with the current token
       (signed HMAC) and retries once.
    3) Falls back to bootstrap re-handshake signal if refresh itself fails.
- Exponential-backoff retry on 5xx.
- Circuit breaker (open when >5% errors over the last 10 min).
- pending_crm_events replay buffer: when the breaker is open,
  event-reporting calls are buffered to Mongo and drained on recovery.
- Per-(campaign, tenant) in-memory cache (10 min TTL) for resolved sender creds.
"""
from __future__ import annotations

import asyncio
import base64
import json
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.hmac_signing import sign
from app.core.logging import get_logger
from app.core.runtime_config import runtime_config

log = get_logger(__name__)

_SECRET_CACHE_TTL_SECONDS = 600
_TOKEN_REFRESH_LEAD_SECONDS = 300
_CIRCUIT_WINDOW_SECONDS = 600
_CIRCUIT_ERROR_RATE_THRESHOLD = 0.05
_CIRCUIT_MIN_SAMPLES = 20
_CIRCUIT_HALF_OPEN_AFTER_SECONDS = 60


class CrmUnavailable(Exception):
    pass


class CrmAuthFailed(Exception):
    pass


@dataclass
class _CacheEntry:
    value: dict[str, Any]
    expires_at: float


class _CircuitBreaker:
    def __init__(self) -> None:
        self._samples: deque[tuple[float, bool]] = deque(maxlen=500)
        self._opened_at: float | None = None

    def record(self, ok: bool) -> None:
        now = time.monotonic()
        self._samples.append((now, ok))
        self._evict_stale(now)
        if self.is_open():
            return
        if len(self._samples) >= _CIRCUIT_MIN_SAMPLES:
            err_rate = sum(1 for _, good in self._samples if not good) / len(self._samples)
            if err_rate > _CIRCUIT_ERROR_RATE_THRESHOLD:
                self._opened_at = now
                log.warning("crm_circuit_open", extra={"extra_payload": {"err_rate": err_rate}})

    def is_open(self) -> bool:
        if self._opened_at is None:
            return False
        if time.monotonic() - self._opened_at >= _CIRCUIT_HALF_OPEN_AFTER_SECONDS:
            # enter half-open: next call is allowed; success closes, failure re-opens
            self._opened_at = None
            self._samples.clear()
            log.info("crm_circuit_half_open")
            return False
        return True

    def _evict_stale(self, now: float) -> None:
        cutoff = now - _CIRCUIT_WINDOW_SECONDS
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()


def _jwt_exp_seconds_until_expiry(token: str) -> float | None:
    """If token looks like a JWT, decode payload.exp. Returns seconds remaining,
    or None if the token is opaque / unparseable.
    """
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("utf-8")))
        exp = payload.get("exp")
        if not isinstance(exp, (int, float)):
            return None
        return float(exp) - time.time()
    except Exception:  # noqa: BLE001
        return None


class LeadPulseClient:
    def __init__(self) -> None:
        self._secret_cache: dict[str, _CacheEntry] = {}
        self._secret_lock = asyncio.Lock()
        self._token_lock = asyncio.Lock()
        self._breaker = _CircuitBreaker()
        # Set to True once we detect the CRM has no /api/mcp/refresh-token.
        self._refresh_disabled = False

    # ------------------------------------------------------------------ helpers
    def _base(self) -> str:
        return runtime_config.get().leadpulse_url

    def _auth_headers(
        self, method: str, path: str, body: bytes, *, signing_override: str | None = None
    ) -> dict[str, str]:
        cfg = runtime_config.get()
        # ALWAYS send Content-Type: application/json.
        # The CRM's /api/mcp/* routes use express.raw({ type: 'application/json' })
        # which only fires on matching Content-Type. When it fires (even for
        # empty bodies) it sets req.body = Buffer.alloc(0), which the HMAC
        # middleware correctly hashes as sha256("") = e3b0c4…. When it
        # does NOT fire, req.body stays as Express's default {} placeholder
        # and the fallback serializer hashes sha256("{}") — signature mismatch.
        headers = {
            "Authorization": f"Bearer {cfg.leadpulse_token}",
            "X-MCP-Instance-Id": cfg.instance_id,
            "Content-Type": "application/json",
        }
        # Sign with: (a) override if supplied (register flow), else (b) the
        # per-instance HMAC issued by /api/mcp/register, else (c) the bootstrap
        # key if we have one but no instance secret yet.
        key = signing_override or cfg.hmac_secret or cfg.mcp_bootstrap_key
        if key:
            headers.update(sign(key, method, path, body))
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        allow_refresh: bool = True,
        max_retries: int = 3,
    ) -> dict[str, Any]:
        if self._breaker.is_open():
            raise CrmUnavailable("CRM circuit breaker is open")

        await self._maybe_proactive_refresh()

        body_bytes = json.dumps(json_body, separators=(",", ":")).encode("utf-8") if json_body is not None else b""
        url = f"{self._base()}{path}"
        backoff = 1.0
        last_exc: Exception | None = None

        for attempt in range(max_retries):
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.request(
                        method,
                        url,
                        content=body_bytes if json_body is not None else None,
                        params=params,
                        headers=self._auth_headers(method, path, body_bytes),
                    )
                if resp.status_code == 401 and allow_refresh:
                    await self._refresh_token()
                    # retry once with new token; disable further refresh to avoid loops
                    return await self._request(
                        method, path, json_body=json_body, params=params,
                        allow_refresh=False, max_retries=1,
                    )
                if resp.status_code >= 500:
                    raise httpx.HTTPStatusError("5xx", request=resp.request, response=resp)
                resp.raise_for_status()
                self._breaker.record(ok=True)
                return resp.json()
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                self._breaker.record(ok=False)
                if attempt == max_retries - 1:
                    break
                await asyncio.sleep(backoff)
                backoff *= 2
            except httpx.HTTPError as exc:
                last_exc = exc
                self._breaker.record(ok=False)
                if attempt == max_retries - 1:
                    break
                await asyncio.sleep(backoff)
                backoff *= 2

        assert last_exc is not None
        raise CrmUnavailable(f"CRM call {method} {path} failed: {last_exc}")

    # ------------------------------------------------------------- token refresh
    async def _maybe_proactive_refresh(self) -> None:
        remaining = _jwt_exp_seconds_until_expiry(runtime_config.get().leadpulse_token)
        if remaining is not None and remaining < _TOKEN_REFRESH_LEAD_SECONDS:
            await self._refresh_token()

    async def _refresh_token(self) -> None:
        """Best-effort bearer-token refresh.

        The CRM is HMAC-authenticated — a missing /api/mcp/refresh-token
        endpoint (404) is not a failure, it just means the CRM doesn't issue
        refreshable tokens. In that case we disable future refresh attempts
        (so 401s don't trigger an infinite refresh storm) and let the caller
        surface the underlying 401.
        """
        async with self._token_lock:
            if self._refresh_disabled:
                return
            cfg = runtime_config.get()
            remaining = _jwt_exp_seconds_until_expiry(cfg.leadpulse_token)
            if remaining is not None and remaining > _TOKEN_REFRESH_LEAD_SECONDS:
                return
            body = {"instance_id": cfg.instance_id}
            body_bytes = json.dumps(body, separators=(",", ":")).encode("utf-8")
            path = "/api/mcp/refresh-token"
            url = f"{self._base()}{path}"
            headers = {
                "Authorization": f"Bearer {cfg.leadpulse_token}",
                "X-MCP-Instance-Id": cfg.instance_id,
                "Content-Type": "application/json",
            }
            if cfg.hmac_secret:
                headers.update(sign(cfg.hmac_secret, "POST", path, body_bytes))

            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(url, content=body_bytes, headers=headers)
            except httpx.HTTPError as exc:
                log.warning("token_refresh_transport_error",
                            extra={"extra_payload": {"err": str(exc)[:200]}})
                return

            if resp.status_code == 404 or resp.status_code == 405:
                # CRM doesn't support token refresh — stop trying.
                self._refresh_disabled = True
                log.info("token_refresh_unavailable_disabled")
                return
            if resp.status_code in (401, 403):
                log.error("token_refresh_rejected",
                          extra={"extra_payload": {"status": resp.status_code}})
                raise CrmAuthFailed("Token refresh rejected; re-bootstrap required")
            if resp.status_code >= 400:
                log.warning("token_refresh_bad_status",
                            extra={"extra_payload": {"status": resp.status_code}})
                return
            try:
                data = resp.json()
            except ValueError:
                return
            new_token = (data.get("data") or {}).get("token")
            if isinstance(new_token, str) and new_token:
                await runtime_config.rotate_token(new_token)
                log.info("token_refreshed")

    # -------------------------------------------------------- sender-creds cache
    async def resolve_sender_credentials(
        self, campaign_id: str, tenant_user_id: str
    ) -> dict[str, Any]:
        """Return ``{provider, apiKey, fromEmail, fromName, ...}`` resolved by the CRM."""
        cache_key = f"{campaign_id}:{tenant_user_id}"
        async with self._secret_lock:
            entry = self._secret_cache.get(cache_key)
            if entry is not None and entry.expires_at > time.monotonic():
                return entry.value

        payload = {"campaign_id": campaign_id, "tenant_user_id": tenant_user_id}
        data = await self._request("POST", "/api/mcp/resolve-secret", json_body=payload)
        creds = data.get("data") or data  # be tolerant of either shape
        async with self._secret_lock:
            self._secret_cache[cache_key] = _CacheEntry(
                value=creds, expires_at=time.monotonic() + _SECRET_CACHE_TTL_SECONDS
            )
        return creds

    def invalidate_sender_cache(
        self, campaign_id: str | None = None, tenant_user_id: str | None = None
    ) -> None:
        if campaign_id is None and tenant_user_id is None:
            self._secret_cache.clear()
            return
        to_remove = [
            k
            for k in self._secret_cache
            if (campaign_id is None or k.startswith(f"{campaign_id}:"))
            and (tenant_user_id is None or k.endswith(f":{tenant_user_id}"))
        ]
        for k in to_remove:
            self._secret_cache.pop(k, None)

    # ----------------------------------------- auto-buffer event reports on fail
    async def _post_or_buffer(
        self, endpoint: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """POST an event to CRM; if unreachable, buffer it for replay."""
        try:
            return await self._request("POST", endpoint, json_body=payload)
        except CrmUnavailable:
            try:
                from app.db.mongodb import get_db
                from app.services.pending_crm_events import buffer_event

                await buffer_event(get_db(), endpoint, payload)
            except Exception:  # noqa: BLE001
                log.exception("buffer_crm_event_failed")
            return {"success": False, "buffered": True, "endpoint": endpoint}

    # ------------------------------------------------------- 11 MCP->CRM endpoints
    async def register(self, allocation: dict[str, Any] | None = None) -> dict[str, Any]:
        """One-time register. Signs with MCP_BOOTSTRAP_KEY (if supplied at
        bootstrap time); stores the per-instance HMAC secret returned by CRM.

        The CRM response shape (projex_crm):
            { "success": true,
              "data": { "instance": {...},
                        "apiKey": "<per-instance-secret>",
                        "config": {...} } }
        The per-instance secret must be stored and used to sign EVERY
        subsequent CRM call (heartbeat, campaigns, tracker-event, etc.).

        ``allocation``: optional agent-allocation summary
        (``{cpu_vcpu, ram_mb, senders, extraction, hygiene_eligible,
        daily_capacity}``) computed from the container's ECS task size.
        Lets the CRM Fleet Dashboard reconcile expected vs actual capacity
        without a second round-trip.
        """
        cfg = runtime_config.get()
        body: dict[str, Any] = {"instanceId": cfg.instance_id, "instance_id": cfg.instance_id}
        if allocation:
            body["allocation"] = allocation
        body_bytes = json.dumps(body, separators=(",", ":")).encode("utf-8")
        path = "/api/mcp/register"
        url = f"{self._base()}{path}"
        headers = self._auth_headers(
            "POST", path, body_bytes, signing_override=cfg.mcp_bootstrap_key
        )

        log.info(
            "mcp_register_request",
            extra={
                "extra_payload": {
                    "url": url,
                    "bootstrap_key_prefix": (cfg.mcp_bootstrap_key or "")[:6] + "…",
                    "instance_id": cfg.instance_id,
                    "ts": headers.get("X-MCP-Timestamp"),
                    "nonce_prefix": (headers.get("X-MCP-Nonce") or "")[:8] + "…",
                    "sig_prefix": (headers.get("X-MCP-Signature") or "")[:8] + "…",
                    "body_sha256_prefix": headers.get("X-MCP-Signature", "")[:0],
                    "body_len": len(body_bytes),
                }
            },
        )

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, content=body_bytes, headers=headers)

        if resp.status_code >= 400:
            log.error(
                "mcp_register_http_error",
                extra={
                    "extra_payload": {
                        "status": resp.status_code,
                        "body": resp.text[:500],
                    }
                },
            )
            resp.raise_for_status()

        try:
            data = resp.json()
        except ValueError:
            log.error(
                "mcp_register_non_json",
                extra={"extra_payload": {"status": resp.status_code, "body": resp.text[:500]}},
            )
            return {}

        payload = data.get("data") or {}
        new_secret = (
            payload.get("apiKey")
            or payload.get("api_key")
            or payload.get("hmac_secret")
            or payload.get("hmacSecret")
        )
        if isinstance(new_secret, str) and new_secret:
            await runtime_config.rotate_hmac(new_secret)
            log.info(
                "mcp_register_stored_secret",
                extra={"extra_payload": {"secret_prefix": new_secret[:6] + "…"}},
            )
        else:
            log.error(
                "mcp_register_no_secret_in_response",
                extra={"extra_payload": {"data_keys": list(payload.keys())}},
            )
        return data

    async def heartbeat(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/api/mcp/heartbeat", json_body=payload)

    async def get_active_campaigns(self, since: str | None = None) -> dict[str, Any]:
        params = {"since": since} if since else None
        return await self._request("GET", "/api/mcp/campaigns", params=params)

    async def get_campaign_manifest(self, campaign_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/api/mcp/campaigns/{campaign_id}/manifest")

    async def post_file_ingested(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._post_or_buffer("/api/mcp/file-ingested", payload)

    async def post_tracker_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._post_or_buffer("/api/mcp/tracker-event", payload)

    async def post_daily_rollup(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._post_or_buffer("/api/mcp/daily-rollup", payload)

    async def post_campaign_step_complete(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._post_or_buffer("/api/mcp/campaign-step-complete", payload)

    async def post_campaign_complete(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._post_or_buffer("/api/mcp/campaign-complete", payload)

    async def post_forecast(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._post_or_buffer("/api/mcp/forecast-push", payload)

    async def get_tenant_quotas(self) -> dict[str, Any] | None:
        """Fetch plan-tier per-tenant quotas from the CRM.

        Returns the response body on success, or ``None`` when the CRM
        endpoint isn't available yet (CRM TK-2655 is still backlog at
        time of writing — a 404 is expected until that ships). Lets the
        quota_refresher loop degrade gracefully without needing a
        feature flag on the MCP side.
        """
        try:
            return await self._request("GET", "/api/mcp/tenant-quotas")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise


leadpulse_client = LeadPulseClient()
