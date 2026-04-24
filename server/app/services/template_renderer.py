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
    Company falls back to a configurable word ("team" by default) so
    "I came across {{company}}" never renders as "I came across  ."
    """
    first_name = contact.get("first_name") or ""
    if not first_name:
        cfg = campaign.get("config") or campaign
        first_name = cfg.get("default_greeting", "there")

    cfg = campaign.get("config") or campaign
    company_fallback = cfg.get("default_company", "your team")

    tracker_id = contact.get("_tracker_id") or ""
    tracker_base = contact.get("_tracker_base") or ""
    contact_company_url = contact.get("company_website") or contact.get("company_url") or ""

    # Booking URL: accept either name the CRM may use.
    booking_template = (
        campaign.get("booking_url_template")
        or campaign.get("booking_link_template")
        or ""
    )
    if tracker_id and "{trackerId}" in booking_template:
        booking_template = booking_template.replace("{trackerId}", tracker_id)

    # Target URL (secondary CTA — "learn more", "see our pricing", etc).
    # Substitute {trackerId} and {company_url} so the rendered link is
    # fully-qualified. {company_url} falls back to contact.company_website
    # if present, otherwise is stripped (link will just point at the
    # tracker landing — graceful even without a known company URL).
    target_template = campaign.get("target_url_template", "") or ""
    if tracker_id and "{trackerId}" in target_template:
        target_template = target_template.replace("{trackerId}", tracker_id)
    if "{company_url}" in target_template:
        target_template = target_template.replace("{company_url}", contact_company_url)

    # Unsubscribe URL — always LeadPulse-hosted (never a user domain).
    # Authors can reference this as {{unsubscribe_link}}; the renderer
    # also auto-appends a footer so it's never missing.
    unsubscribe_link = ""
    if tracker_id and tracker_base:
        unsubscribe_link = f"{tracker_base.rstrip('/')}/api/tracking/unsubscribe/{tracker_id}"

    # Sender's business info for signature / "Learn more" CTA. Populated
    # by CRM's /api/mcp/campaigns from business_contexts.
    business = campaign.get("business") or {}
    website_url = business.get("website_url") or ""
    business_name = business.get("business_name") or ""

    return {
        "firstName": first_name,
        "lastName": contact.get("last_name") or "",
        "company": contact.get("company") or contact.get("company_domain") or company_fallback,
        "jobTitle": contact.get("job_title") or "",
        "email": contact.get("email", ""),
        "booking_link": booking_template,
        "target_url": target_template,
        "website_url": website_url,
        "business_name": business_name or "our website",
        "unsubscribe_link": unsubscribe_link,
        "custom": contact.get("custom_fields", {}),
    }


_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def _flatten_md_links_for_text(text: str) -> str:
    """Plain-text rendition: [Book a slot](url) -> 'Book a slot: url'.
    Keeps the label AND the URL visible (recipients on MUAs that don't
    render HTML still need a clickable/copyable URL)."""
    return _MD_LINK_RE.sub(lambda m: f"{m.group(1)}: {m.group(2)}", text)


def _text_to_html(text: str) -> str:
    """Minimal text→HTML conversion so open-pixel + click-wrap work when the
    template has only a plain-text body.

    Markdown-style [label](url) is rendered as <a href="url">label</a>
    with only the label visible (no raw URL), so emails look clean and
    clicks are one-tap. Bare URLs (no markdown) are still auto-linked.
    """
    from html import escape

    # 1. Extract [label](url) pairs before HTML-escape — replace with
    #    placeholders we can restore afterward without the anchor tags
    #    getting escaped.
    links: list[tuple[str, str]] = []

    def _capture_link(m: re.Match[str]) -> str:
        idx = len(links)
        links.append((m.group(1), m.group(2)))
        return f"\x00MDLINK{idx}\x00"

    staged = _MD_LINK_RE.sub(_capture_link, text)
    escaped = escape(staged, quote=False)

    # 2. Restore the markdown-link placeholders as real anchors. label
    #    itself is HTML-escaped; href is attribute-escaped (quote=True).
    def _restore(m: re.Match[str]) -> str:
        idx = int(m.group(1))
        label, url = links[idx]
        return f'<a href="{escape(url, quote=True)}">{escape(label, quote=False)}</a>'

    with_md_links = re.sub(r"\x00MDLINK(\d+)\x00", _restore, escaped)

    # 3. Auto-link any remaining bare URLs (didn't get explicit markdown).
    url_re = re.compile(r"(?<!href=\")(?<!\">)(https?://[^\s<>\"']+)")
    with_links = url_re.sub(r'<a href="\1">\1</a>', with_md_links)

    # Double newline -> paragraph break; single newline -> <br>.
    paragraphs = [p.replace("\n", "<br>") for p in with_links.split("\n\n") if p.strip()]
    body = "".join(f"<p>{p}</p>" for p in paragraphs)
    return f"<html><body>{body}</body></html>"


def _append_unsubscribe_footer_text(body: str, unsubscribe_url: str) -> str:
    """Append a plain-text unsubscribe line if the author hasn't put one
    in already. Required for CAN-SPAM compliance and Gmail bulk sender
    rules — we never want an email going out without it."""
    if not unsubscribe_url:
        return body
    if "unsubscribe" in body.lower():
        return body
    return body.rstrip() + (
        "\n\n---\n"
        "You're receiving this because we thought it might be relevant. "
        f"To stop hearing from us, unsubscribe here: {unsubscribe_url}"
    )


def _append_unsubscribe_footer_html(html: str, unsubscribe_url: str) -> str:
    """HTML equivalent — small grey footer with a clickable 'unsubscribe' link."""
    if not unsubscribe_url or "unsubscribe" in html.lower():
        return html
    footer = (
        '<p style="font-size:11px;color:#999;text-align:center;margin-top:30px;'
        'border-top:1px solid #eee;padding-top:12px;">'
        "You're receiving this because we thought it might be relevant. "
        f'<a href="{unsubscribe_url}" style="color:#999;text-decoration:underline;">Unsubscribe</a>.'
        "</p>"
    )
    if "</body>" in html.lower():
        return re.sub(r"</body>", footer + "</body>", html, count=1, flags=re.IGNORECASE)
    return html + footer


def render_email(
    *,
    step: dict[str, Any],
    contact: dict[str, Any],
    campaign: dict[str, Any],
    tracker_id: str,
    tracker_base_url: str,
) -> dict[str, str]:
    """Return ``{subject, body_html, body_text}``."""
    # Expose tracker_id + tracker_base on the contact so build_context can
    # substitute {trackerId} in booking/target URLs AND build the
    # {{unsubscribe_link}} off the same base host as the tracking wrapper.
    contact = {
        **contact,
        "_tracker_id": tracker_id,
        "_tracker_base": tracker_base_url,
    }
    ctx = build_context(contact, campaign)

    subject = _render_placeholders(step.get("subject", step.get("subject_template", "")), ctx)
    body_html = _render_placeholders(step.get("body_html", step.get("body_template_html", "")), ctx)
    body_text = _render_placeholders(step.get("body_text", step.get("body_template_text", "")), ctx)

    # If the step is text-only, synthesize an HTML version from the text so
    # open-pixel and click-wrapping (and provider-native open tracking) work.
    # The synthesizer honors markdown-style [label](url) so emails can show
    # clickable anchor text instead of raw URLs.
    if not body_html and body_text:
        body_html = _text_to_html(body_text)

    # Plain-text output: flatten markdown links to "label: url" so MUAs that
    # don't render HTML still show the label and a clickable URL.
    if body_text:
        body_text = _flatten_md_links_for_text(body_text)

    # Auto-append unsubscribe footer (CAN-SPAM + Gmail bulk sender compliance).
    unsubscribe_url = ctx.get("unsubscribe_link") or ""
    if body_text:
        body_text = _append_unsubscribe_footer_text(body_text, unsubscribe_url)

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
        body_html = _append_unsubscribe_footer_html(body_html, unsubscribe_url)
        body_html = _append_pixel(body_html, tracker_base_url.rstrip("/"), tracker_id)

    return {"subject": subject, "body_html": body_html, "body_text": body_text}
