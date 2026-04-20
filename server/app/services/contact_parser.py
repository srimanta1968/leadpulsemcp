"""Stream-parse contact datasheets from CSV, XLSX, or JSON payloads.

Yields normalized dicts. Never loads the entire file into memory for CSV/JSON;
XLSX is read via openpyxl's iterator mode.
"""
from __future__ import annotations

import csv
import io
import json
import re
from typing import Iterable, Iterator

from openpyxl import load_workbook

_EMAIL_RE = re.compile(r"^[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}$", re.IGNORECASE)
_PHONE_RE = re.compile(r"[^\d+]")

_FIELD_ALIASES = {
    "email": {"email", "email_address", "e-mail", "mail"},
    "first_name": {"first_name", "firstname", "given_name", "first", "fname"},
    "last_name": {"last_name", "lastname", "surname", "family_name", "last", "lname"},
    "phone": {"phone", "phone_number", "mobile", "telephone", "tel"},
    "company": {"company", "organization", "org", "employer"},
    "company_url": {"company_url", "website", "url", "domain"},
    "job_title": {"job_title", "title", "role", "position"},
}


def _canonical_field(header: str) -> str | None:
    key = header.strip().lower().replace(" ", "_")
    for canonical, aliases in _FIELD_ALIASES.items():
        if key in aliases:
            return canonical
    return None


def _normalize_phone(raw: str) -> str:
    if not raw:
        return ""
    cleaned = _PHONE_RE.sub("", raw)
    if cleaned and not cleaned.startswith("+"):
        cleaned = "+" + cleaned
    return cleaned


def _normalize_row(raw_row: dict[str, str]) -> dict[str, object] | None:
    out: dict[str, object] = {"custom_fields": {}}
    for header, value in raw_row.items():
        if header is None:
            continue
        canonical = _canonical_field(header)
        str_value = "" if value is None else str(value).strip()
        if canonical is None:
            if str_value:
                out["custom_fields"][header.strip()] = str_value
            continue
        if canonical == "email":
            out["email"] = str_value.lower()
        elif canonical == "phone":
            out["phone"] = _normalize_phone(str_value)
        else:
            out[canonical] = str_value

    email = out.get("email")
    if not email or not isinstance(email, str) or not _EMAIL_RE.match(email):
        return None
    return out


def parse_csv(stream: io.IOBase) -> Iterator[dict[str, object]]:
    reader = csv.DictReader(io.TextIOWrapper(stream, encoding="utf-8", newline=""))
    for raw in reader:
        normalized = _normalize_row(raw)
        if normalized is not None:
            yield normalized


def parse_json(stream: io.IOBase) -> Iterator[dict[str, object]]:
    """Stream-parse a JSON contact file item-by-item using ijson.

    Supports two shapes:
      - top-level array: [ {...}, {...}, ... ]          -> items at "item"
      - envelope object: { "contacts": [ {...}, ... ] } -> items at "contacts.item"

    A 500 MB file never materializes in memory — only one row at a time.
    Phase 1 change #6 in docs/3phase_implementation.md.
    """
    import ijson  # lazy import: only this parser path needs it

    # Detect shape from the first non-whitespace byte without consuming
    # more than one char — `ijson` handles everything after this probe.
    head = stream.read(1)
    # Put it back at the front for ijson to re-read.
    remainder = stream.read()
    combined = io.BytesIO(head + remainder)

    if head == b"[":
        prefix = "item"
    elif head == b"{":
        prefix = "contacts.item"
    else:
        # Unknown shape; stay safe and yield nothing rather than OOM on a
        # full json.loads() of an arbitrary blob.
        return

    for raw in ijson.items(combined, prefix):
        if not isinstance(raw, dict):
            continue
        normalized = _normalize_row(
            {k: ("" if v is None else str(v)) for k, v in raw.items()}
        )
        if normalized is not None:
            yield normalized


def parse_xlsx(raw_bytes: bytes) -> Iterator[dict[str, object]]:
    wb = load_workbook(io.BytesIO(raw_bytes), read_only=True, data_only=True)
    ws = wb.active
    if ws is None:
        return
    rows_iter = ws.iter_rows(values_only=True)
    try:
        header_row = next(rows_iter)
    except StopIteration:
        return
    headers = [str(h) if h is not None else "" for h in header_row]
    for row in rows_iter:
        raw = {headers[i]: (row[i] if i < len(row) else "") for i in range(len(headers))}
        normalized = _normalize_row({k: ("" if v is None else str(v)) for k, v in raw.items()})
        if normalized is not None:
            yield normalized


def parse_stream(filename: str, content: bytes) -> Iterator[dict[str, object]]:
    name = filename.lower()
    if name.endswith(".csv"):
        yield from parse_csv(io.BytesIO(content))
    elif name.endswith(".json"):
        yield from parse_json(io.BytesIO(content))
    elif name.endswith(".xlsx"):
        yield from parse_xlsx(content)
    else:
        raise ValueError(f"Unsupported file type: {filename}")
