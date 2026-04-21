"""Agent allocation calculator.

Decides how many sender / extraction agents fit inside a given CPU+RAM
envelope. Mirrors the TypeScript implementation in the CRM
(server/src/services/container-sizes.service.ts). The two MUST stay
bit-for-bit identical so heartbeats reconcile cleanly against the CRM's
expected allocation per container size.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING


RUNTIME_OVERHEAD_MB = 200
PER_AGENT_MB = 80
PER_VCPU_AGENTS = 5
SENDS_PER_AGENT_PER_HOUR = 180
SEND_WINDOW_HOURS = 8


@dataclass(frozen=True)
class AgentAllocation:
    senders: int
    extraction: int
    hygiene_eligible: bool
    daily_capacity: int


def compute_allocation(cpu_vcpu: float, ram_mb: int) -> AgentAllocation:
    """Return the agent allocation for a container sized (cpu_vcpu, ram_mb).

    Constraints applied in order:
      - Memory budget: (ram_mb - overhead) / per_agent_mb
      - CPU budget:    cpu_vcpu * agents_per_vcpu  (I/O-bound ceiling)
      - Extraction uses 2 slots when total >= 20, else 1
      - Senders get the remainder, minimum 1
      - Hygiene singleton can only be elected on containers with total >= 5 slots

    Daily capacity assumes an 8-hour send window at 180 sends/agent/hour.
    Output must match server/src/services/container-sizes.service.ts exactly.
    """
    usable_ram = max(0, ram_mb - RUNTIME_OVERHEAD_MB)
    by_memory = usable_ram // PER_AGENT_MB
    by_cpu = math.floor(cpu_vcpu * PER_VCPU_AGENTS)
    total = max(1, min(by_memory, by_cpu))

    extraction = 2 if total >= 20 else 1 if total >= 2 else 0
    senders = max(1, total - extraction)
    hygiene_eligible = total >= 5
    daily_capacity = senders * SENDS_PER_AGENT_PER_HOUR * SEND_WINDOW_HOURS

    return AgentAllocation(
        senders=senders,
        extraction=extraction,
        hygiene_eligible=hygiene_eligible,
        daily_capacity=daily_capacity,
    )


# Reference presets — kept here as the Python-side source of truth for parity
# tests against the CRM's BASE_PRESETS. Actual runtime values arrive via env
# vars (CPU_VCPU, RAM_MB) and are not read from this table.
REFERENCE_PRESETS: tuple[tuple[str, float, int], ...] = (
    ("nano", 0.25, 512),
    ("micro", 0.5, 1024),
    ("small", 1.0, 1024),
    ("medium", 1.0, 2048),
    ("large", 2.0, 4096),
    ("xlarge", 4.0, 8192),
    ("2xlarge", 8.0, 16384),
)


if TYPE_CHECKING:
    # Static sanity check documented in types — runtime check is in tests.
    _check: AgentAllocation = compute_allocation(1.0, 1024)
