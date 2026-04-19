"""Email delivery: SendGrid HTTP API (primary) with SMTP fallback.

Credentials ``{provider, api_key, from_email, from_name, smtp_host, smtp_port,
smtp_user, smtp_password}`` are supplied by leadpulse_client.resolve_sender_credentials.
"""
from __future__ import annotations

import asyncio
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any

import httpx

from app.core.logging import get_logger

log = get_logger(__name__)


@dataclass
class SendResult:
    ok: bool
    provider: str
    provider_message_id: str | None
    bounce: bool = False
    bounce_type: str | None = None  # "hard" | "soft"
    error: str | None = None


async def send_email(
    *,
    creds: dict[str, Any],
    to_email: str,
    subject: str,
    body_html: str,
    body_text: str,
) -> SendResult:
    provider = (creds.get("provider") or "").lower()
    if provider == "sendgrid":
        return await _send_via_sendgrid(creds, to_email, subject, body_html, body_text)
    if provider == "smtp":
        return await _send_via_smtp(creds, to_email, subject, body_html, body_text)
    return SendResult(
        ok=False, provider=provider or "unknown", provider_message_id=None,
        error=f"Unsupported provider: {provider!r}",
    )


async def _send_via_sendgrid(
    creds: dict[str, Any], to_email: str, subject: str, body_html: str, body_text: str
) -> SendResult:
    api_key = creds.get("api_key") or creds.get("apiKey")
    from_email = creds.get("from_email") or creds.get("fromEmail")
    from_name = creds.get("from_name") or creds.get("fromName") or ""
    if not api_key or not from_email:
        return SendResult(ok=False, provider="sendgrid", provider_message_id=None,
                          error="Missing sendgrid api_key or from_email")

    payload = {
        "personalizations": [{"to": [{"email": to_email}]}],
        "from": {"email": from_email, "name": from_name},
        "subject": subject,
        "content": [
            {"type": "text/plain", "value": body_text or ""},
            {"type": "text/html", "value": body_html or ""},
        ],
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    backoff = 1.0
    for attempt in range(4):
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(
                    "https://api.sendgrid.com/v3/mail/send", json=payload, headers=headers
                )
            if resp.status_code == 429:
                await asyncio.sleep(backoff)
                backoff *= 2
                continue
            if 200 <= resp.status_code < 300:
                msg_id = resp.headers.get("X-Message-Id")
                return SendResult(ok=True, provider="sendgrid", provider_message_id=msg_id)
            if resp.status_code in (400, 403):
                body = resp.text[:300]
                return SendResult(
                    ok=False, provider="sendgrid", provider_message_id=None,
                    bounce=True, bounce_type="hard",
                    error=f"sendgrid {resp.status_code}: {body}",
                )
            if resp.status_code == 401:
                return SendResult(
                    ok=False, provider="sendgrid", provider_message_id=None,
                    error="sendgrid api key rejected",
                )
            return SendResult(
                ok=False, provider="sendgrid", provider_message_id=None,
                error=f"sendgrid {resp.status_code}: {resp.text[:200]}",
            )
        except httpx.HTTPError as exc:
            log.warning("sendgrid_transport_error", extra={"extra_payload": {"attempt": attempt, "err": str(exc)}})
            await asyncio.sleep(backoff)
            backoff *= 2
    return SendResult(ok=False, provider="sendgrid", provider_message_id=None,
                      error="sendgrid exhausted retries")


async def _send_via_smtp(
    creds: dict[str, Any], to_email: str, subject: str, body_html: str, body_text: str
) -> SendResult:
    host = creds.get("smtp_host")
    port = int(creds.get("smtp_port") or 587)
    user = creds.get("smtp_user")
    password = creds.get("smtp_password")
    from_email = creds.get("from_email") or user
    from_name = creds.get("from_name") or ""
    if not host or not user or not password or not from_email:
        return SendResult(ok=False, provider="smtp", provider_message_id=None,
                          error="SMTP credentials incomplete")

    def _send_sync() -> SendResult:
        msg = EmailMessage()
        msg["From"] = f"{from_name} <{from_email}>" if from_name else from_email
        msg["To"] = to_email
        msg["Subject"] = subject
        if body_text:
            msg.set_content(body_text)
        if body_html:
            msg.add_alternative(body_html, subtype="html")
        try:
            with smtplib.SMTP(host, port, timeout=30) as s:
                s.ehlo()
                s.starttls()
                s.login(user, password)
                s.send_message(msg)
            return SendResult(ok=True, provider="smtp", provider_message_id=msg["Message-ID"])
        except smtplib.SMTPRecipientsRefused as exc:
            return SendResult(ok=False, provider="smtp", provider_message_id=None,
                              bounce=True, bounce_type="hard", error=str(exc))
        except smtplib.SMTPException as exc:
            return SendResult(ok=False, provider="smtp", provider_message_id=None,
                              error=str(exc))

    return await asyncio.to_thread(_send_sync)
