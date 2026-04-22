"""CRM connectivity state machine.

The container heartbeats the CRM every 30s. When heartbeats start failing
we have to decide: is the CRM just rebooting (absorb it), down for a
while (pause new work but keep in-flight), or permanently gone (give up
and let ECS replace us)?

Modes (elapsed time since the FIRST failing heartbeat in the current
streak — resets on any success):

    CONNECTED       < 30s   — normal. Sender pulls and sends.
    STOP_NEW_WORK   ≥ 30s   — stop leasing new send_queue docs. Finish
                              anything already in-flight.
    DEGRADED        ≥ 75s   — same behavior as stop_new_work, louder
                              logging. CRM is genuinely down not slow.
    ISOLATED        ≥ 10min — self-terminate cleanly. ECS replaces us.

Heartbeat retry cadence:
    - On success: sleep 30s (normal interval).
    - On failure: 5s → 10s → 20s → 30s cap. Catches a 90s pm2 reload
      quickly without DDoSing a slow CRM.

Scope: in-process only. Every container keeps its own state. The CRM
side suppresses "container dead" alerts for up to 90s (grace window) and
while Mongo is down, which aligns with the thresholds here.
"""
from __future__ import annotations

import time
from enum import Enum
from typing import Any

from app.core.logging import get_logger

log = get_logger(__name__)


class Mode(str, Enum):
    CONNECTED = "connected"
    STOP_NEW_WORK = "stop_new_work"
    DEGRADED = "degraded"
    ISOLATED = "isolated"


STOP_NEW_WORK_AFTER_S = 30
DEGRADED_AFTER_S = 75
ISOLATED_AFTER_S = 600  # 10 min
HEALTHY_HEARTBEAT_INTERVAL_S = 30.0
_FAILURE_BACKOFF_S = (5.0, 10.0, 20.0, 30.0)


class _State:
    def __init__(self) -> None:
        self.first_failure_at: float | None = None
        self.last_ok_at: float | None = None
        self.consecutive_failures: int = 0
        self.consecutive_successes: int = 0
        self.last_error: str | None = None
        self._last_logged_mode: Mode = Mode.CONNECTED
        # Operator-initiated drain (POST /api/admin/mcp/drain). Returned
        # by the CRM in every heartbeat response. When true, we stop
        # leasing new work even if CRM connectivity is fine.
        self.drain_requested: bool = False

    def on_heartbeat_success(self) -> None:
        now = time.monotonic()
        prev_mode = self._current_mode(now)
        self.last_ok_at = now
        self.first_failure_at = None
        self.consecutive_failures = 0
        self.consecutive_successes += 1
        self.last_error = None
        new_mode = self._current_mode(now)
        if prev_mode != new_mode:
            log.info(
                "crm_connectivity_recovered",
                extra={"extra_payload": {"from": prev_mode.value, "to": new_mode.value}},
            )
            self._last_logged_mode = new_mode

    def on_heartbeat_failure(self, err: str) -> None:
        now = time.monotonic()
        prev_mode = self._current_mode(now)
        if self.first_failure_at is None:
            self.first_failure_at = now
        self.consecutive_successes = 0
        self.consecutive_failures += 1
        self.last_error = err[:300]
        new_mode = self._current_mode(now)
        if new_mode != self._last_logged_mode:
            log.warning(
                "crm_connectivity_state_change",
                extra={
                    "extra_payload": {
                        "from": prev_mode.value,
                        "to": new_mode.value,
                        "consecutive_failures": self.consecutive_failures,
                        "last_error": self.last_error,
                    }
                },
            )
            self._last_logged_mode = new_mode

    def _current_mode(self, now: float) -> Mode:
        if self.first_failure_at is None:
            return Mode.CONNECTED
        elapsed = now - self.first_failure_at
        if elapsed >= ISOLATED_AFTER_S:
            return Mode.ISOLATED
        if elapsed >= DEGRADED_AFTER_S:
            return Mode.DEGRADED
        if elapsed >= STOP_NEW_WORK_AFTER_S:
            return Mode.STOP_NEW_WORK
        return Mode.CONNECTED

    def current_mode(self) -> Mode:
        return self._current_mode(time.monotonic())

    def next_heartbeat_sleep_s(self) -> float:
        """Adaptive sleep: 30s on healthy, 5/10/20/30 backoff on failure."""
        if self.first_failure_at is None:
            return HEALTHY_HEARTBEAT_INTERVAL_S
        idx = min(self.consecutive_failures - 1, len(_FAILURE_BACKOFF_S) - 1)
        idx = max(idx, 0)
        return _FAILURE_BACKOFF_S[idx]

    def set_drain_requested(self, requested: bool) -> None:
        if requested and not self.drain_requested:
            log.warning("drain_requested_received")
        elif not requested and self.drain_requested:
            log.info("drain_cleared")
        self.drain_requested = requested

    def snapshot(self) -> dict[str, Any]:
        now = time.monotonic()
        mode = self._current_mode(now)
        failing_for_s = (
            int(now - self.first_failure_at) if self.first_failure_at else 0
        )
        return {
            "mode": mode.value,
            "consecutive_failures": self.consecutive_failures,
            "consecutive_successes": self.consecutive_successes,
            "failing_for_seconds": failing_for_s,
            "last_error": self.last_error,
            "drain_requested": self.drain_requested,
        }


_state = _State()


def on_heartbeat_success() -> None:
    _state.on_heartbeat_success()


def on_heartbeat_failure(err: str) -> None:
    _state.on_heartbeat_failure(err)


def current_mode() -> Mode:
    return _state.current_mode()


def next_heartbeat_sleep_s() -> float:
    return _state.next_heartbeat_sleep_s()


def set_drain_requested(requested: bool) -> None:
    _state.set_drain_requested(requested)


def drain_requested() -> bool:
    return _state.drain_requested


def can_lease_new_work() -> bool:
    """Sender check — stop pulling new work when CRM is degraded OR an
    operator-initiated drain is in progress."""
    if _state.drain_requested:
        return False
    return _state.current_mode() == Mode.CONNECTED


def should_finish_inflight_only() -> bool:
    return _state.current_mode() in (Mode.STOP_NEW_WORK, Mode.DEGRADED)


def should_self_terminate() -> bool:
    return _state.current_mode() == Mode.ISOLATED


def snapshot() -> dict[str, Any]:
    return _state.snapshot()
