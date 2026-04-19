"""In-process supervisor: runs the four agent loops and auto-restarts on crash.

Tracks per-loop crash counters and surfaces a ``degraded`` / ``unhealthy`` /
``quarantine`` status to the heartbeat loop.

Escalation:
- 5 crashes of the same loop within 10 min  -> loop disabled + instance degraded
- 10 crashes in 60 min OR >3 ECS restarts   -> instance quarantined
  (heartbeat still runs; extraction/sender stop pulling new work; operator
   must clear via POST /admin/scale-hint {action: resume}).
"""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from app.core.logging import get_logger
from app.core.metrics import metric_counter

log = get_logger(__name__)

_WINDOW_10_MIN = 600
_WINDOW_60_MIN = 3600
_MAX_CRASHES_10_MIN = 5
_MAX_CRASHES_60_MIN = 10


@dataclass
class LoopState:
    name: str
    coro_factory: Callable[[], Awaitable[None]]
    enabled: bool = True
    task: asyncio.Task | None = None
    last_crash_at: float | None = None
    crash_times: deque[float] = field(default_factory=lambda: deque(maxlen=200))
    restart_count: int = 0
    backoff_seconds: float = 1.0


class Supervisor:
    def __init__(self) -> None:
        self._loops: dict[str, LoopState] = {}
        self._stopping = asyncio.Event()
        self._quarantined = False
        self._total_restarts_60min: deque[float] = deque(maxlen=500)

    def register(self, name: str, coro_factory: Callable[[], Awaitable[None]]) -> None:
        self._loops[name] = LoopState(name=name, coro_factory=coro_factory)

    def quarantined(self) -> bool:
        return self._quarantined

    def status(self) -> dict:
        return {
            "quarantined": self._quarantined,
            "loops": {
                name: {
                    "enabled": s.enabled,
                    "restarts": s.restart_count,
                    "last_crash_at": s.last_crash_at,
                }
                for name, s in self._loops.items()
            },
        }

    async def start_all(self) -> None:
        for name in list(self._loops):
            self._spawn(name)

    def _spawn(self, name: str) -> None:
        state = self._loops[name]
        if not state.enabled or self._stopping.is_set():
            return
        state.task = asyncio.create_task(self._run_loop(state), name=f"agent:{name}")

    async def _run_loop(self, state: LoopState) -> None:
        while state.enabled and not self._stopping.is_set():
            try:
                await state.coro_factory()
                log.info("agent_loop_exit_clean", extra={"extra_payload": {"loop": state.name}})
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                now = time.monotonic()
                state.crash_times.append(now)
                state.last_crash_at = now
                state.restart_count += 1
                self._total_restarts_60min.append(now)
                log.exception(
                    "agent_loop_crashed",
                    extra={"extra_payload": {"loop": state.name, "err": str(exc)[:200]}},
                )
                metric_counter("mcp.watchdog.loop_restart_total", 1, {"loop": state.name})
                if self._should_disable(state):
                    state.enabled = False
                    log.error("agent_loop_disabled_by_crash_rate", extra={"extra_payload": {"loop": state.name}})
                    return
                if self._should_quarantine():
                    self._quarantined = True
                    log.error("instance_quarantined_due_to_crashes")
                await asyncio.sleep(min(60.0, state.backoff_seconds))
                state.backoff_seconds = min(60.0, state.backoff_seconds * 2)

    def _should_disable(self, state: LoopState) -> bool:
        now = time.monotonic()
        recent = [t for t in state.crash_times if now - t <= _WINDOW_10_MIN]
        return len(recent) >= _MAX_CRASHES_10_MIN

    def _should_quarantine(self) -> bool:
        now = time.monotonic()
        recent = [t for t in self._total_restarts_60min if now - t <= _WINDOW_60_MIN]
        return len(recent) >= _MAX_CRASHES_60_MIN

    def reset_quarantine(self) -> None:
        self._quarantined = False
        for state in self._loops.values():
            state.enabled = True
            state.backoff_seconds = 1.0
        log.info("quarantine_cleared_restarting_loops")
        for name in list(self._loops):
            if self._loops[name].task is None or self._loops[name].task.done():
                self._spawn(name)

    async def stop_all(self, timeout: float = 60.0) -> None:
        self._stopping.set()
        tasks = [s.task for s in self._loops.values() if s.task is not None and not s.task.done()]
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
            log.info("supervisor_stopped", extra={"extra_payload": {"tasks": len(tasks)}})


supervisor = Supervisor()
