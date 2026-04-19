"""Container health probe: memory, event-loop lag, Mongo latency, file descriptors.

Results feed into the watchdog + /health endpoint.
"""
from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field

try:
    import resource  # POSIX-only
except ImportError:  # pragma: no cover — Windows dev only
    resource = None  # type: ignore[assignment]

from app.core.logging import get_logger
from app.db.mongodb import ping as mongo_ping

log = get_logger(__name__)


@dataclass
class HealthSnapshot:
    healthy: bool
    degraded: bool
    rss_mb: float
    rss_warn: bool
    rss_fail: bool
    event_loop_lag_ms: float
    event_loop_fail: bool
    mongo_latency_ms: float
    mongo_warn: bool
    mongo_fail: bool
    open_fds: int
    messages: list[str] = field(default_factory=list)


class RuntimeProbe:
    def __init__(self) -> None:
        self._mem_limit_mb = _read_mem_limit_mb()
        self._last: HealthSnapshot | None = None

    @property
    def last(self) -> HealthSnapshot | None:
        return self._last

    async def measure(self) -> HealthSnapshot:
        msgs: list[str] = []
        rss_mb = _rss_mb()
        rss_warn = self._mem_limit_mb > 0 and rss_mb / self._mem_limit_mb > 0.80
        rss_fail = self._mem_limit_mb > 0 and rss_mb / self._mem_limit_mb > 0.95
        if rss_fail:
            msgs.append(f"memory critical: {rss_mb:.0f}MB / {self._mem_limit_mb}MB")
        elif rss_warn:
            msgs.append(f"memory high: {rss_mb:.0f}MB / {self._mem_limit_mb}MB")

        lag_ms = await _measure_event_loop_lag()
        loop_fail = lag_ms > 500.0
        if loop_fail:
            msgs.append(f"event loop lag {lag_ms:.0f}ms > 500ms")

        try:
            mongo_ms = await asyncio.wait_for(mongo_ping(), timeout=3.0)
        except Exception as exc:  # noqa: BLE001
            mongo_ms = 9999.0
            msgs.append(f"mongo ping failed: {exc}")
        mongo_warn = mongo_ms > 500.0
        mongo_fail = mongo_ms > 2000.0

        open_fds = _count_open_fds()

        snapshot = HealthSnapshot(
            healthy=not (rss_fail or loop_fail or mongo_fail),
            degraded=bool(msgs),
            rss_mb=rss_mb,
            rss_warn=rss_warn,
            rss_fail=rss_fail,
            event_loop_lag_ms=lag_ms,
            event_loop_fail=loop_fail,
            mongo_latency_ms=mongo_ms,
            mongo_warn=mongo_warn,
            mongo_fail=mongo_fail,
            open_fds=open_fds,
            messages=msgs,
        )
        self._last = snapshot
        if not snapshot.healthy:
            log.warning("runtime_probe_unhealthy", extra={"extra_payload": {"msgs": msgs}})
        return snapshot


def _rss_mb() -> float:
    if resource is None:
        try:
            with open(f"/proc/{os.getpid()}/status") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        return float(line.split()[1]) / 1024.0
        except OSError:
            return 0.0
        return 0.0
    usage = resource.getrusage(resource.RUSAGE_SELF)
    if os.name == "posix":
        return usage.ru_maxrss / 1024.0
    return float(usage.ru_maxrss) / (1024.0 * 1024.0)


async def _measure_event_loop_lag() -> float:
    t0 = time.perf_counter()
    await asyncio.sleep(0)
    return (time.perf_counter() - t0) * 1000.0


def _read_mem_limit_mb() -> int:
    """Best-effort cgroup memory.limit_in_bytes. 0 if unavailable."""
    for path in (
        "/sys/fs/cgroup/memory.max",
        "/sys/fs/cgroup/memory/memory.limit_in_bytes",
    ):
        try:
            with open(path) as f:
                raw = f.read().strip()
            if raw == "max":
                return 0
            return int(int(raw) / (1024 * 1024))
        except OSError:
            continue
    return 0


def _count_open_fds() -> int:
    try:
        return len(os.listdir(f"/proc/{os.getpid()}/fd"))
    except OSError:
        return -1


runtime_probe = RuntimeProbe()
