"""CloudWatch Embedded Metric Format (EMF) emission.

Emits metric records as JSON lines that CloudWatch Logs agent recognizes and
auto-converts into metrics — no extra daemon required. Falls back to plain
structured logs when not running under ECS.

Usage:
    from app.core.metrics import metric_counter, metric_gauge
    metric_counter("mcp.sender.emails_sent_total", 1, {"campaign_id": cid})
    metric_gauge("mcp.queue.pending_depth", depth, {"campaign_id": cid})
"""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Any

_NAMESPACE = os.environ.get("METRICS_NAMESPACE", "LeadPulseMCP")


def _emit(metric_name: str, value: float, unit: str, dims: dict[str, str] | None) -> None:
    dims = dims or {}
    record = {
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [
                {
                    "Namespace": _NAMESPACE,
                    "Dimensions": [list(dims.keys())] if dims else [[]],
                    "Metrics": [{"Name": metric_name, "Unit": unit}],
                }
            ],
        },
        metric_name: value,
        **dims,
    }
    sys.stdout.write(json.dumps(record, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def metric_counter(name: str, value: float = 1, dims: dict[str, str] | None = None) -> None:
    _emit(name, value, "Count", dims)


def metric_gauge(name: str, value: float, dims: dict[str, str] | None = None) -> None:
    _emit(name, value, "None", dims)


def metric_latency_ms(name: str, ms: float, dims: dict[str, str] | None = None) -> None:
    _emit(name, ms, "Milliseconds", dims)


def metric_bytes(name: str, value: float, dims: dict[str, str] | None = None) -> None:
    _emit(name, value, "Bytes", dims)
