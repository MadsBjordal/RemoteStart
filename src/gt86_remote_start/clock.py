"""Time sources.

All sequencing uses a monotonic clock. Wall-clock time is used only for
human-readable log timestamps. Both are injectable so tests and the
simulator can run in deterministic virtual time.
"""

from __future__ import annotations

import time
from typing import Protocol


class Clock(Protocol):
    def monotonic(self) -> float: ...

    def wall(self) -> float: ...


class SystemClock:
    def monotonic(self) -> float:
        return time.monotonic()

    def wall(self) -> float:
        return time.time()


class FakeClock:
    """Manually advanced clock for tests and simulation."""

    def __init__(self, start: float = 1000.0, wall_start: float = 1_790_000_000.0) -> None:
        self._t = start
        self._wall_offset = wall_start - start

    def monotonic(self) -> float:
        return self._t

    def wall(self) -> float:
        return self._t + self._wall_offset

    def advance(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("a monotonic clock cannot go backwards")
        self._t += seconds
