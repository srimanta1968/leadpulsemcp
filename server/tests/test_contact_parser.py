from __future__ import annotations

from app.services.contact_parser import parse_stream


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
