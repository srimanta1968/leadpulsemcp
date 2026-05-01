from __future__ import annotations

from app.services.contact_parser import (
    parse_stream,
    parse_stream_with_stats,
    validate_headers,
)


CSV_BYTES = (
    b"email,first_name,last_name,company,phone\n"
    b"Jane@Example.com,Jane,Doe,Acme Corp,+1 (555) 123-4567\n"
    b"not-an-email,Bob,,Widgets,\n"
    b"BOB@WIDGETS.IO,Bob,,Widgets,5551234567\n"
)


def test_csv_normalizes_email_and_phone_and_skips_invalid_rows() -> None:
    rows = list(parse_stream("contacts.csv", CSV_BYTES))
    assert len(rows) == 2
    assert rows[0]["email"] == "jane@example.com"
    assert rows[0]["phone"].startswith("+")
    assert rows[1]["email"] == "bob@widgets.io"


def test_json_array_is_parsed() -> None:
    payload = b'[{"email": "A@B.com", "first_name": "A"}, {"email": "bad"}]'
    rows = list(parse_stream("c.json", payload))
    assert len(rows) == 1 and rows[0]["email"] == "a@b.com"


def test_unsupported_extension_raises() -> None:
    import pytest

    with pytest.raises(ValueError):
        list(parse_stream("file.bin", b"ignored"))


# Files arriving with non-standard headers (e.g. "Business Emails",
# "Contact First Name") used to drop every row because alias detection
# missed the email column. The CRM column-mapping UI now sends an
# explicit override so users can still ingest those files.
NONSTANDARD_CSV = (
    b"Business Emails,Contact First Name,Contact Last Name,Company Name\n"
    b"jane@acme.com,Jane,Doe,Acme Corp\n"
    b"bob@widgets.io,Bob,Smith,Widgets\n"
)


def test_validate_headers_uses_mapping_override() -> None:
    headers = ["Business Emails", "Contact First Name", "Company Name", "Notes"]
    # No mapping → email is missing, all four are unknown.
    bare = validate_headers(headers)
    assert bare["missing_required"] == ["email"]
    # With mapping → email is matched, Notes stays unknown.
    mapping = {
        "Business Emails": "email",
        "Contact First Name": "first_name",
        "Company Name": "company",
    }
    mapped = validate_headers(headers, mapping)
    assert mapped["missing_required"] == []
    assert mapped["matched"]["email"] == "Business Emails"
    assert mapped["unknown"] == ["Notes"]


def test_parse_stream_with_stats_honors_mapping() -> None:
    mapping = {
        "Business Emails": "email",
        "Contact First Name": "first_name",
        "Contact Last Name": "last_name",
        "Company Name": "company",
    }
    rows, stats = parse_stream_with_stats("c.csv", NONSTANDARD_CSV, mapping)
    assert stats["rows_accepted"] == 2
    assert stats["rows_missing_email"] == 0
    assert rows[0]["email"] == "jane@acme.com"
    assert rows[0]["first_name"] == "Jane"
    assert rows[0]["company"] == "Acme Corp"


def test_mapping_empty_canonical_ignores_column() -> None:
    """Passing '' as the canonical drops a column entirely (not even kept
    as a custom_field). Lets users hide noise columns at mapping time."""
    csv_bytes = (
        b"email,first_name,internal_note\n"
        b"a@b.com,Jane,confidential\n"
    )
    mapping = {"internal_note": ""}
    rows, stats = parse_stream_with_stats("c.csv", csv_bytes, mapping)
    assert stats["rows_accepted"] == 1
    assert "internal_note" not in rows[0].get("custom_fields", {})


def test_mapping_none_keeps_existing_alias_behavior() -> None:
    """Backward-compat: callers that don't pass a mapping see no behavior
    change — alias-based auto-detection is unchanged."""
    rows, stats = parse_stream_with_stats("c.csv", CSV_BYTES)
    assert stats["rows_accepted"] == 2
    assert rows[0]["email"] == "jane@example.com"
