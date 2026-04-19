from __future__ import annotations

from app.services.template_renderer import render_email


def test_placeholders_and_tracking() -> None:
    step = {
        "subject": "Hi {{firstName}} from {{company}}",
        "body_html": '<html><body>Hello <a href="https://example.com/offer">click</a></body></html>',
        "body_text": "plain text for {{firstName}}",
    }
    contact = {"first_name": "Jane", "last_name": "Doe", "company": "Acme", "email": "jane@acme.com"}
    campaign = {"tracking_domain": "https://track.projexlight.com"}
    out = render_email(
        step=step,
        contact=contact,
        campaign=campaign,
        tracker_id="trk123",
        tracker_base_url="https://track.projexlight.com",
    )
    assert out["subject"] == "Hi Jane from Acme"
    assert "/api/tracking/click/trk123?u=https%3A%2F%2Fexample.com%2Foffer" in out["body_html"]
    assert "/api/tracking/pixel/trk123" in out["body_html"]
    assert out["body_text"] == "plain text for Jane"


def test_placeholder_with_missing_field_renders_empty() -> None:
    step = {"subject": "{{firstName}}", "body_html": "", "body_text": ""}
    contact = {"email": "x@y.z"}
    out = render_email(step=step, contact=contact, campaign={}, tracker_id="t", tracker_base_url="https://x")
    assert out["subject"] == ""
