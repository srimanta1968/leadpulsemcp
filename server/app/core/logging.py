"""Structured JSON logging for the MCP. Every log line is a single JSON object
on stdout that ECS ships to CloudWatch.

Fields always present: ts, level, msg, instance_id, agent_uid, campaign_id,
email_hash, trace_id, module.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any

from app.core.runtime_config import runtime_config


_STANDARD_LOG_RECORD_ATTRS = frozenset({
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "message", "asctime", "taskName",
})


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:  # noqa: D401
        try:
            instance_id = runtime_config.get().instance_id if runtime_config.is_configured() else None
        except Exception:  # pragma: no cover — defensive
            instance_id = None

        payload: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "msg": record.getMessage(),
            "module": record.module,
            "instance_id": instance_id,
        }
        # Surface every `extra={...}` field passed by the caller. extra_payload
        # stays supported as a nested dict that's flattened into the top-level
        # JSON so queries like `$.path = "/api/v1/foo"` work in CloudWatch.
        for key, value in record.__dict__.items():
            if key in _STANDARD_LOG_RECORD_ATTRS or key in payload:
                continue
            if key == "extra_payload" and isinstance(value, dict):
                payload.update(value)
                continue
            if value is None:
                continue
            payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, separators=(",", ":"))


def hash_email(email: str) -> str:
    """Stable sha256 prefix used in log lines so PII is not logged verbatim."""
    return hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()[:16]


def configure_logging() -> None:
    level_name = os.environ.get("LOG_LEVEL", "info").upper()
    root = logging.getLogger()
    root.setLevel(level_name)
    for handler in list(root.handlers):
        root.removeHandler(handler)
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(JsonFormatter())
    root.addHandler(h)
    # Quiet overly chatty libs.
    for name in ("pymongo", "motor", "httpx", "httpcore", "uvicorn.access"):
        logging.getLogger(name).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
