"""Render an email step template with contact placeholders + CRM tracking wrapping.

Placeholders supported: ``{{firstName}}``, ``{{lastName}}``, ``{{company}}``,
``{{jobTitle}}``, ``{{email}}``, ``{{booking_link}}``, ``{{target_url}}``,
and ``{{custom.<key>}}`` for datasheet-custom columns.

Tracking:
- Every ``<a href>`` is rewritten to the CRM click-tracking URL.
- A transparent 1x1 gif pixel pointing at the CRM's pixel endpoint is appended
  to the HTML body just before ``</body>``.
"""
from __future__ import annotations

import re
from typing import Any

_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}")
_ANCHOR_RE = re.compile(r"<a\s+([^>]*?)href\s*=\s*\"([^\"]+)\"([^>]*)>", re.IGNORECASE)


def _render_placeholders(template: str, ctx: dict[str, Any]) -> str:
    def sub(m: re.Match[str]) -> str:
        key = m.group(1)
        if key.startswith("custom."):
            return str(ctx.get("custom", {}).get(key[len("custom.") :], ""))
        return str(ctx.get(key, ""))

    return _PLACEHOLDER_RE.sub(sub, template)


def _link_type_hint(url: str, booking_url: str | None, target_url: str | None) -> str | None:
    """Classify a URL as booking/target_url so the CRM click handler can
    branch behavior (booking-CTA click creates a Prospect; others are
    counter-only). Matches on the prefix before the first {placeholder}
    token so {trackerId} / {company_url} stubs don't defeat the check.
    """
    def _prefix(u: str) -> str:
        brace = u.find("{")
        return u[:brace] if brace >= 0 else u

    if booking_url:
        bp = _prefix(booking_url).rstrip("/")
        if bp and url.startswith(bp):
            return "booking"
    if target_url:
        tp = _prefix(target_url).rstrip("/")
        if tp and url.startswith(tp):
            return "target_url"
    return None


def _wrap_links(
    html: str,
    tracker_base: str,
    tracker_id: str,
    booking_url: str | None = None,
    target_url: str | None = None,
) -> str:
    def rewrite(m: re.Match[str]) -> str:
        pre, url, post = m.group(1), m.group(2), m.group(3)
        from urllib.parse import quote

        hint = _link_type_hint(url, booking_url, target_url)
        t_param = f"&t={hint}" if hint else ""
        wrapped = f"{tracker_base}/api/tracking/click/{tracker_id}?url={quote(url, safe='')}{t_param}"
        return f"<a {pre}href=\"{wrapped}\"{post}>"

    return _ANCHOR_RE.sub(rewrite, html)


def _append_pixel(html: str, tracker_base: str, tracker_id: str) -> str:
    pixel = (
        f'<img src="{tracker_base}/api/tracking/pixel/{tracker_id}" '
        'width="1" height="1" style="display:none" alt="" />'
    )
    if "</body>" in html.lower():
        return re.sub(r"</body>", pixel + "</body>", html, count=1, flags=re.IGNORECASE)
    return html + pixel


def build_context(contact: dict[str, Any], campaign: dict[str, Any]) -> dict[str, Any]:
    """Build placeholder context with safe fallbacks.

    Greeting fallback: when first_name is blank we substitute
    ``campaign.config.default_greeting`` (default "there") so templates
    with ``Hi {{firstName}},`` never ship as ``Hi ,`` on the wire.
    Company falls back to an empty string — templates that depend on
    company should opt into ``{{company | default('your team')}}``-style
    rendering at authoring time, not here.
    """
    first_name = contact.get("first_name") or ""
    if not first_name:
        cfg = campaign.get("config") or campaign
        first_name = cfg.get("default_greeting", "there")
    # Booking URL: accept either name the CRM may use.
    booking_template = (
        campaign.get("booking_url_template")
        or campaign.get("booking_link_template")
        or ""
    )
    # Substitute {trackerId} placeholder if the URL is per-recipient.
    tracker_id = contact.get("_tracker_id") or ""
    if tracker_id and "{trackerId}" in booking_template:
        booking_template = booking_template.replace("{trackerId}", tracker_id)

    return {
        "firstName": first_name,
        "lastName": contact.get("last_name") or "",
        "company": contact.get("company") or contact.get("company_domain") or "",
        "jobTitle": contact.get("job_title") or "",
        "email": contact.get("email", ""),
        "booking_link": booking_template,
        "target_url": campaign.get("target_url_template", ""),
        "custom": contact.get("custom_fields", {}),
    }


def _text_to_html(text: str) -> str:
    """Minimal text→HTML conversion so open-pixel + click-wrap work when the
    template has only a plain-text body. Escapes HTML metacharacters, turns
    bare URLs into <a> tags, wraps in <p>."""
    from html import escape

    escaped = escape(text, quote=False)
    url_re = re.compile(r"(https?://[^\s<>\"']+)")
    with_links = url_re.sub(r'<a href="\1">\1</a>', escaped)
    # Double newline -> paragraph break; single newline -> <br>.
    paragraphs = [p.replace("\n", "<br>") for p in with_links.split("\n\n") if p.strip()]
    body = "".join(f"<p>{p}</p>" for p in paragraphs)
    return f"<html><body>{body}</body></html>"


def render_email(
    *,
    step: dict[str, Any],
    contact: dict[str, Any],
    campaign: dict[str, Any],
    tracker_id: str,
    tracker_base_url: str,
) -> dict[str, str]:
    """Return ``{subject, body_html, body_text}``."""
    # Expose the tracker_id on the contact so build_context can substitute
    # {trackerId} in per-recipient booking URLs.
    contact = {**contact, "_tracker_id": tracker_id}
    ctx = build_context(contact, campaign)

    subject = _render_placeholders(step.get("subject", step.get("subject_template", "")), ctx)
    body_html = _render_placeholders(step.get("body_html", step.get("body_template_html", "")), ctx)
    body_text = _render_placeholders(step.get("body_text", step.get("body_template_text", "")), ctx)

    # If the step is text-only, synthesize an HTML version from the text so
    # open-pixel and click-wrapping (and provider-native open tracking) work.
    if not body_html and body_text:
        body_html = _text_to_html(body_text)

    if body_html and tracker_id:
        booking_hint = (
            campaign.get("booking_url_template")
            or campaign.get("booking_link_template")
            or None
        )
        target_hint = campaign.get("target_url_template") or None
        body_html = _wrap_links(
            body_html,
            tracker_base_url.rstrip("/"),
            tracker_id,
            booking_url=booking_hint,
            target_url=target_hint,
        )
        body_html = _append_pixel(body_html, tracker_base_url.rstrip("/"), tracker_id)

    return {"subject": subject, "body_html": body_html, "body_text": body_text}
