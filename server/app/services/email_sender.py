"""Email delivery: provider-routed HTTP APIs (SendGrid/Mailgun/Postmark) with
SMTP fallback.

Credentials ``{provider, api_key, from_email, from_name, domain, region,
message_stream, smtp_host, smtp_port, smtp_user, smtp_password}`` are supplied
by leadpulse_client.resolve_sender_credentials. ``provider`` is lower-cased and
dispatched by send_email().
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
    idempotency_key: str | None = None,
) -> SendResult:
    provider = (creds.get("provider") or "").lower()
    if provider == "sendgrid":
        return await _send_via_sendgrid(
            creds, to_email, subject, body_html, body_text, idempotency_key
        )
    if provider == "mailgun":
        return await _send_via_mailgun(
            creds, to_email, subject, body_html, body_text, idempotency_key
        )
    if provider == "postmark":
        return await _send_via_postmark(
            creds, to_email, subject, body_html, body_text, idempotency_key
        )
    if provider == "smtp":
        return await _send_via_smtp(
            creds, to_email, subject, body_html, body_text, idempotency_key
        )
    return SendResult(
        ok=False, provider=provider or "unknown", provider_message_id=None,
        error=f"Unsupported provider: {provider!r}",
    )


async def _send_via_sendgrid(
    creds: dict[str, Any],
    to_email: str,
    subject: str,
    body_html: str,
    body_text: str,
    idempotency_key: str | None,
) -> SendResult:
    api_key = creds.get("api_key") or creds.get("apiKey")
    from_email = creds.get("from_email") or creds.get("fromEmail")
    from_name = creds.get("from_name") or creds.get("fromName") or ""
    if not api_key or not from_email:
        return SendResult(ok=False, provider="sendgrid", provider_message_id=None,
                          error="Missing sendgrid api_key or from_email")

    # SendGrid requires every content entry's value be a non-empty string.
    # Include text/plain and text/html only when populated; fall back to a
    # single-space plaintext so the payload is never empty.
    content: list[dict[str, str]] = []
    if body_text:
        content.append({"type": "text/plain", "value": body_text})
    if body_html:
        content.append({"type": "text/html", "value": body_html})
    if not content:
        content.append({"type": "text/plain", "value": " "})
    payload: dict[str, Any] = {
        "personalizations": [{"to": [{"email": to_email}]}],
        "from": {"email": from_email, "name": from_name},
        "subject": subject,
        "content": content,
        # Native SendGrid tracking so bare-URL plaintext emails still get
        # click-tracked (enable_text) and HTML gets open-tracked via pixel.
        "tracking_settings": {
            "click_tracking": {"enable": True, "enable_text": True},
            "open_tracking": {"enable": True},
        },
    }
    if idempotency_key:
        # custom_args echoes back on SendGrid Event Webhook so we can
        # reconcile a crash between SMTP success and our status write.
        payload["custom_args"] = {"idempotency_key": idempotency_key}
        # Stable Message-Id lets recipient mail servers dedupe on re-send.
        domain = from_email.split("@", 1)[1] if "@" in from_email else "leadpulse.local"
        payload["headers"] = {"Message-Id": f"<{idempotency_key}@{domain}>"}
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
                # 400/403 from SendGrid is nearly always sender-side: bad
                # payload shape, disallowed from-domain, key lacks scope.
                # True recipient bounces arrive async via the Event Webhook
                # (tracker-event). Don't poison the hygiene list on these.
                return SendResult(
                    ok=False, provider="sendgrid", provider_message_id=None,
                    error=f"sendgrid {resp.status_code}: {resp.text[:300]}",
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


async def _send_via_mailgun(
    creds: dict[str, Any],
    to_email: str,
    subject: str,
    body_html: str,
    body_text: str,
    idempotency_key: str | None,
) -> SendResult:
    api_key = creds.get("api_key") or creds.get("apiKey")
    from_email = creds.get("from_email") or creds.get("fromEmail")
    from_name = creds.get("from_name") or creds.get("fromName") or ""
    domain = creds.get("domain")
    region = (creds.get("region") or "us").lower()
    if not api_key or not from_email or not domain:
        return SendResult(
            ok=False, provider="mailgun", provider_message_id=None,
            error="Missing mailgun api_key, from_email, or domain",
        )

    host = "api.eu.mailgun.net" if region == "eu" else "api.mailgun.net"
    url = f"https://{host}/v3/{domain}/messages"

    from_header = f"{from_name} <{from_email}>" if from_name else from_email
    form: dict[str, Any] = {
        "from": from_header,
        "to": to_email,
        "subject": subject,
    }
    if body_text:
        form["text"] = body_text
    if body_html:
        form["html"] = body_html
    # Mailgun native tracking.
    form["o:tracking"] = "yes"
    form["o:tracking-clicks"] = "yes"
    form["o:tracking-opens"] = "yes"
    if idempotency_key:
        msg_domain = from_email.split("@", 1)[1] if "@" in from_email else "leadpulse.local"
        form["h:Message-Id"] = f"<{idempotency_key}@{msg_domain}>"
        form["v:idempotency_key"] = idempotency_key

    backoff = 1.0
    for attempt in range(4):
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(url, data=form, auth=("api", api_key))
            if resp.status_code == 429:
                await asyncio.sleep(backoff)
                backoff *= 2
                continue
            if 200 <= resp.status_code < 300:
                msg_id = (resp.json() or {}).get("id") if resp.content else None
                return SendResult(ok=True, provider="mailgun", provider_message_id=msg_id)
            if resp.status_code == 400:
                # Sender-side payload issue, not a recipient bounce.
                return SendResult(
                    ok=False, provider="mailgun", provider_message_id=None,
                    error=f"mailgun 400: {resp.text[:300]}",
                )
            if resp.status_code in (401, 403):
                return SendResult(
                    ok=False, provider="mailgun", provider_message_id=None,
                    error="mailgun api key rejected",
                )
            return SendResult(
                ok=False, provider="mailgun", provider_message_id=None,
                error=f"mailgun {resp.status_code}: {resp.text[:200]}",
            )
        except httpx.HTTPError as exc:
            log.warning("mailgun_transport_error", extra={"extra_payload": {"attempt": attempt, "err": str(exc)}})
            await asyncio.sleep(backoff)
            backoff *= 2
    return SendResult(
        ok=False, provider="mailgun", provider_message_id=None,
        error="mailgun exhausted retries",
    )


async def _send_via_postmark(
    creds: dict[str, Any],
    to_email: str,
    subject: str,
    body_html: str,
    body_text: str,
    idempotency_key: str | None,
) -> SendResult:
    api_key = creds.get("api_key") or creds.get("apiKey")
    from_email = creds.get("from_email") or creds.get("fromEmail")
    from_name = creds.get("from_name") or creds.get("fromName") or ""
    message_stream = creds.get("message_stream") or creds.get("messageStream") or "outbound"
    if not api_key or not from_email:
        return SendResult(
            ok=False, provider="postmark", provider_message_id=None,
            error="Missing postmark api_key or from_email",
        )

    from_header = f"{from_name} <{from_email}>" if from_name else from_email
    payload: dict[str, Any] = {
        "From": from_header,
        "To": to_email,
        "Subject": subject,
        "MessageStream": message_stream,
        # Postmark native tracking (HtmlAndText covers bare URLs in text).
        "TrackOpens": True,
        "TrackLinks": "HtmlAndText",
    }
    if body_text:
        payload["TextBody"] = body_text
    if body_html:
        payload["HtmlBody"] = body_html
    if idempotency_key:
        payload["Metadata"] = {"idempotency_key": idempotency_key}
        msg_domain = from_email.split("@", 1)[1] if "@" in from_email else "leadpulse.local"
        payload["Headers"] = [
            {"Name": "Message-ID", "Value": f"<{idempotency_key}@{msg_domain}>"},
        ]

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-Postmark-Server-Token": api_key,
    }
    backoff = 1.0
    for attempt in range(4):
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(
                    "https://api.postmarkapp.com/email", json=payload, headers=headers,
                )
            if resp.status_code == 429:
                await asyncio.sleep(backoff)
                backoff *= 2
                continue
            if 200 <= resp.status_code < 300:
                msg_id = (resp.json() or {}).get("MessageID") if resp.content else None
                return SendResult(ok=True, provider="postmark", provider_message_id=msg_id)
            # Postmark 422 = inactive recipient / blocked address (a real bounce).
            # 400 = malformed request (sender-side, not a bounce).
            if resp.status_code == 422:
                return SendResult(
                    ok=False, provider="postmark", provider_message_id=None,
                    bounce=True, bounce_type="hard",
                    error=f"postmark 422: {resp.text[:300]}",
                )
            if resp.status_code == 400:
                return SendResult(
                    ok=False, provider="postmark", provider_message_id=None,
                    error=f"postmark 400: {resp.text[:300]}",
                )
            if resp.status_code in (401, 403):
                return SendResult(
                    ok=False, provider="postmark", provider_message_id=None,
                    error="postmark server token rejected",
                )
            return SendResult(
                ok=False, provider="postmark", provider_message_id=None,
                error=f"postmark {resp.status_code}: {resp.text[:200]}",
            )
        except httpx.HTTPError as exc:
            log.warning("postmark_transport_error", extra={"extra_payload": {"attempt": attempt, "err": str(exc)}})
            await asyncio.sleep(backoff)
            backoff *= 2
    return SendResult(
        ok=False, provider="postmark", provider_message_id=None,
        error="postmark exhausted retries",
    )


async def _send_via_smtp(
    creds: dict[str, Any],
    to_email: str,
    subject: str,
    body_html: str,
    body_text: str,
    idempotency_key: str | None,
) -> SendResult:
    host = creds.get("smtp_host") or creds.get("smtpHost")
    port = int(creds.get("smtp_port") or creds.get("smtpPort") or 587)
    user = creds.get("smtp_user") or creds.get("smtpUser")
    password = creds.get("smtp_password") or creds.get("smtpPassword") or creds.get("apiKey") or creds.get("api_key")
    from_email = creds.get("from_email") or creds.get("fromEmail") or user
    from_name = creds.get("from_name") or creds.get("fromName") or ""
    if not host or not user or not password or not from_email:
        return SendResult(ok=False, provider="smtp", provider_message_id=None,
                          error="SMTP credentials incomplete")

    def _send_sync() -> SendResult:
        msg = EmailMessage()
        msg["From"] = f"{from_name} <{from_email}>" if from_name else from_email
        msg["To"] = to_email
        msg["Subject"] = subject
        if idempotency_key:
            # RFC 5322 Message-ID — recipients commonly dedupe identical IDs.
            domain = from_email.split("@", 1)[1] if "@" in from_email else "leadpulse.local"
            msg["Message-ID"] = f"<{idempotency_key}@{domain}>"
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
