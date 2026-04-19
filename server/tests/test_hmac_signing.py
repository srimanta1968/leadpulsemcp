from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.core.hmac_signing import any_secret_matches, sign, verify


def test_sign_and_verify_roundtrip() -> None:
    secret = "s3cret"
    h = sign(secret, "POST", "/api/x", b'{"a":1}')
    assert verify(
        secret, "POST", "/api/x", b'{"a":1}',
        h["X-MCP-Timestamp"], h["X-MCP-Signature"], h["X-MCP-Nonce"],
    )


def test_verify_fails_on_body_tampering() -> None:
    secret = "s3cret"
    h = sign(secret, "POST", "/api/x", b'{"a":1}')
    assert not verify(
        secret, "POST", "/api/x", b'{"a":2}',
        h["X-MCP-Timestamp"], h["X-MCP-Signature"], h["X-MCP-Nonce"],
    )


def test_verify_fails_on_stale_timestamp() -> None:
    secret = "s3cret"
    ts_stale = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    import hashlib
    import hmac

    payload = f"{ts_stale}\nGET\n/foo\n{hashlib.sha256(b'').hexdigest()}\nnonce"
    sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    assert not verify(secret, "GET", "/foo", b"", ts_stale, sig, "nonce")


def test_verify_fails_on_missing_nonce() -> None:
    secret = "s3cret"
    h = sign(secret, "POST", "/p", b"{}")
    assert not verify(
        secret, "POST", "/p", b"{}",
        h["X-MCP-Timestamp"], h["X-MCP-Signature"], None,
    )


def test_query_string_ignored_in_signature() -> None:
    secret = "s3cret"
    # Sign for the bare path; verifier must also only use the bare path even if
    # ``?foo=bar`` sneaks in.
    h = sign(secret, "GET", "/q", b"")
    assert verify(
        secret, "GET", "/q?foo=bar", b"",
        h["X-MCP-Timestamp"], h["X-MCP-Signature"], h["X-MCP-Nonce"],
    )


def test_any_secret_matches_during_rotation() -> None:
    old, new = "old-secret", "new-secret"
    h = sign(old, "POST", "/p", b"{}")
    assert any_secret_matches(
        [new, old], "POST", "/p", b"{}",
        h["X-MCP-Timestamp"], h["X-MCP-Signature"], h["X-MCP-Nonce"],
    )
