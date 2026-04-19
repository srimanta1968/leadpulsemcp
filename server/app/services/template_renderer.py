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


def _wrap_links(html: str, tracker_base: str, tracker_id: str) -> str:
    def rewrite(m: re.Match[str]) -> str:
        pre, url, post = m.group(1), m.group(2), m.group(3)
        from urllib.parse import quote

        wrapped = f"{tracker_base}/api/tracking/click/{tracker_id}?u={quote(url, safe='')}"
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
    return {
        "firstName": contact.get("first_name", ""),
        "lastName": contact.get("last_name", ""),
        "company": contact.get("company", ""),
        "jobTitle": contact.get("job_title", ""),
        "email": contact.get("email", ""),
        "booking_link": campaign.get("booking_link_template", ""),
        "target_url": campaign.get("target_url_template", ""),
        "custom": contact.get("custom_fields", {}),
    }


def render_email(
    *,
    step: dict[str, Any],
    contact: dict[str, Any],
    campaign: dict[str, Any],
    tracker_id: str,
    tracker_base_url: str,
) -> dict[str, str]:
    """Return ``{subject, body_html, body_text}``."""
    ctx = build_context(contact, campaign)

    subject = _render_placeholders(step.get("subject", step.get("subject_template", "")), ctx)
    body_html = _render_placeholders(step.get("body_html", step.get("body_template_html", "")), ctx)
    body_text = _render_placeholders(step.get("body_text", step.get("body_template_text", "")), ctx)

    if body_html and tracker_id:
        body_html = _wrap_links(body_html, tracker_base_url.rstrip("/"), tracker_id)
        body_html = _append_pixel(body_html, tracker_base_url.rstrip("/"), tracker_id)

    return {"subject": subject, "body_html": body_html, "body_text": body_text}
