"""HMAC-SHA256 request signing + verification used on both sides of the
MCP <-> CRM channel.

Wire contract (matches projex_crm server/src/middleware/mcp-auth.middleware.ts):
  X-MCP-Instance-Id : instance id issued at bootstrap
  X-MCP-Timestamp   : ISO-8601 UTC string (Date.parse-able)
  X-MCP-Nonce       : random per-request string (replay-cached for 10 min)
  X-MCP-Signature   : hex(hmac_sha256(secret, payload))

  payload = timestamp + "\n" + METHOD + "\n" + path + "\n" + body_sha256 + "\n" + nonce
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timezone
from typing import Iterable

_TIMESTAMP_WINDOW_MS = 5 * 60 * 1000  # 5 minutes


def body_sha256(body: bytes) -> str:
    return hashlib.sha256(body or b"").hexdigest()


def _payload(timestamp: str, method: str, path: str, body: bytes, nonce: str) -> str:
    # Strip any query string: CRM signs ``req.originalUrl.split('?')[0]``.
    path_only = path.split("?", 1)[0]
    return f"{timestamp}\n{method.upper()}\n{path_only}\n{body_sha256(body)}\n{nonce}"


def sign(
    secret: str, method: str, path: str, body: bytes, nonce: str | None = None
) -> dict[str, str]:
    ts = datetime.now(timezone.utc).isoformat()
    n = nonce or secrets.token_hex(16)
    sig = hmac.new(
        secret.encode("utf-8"),
        _payload(ts, method, path, body, n).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return {"X-MCP-Timestamp": ts, "X-MCP-Signature": sig, "X-MCP-Nonce": n}


def _parse_ts_ms(ts: str) -> int | None:
    # Python's datetime.fromisoformat accepts +00:00 but not "Z" (until 3.11).
    try:
        cleaned = ts.replace("Z", "+00:00")
        return int(datetime.fromisoformat(cleaned).timestamp() * 1000)
    except (ValueError, TypeError):
        return None


def verify(
    secret: str,
    method: str,
    path: str,
    body: bytes,
    timestamp_header: str | None,
    signature_header: str | None,
    nonce_header: str | None,
) -> bool:
    if not timestamp_header or not signature_header or not nonce_header:
        return False
    ts_ms = _parse_ts_ms(timestamp_header)
    if ts_ms is None:
        return False
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    if abs(now_ms - ts_ms) > _TIMESTAMP_WINDOW_MS:
        return False
    expected = hmac.new(
        secret.encode("utf-8"),
        _payload(timestamp_header, method, path, body, nonce_header).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)


def any_secret_matches(
    secrets_list: Iterable[str],
    method: str,
    path: str,
    body: bytes,
    timestamp_header: str | None,
    signature_header: str | None,
    nonce_header: str | None,
) -> bool:
    for s in secrets_list:
        if s and verify(s, method, path, body, timestamp_header, signature_header, nonce_header):
            return True
    return False
