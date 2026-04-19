"""Tiny fake LeadPulse CRM for local dev.

Implements just enough of the /api/mcp/* surface so the MCP's four agent loops
have something to talk to. Issues an HMAC secret at /register, returns one
in-memory campaign at /campaigns, serves a manifest that points at a local
CSV fixture, and accepts every inbound event with a 200.

Usage:
  python scripts/fake_crm.py   # listens on 0.0.0.0:9000
"""
from __future__ import annotations

import base64
import csv
import io
import json
import secrets
import sys
import time
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 9000

# In-memory state
_hmac_secret = secrets.token_hex(32)
_token = "dev-token-at-least-16-chars"
_started_at = datetime.now(timezone.utc)
_event_log: list[dict[str, Any]] = []

# A single demo campaign + one datasheet. The datasheet is served inline via a
# data: URL so we don't need S3. The MCP downloads it with httpx, which handles
# plain HTTP(S) only — so we also expose the same bytes at /local-datasheet.
_CAMPAIGN = {
    "id": "demo-campaign-1",
    "tenant_user_id": "demo-tenant-1",
    "status": "running",
    "paused": False,
    "timezone": "UTC",
    "start_date": _started_at.isoformat(),
    "send_window_start": "09:00",
    "send_window_end": "17:00",
    "daily_send_cap": 100,
    "provider": "sendgrid",
    "sender_secret_ref": "dev:secret",
    "tracking_domain": "http://localhost:9000",
    "sequence_snapshot": [
        {
            "step_index": 0,
            "send_offset_days": 0,
            "subject": "Welcome {{firstName}}",
            "body_html": '<html><body>Hi {{firstName}} at {{company}}! <a href="https://example.com/book">Book a demo</a></body></html>',
            "body_text": "Hi {{firstName}}!",
        }
    ],
}

_CSV_BYTES = (
    "email,first_name,last_name,company\n"
    "alice@acme.com,Alice,Anderson,Acme\n"
    "bob@widgets.io,Bob,Baker,Widgets\n"
).encode()


def _json_response(handler: BaseHTTPRequestHandler, status: int, body: dict) -> None:
    payload = json.dumps(body).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(payload)))
    handler.end_headers()
    handler.wfile.write(payload)


def _read_body(handler: BaseHTTPRequestHandler) -> dict:
    length = int(handler.headers.get("Content-Length") or 0)
    raw = handler.rfile.read(length) if length else b""
    try:
        return json.loads(raw.decode() or "{}")
    except json.JSONDecodeError:
        return {}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[crm] {self.address_string()} {fmt % args}", flush=True)

    # ----- MCP -> CRM endpoints -----
    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        body = _read_body(self)
        if path == "/api/mcp/register":
            _json_response(self, 200, {
                "success": True,
                "data": {"instance_id": body.get("instance_id"), "hmac_secret": _hmac_secret},
            })
        elif path == "/api/mcp/heartbeat":
            print(f"[crm] heartbeat status={body.get('status')} agents={len(body.get('agents', []))}")
            _json_response(self, 200, {"success": True, "data": {"ok": True}})
        elif path == "/api/mcp/resolve-secret":
            _json_response(self, 200, {"success": True, "data": {
                "provider": "smtp",
                "smtp_host": "localhost",
                "smtp_port": 1025,       # pair with mailpit/mailhog for real delivery
                "smtp_user": "dev",
                "smtp_password": "dev",
                "from_email": "demo@example.com",
                "from_name": "Demo",
            }})
        elif path == "/api/mcp/refresh-token":
            _json_response(self, 200, {"success": True, "data": {"token": _token}})
        elif path in (
            "/api/mcp/file-ingested",
            "/api/mcp/tracker-event",
            "/api/mcp/daily-rollup",
            "/api/mcp/campaign-step-complete",
            "/api/mcp/campaign-complete",
            "/api/mcp/forecast-push",
        ):
            _event_log.append({"path": path, "body": body, "ts": time.time()})
            print(f"[crm] {path} -> {json.dumps(body)[:120]}")
            _json_response(self, 200, {"success": True, "data": {"received": True}})
        else:
            _json_response(self, 404, {"success": False, "error": f"Unknown POST {path}"})

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/api/mcp/campaigns":
            _json_response(self, 200, {"success": True, "data": {"campaigns": [_CAMPAIGN]}})
        elif path.startswith("/api/mcp/campaigns/") and path.endswith("/manifest"):
            manifest = {
                "campaign": _CAMPAIGN,
                "steps": _CAMPAIGN["sequence_snapshot"],
                "files": [
                    {
                        "file_id": "demo-file-1",
                        "presigned_url": f"http://127.0.0.1:{PORT}/local-datasheet",
                        "original_filename": "contacts.csv",
                        "ingestion_status": "pending",
                        "trackers": [
                            {"email": "alice@acme.com", "tracker_id": "trk-alice"},
                            {"email": "bob@widgets.io", "tracker_id": "trk-bob"},
                        ],
                    }
                ],
            }
            _json_response(self, 200, {"success": True, "data": manifest})
        elif path == "/local-datasheet":
            self.send_response(200)
            self.send_header("Content-Type", "text/csv")
            self.send_header("Content-Length", str(len(_CSV_BYTES)))
            self.end_headers()
            self.wfile.write(_CSV_BYTES)
        elif path == "/debug/events":
            _json_response(self, 200, {"success": True, "data": {"events": _event_log[-50:], "hmac_secret_prefix": _hmac_secret[:8]}})
        elif path == "/api/tracking/pixel" or path.startswith("/api/tracking/pixel/"):
            self.send_response(200)
            self.send_header("Content-Type", "image/gif")
            self.end_headers()
            self.wfile.write(b"GIF89a")
        elif path.startswith("/api/tracking/click/"):
            self.send_response(302)
            self.send_header("Location", "https://example.com")
            self.end_headers()
        else:
            _json_response(self, 404, {"success": False, "error": f"Unknown GET {path}"})


if __name__ == "__main__":
    print(f"[crm] fake LeadPulse CRM listening on http://0.0.0.0:{PORT}")
    print(f"[crm] HMAC secret (only shown once): {_hmac_secret}")
    print(f"[crm] debug events: http://127.0.0.1:{PORT}/debug/events")
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
