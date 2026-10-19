"""Triple-lock detection from authenticated factory lock events.

The factory keyless receiver authenticates the fob (rolling code). This
module never sees radio data: it consumes *validated lock events* from a
``LockEventSource`` (the real implementation will sit on an isolated
interface to the body electronics; see docs/vehicle_research.md for why the
exact signal is still unresolved).

Rules:
  * Only events marked ``authenticated`` count (a lock from the interior
    switch or a door-lock actuator movement is not a fob press).
  * Events are sequenced by the Pi's monotonic receive time.
  * A press closer than ``min_interval`` to the previous one, or repeating an
    event id already seen, is a duplicate and is ignored (debounce).
  * An event that waited longer than ``stale_after`` before being processed
    is ignored (it may belong to a long-past sequence).
  * ``count`` presses whose first and last fall within ``window`` make one
    activation. The sequence is then cleared and a cooldown starts, so one
    sequence can never trigger twice.
  * An UNLOCK (or any non-lock fob command) resets the sequence.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from .clock import Clock
from .config import RemoteStartConfig
from .event_logger import EventLogger


class FobCommand(Enum):
    LOCK = "lock"
    UNLOCK = "unlock"
    PANIC = "panic"
    OTHER = "other"


@dataclass(frozen=True)
class LockEvent:
    command: FobCommand
    received_at: float  # Pi monotonic time when the interface saw it
    authenticated: bool  # True only when the source proves a fob origin
    event_id: str | None = None  # source-provided id, if any, for de-duplication
    source: str = "unknown"


class LockEventSource(Protocol):
    def poll(self) -> list[LockEvent]: ...


@dataclass(frozen=True)
class Activation:
    at: float
    press_times: tuple[float, ...]


class FobListener:
    def __init__(
        self,
        cfg: RemoteStartConfig,
        clock: Clock,
        logger: EventLogger,
        on_activation: Callable[[Activation], None] | None = None,
    ) -> None:
        self._count = cfg.fob_press_count
        self._window = cfg.fob_press_window_seconds
        self._min_interval = cfg.fob_min_press_interval_seconds
        self._stale = cfg.fob_stale_event_seconds
        self._cooldown = cfg.activation_cooldown_seconds
        self._clock = clock
        self._log = logger
        self._on_activation = on_activation
        self._presses: list[float] = []
        self._last_press: float | None = None
        self._cooldown_until = float("-inf")
        self._seen_ids: deque[str] = deque(maxlen=32)
        self._pending: list[Activation] = []

    @property
    def presses_in_sequence(self) -> int:
        return len(self._presses)

    def reset(self, reason: str) -> None:
        if self._presses:
            self._log.log("Lock sequence reset", reason=reason, presses=len(self._presses))
        self._presses.clear()

    def feed(self, event: LockEvent) -> Activation | None:
        now = self._clock.monotonic()
        self.expire()
        if event.received_at > now + 0.001:
            self._log.log("Lock event rejected", level="warning", reason="timestamp in future")
            return None
        if now - event.received_at > self._stale:
            self._log.log(
                "Lock event ignored", reason="stale", age_s=now - event.received_at
            )
            return None
        if not event.authenticated:
            self._log.log("Lock event ignored", reason="not authenticated", source=event.source)
            return None
        if event.command is not FobCommand.LOCK:
            self.reset(f"fob {event.command.value}")
            return None
        if event.event_id is not None:
            if event.event_id in self._seen_ids:
                self._log.log("Lock event ignored", reason="duplicate id", id=event.event_id)
                return None
            self._seen_ids.append(event.event_id)
        t = event.received_at
        if self._last_press is not None and t - self._last_press < self._min_interval:
            self._log.log("Lock event ignored", reason="debounce", gap_s=t - self._last_press)
            return None
        self._last_press = t
        if t < self._cooldown_until:
            self._log.log(
                "Lock event ignored", reason="cooldown", remaining_s=self._cooldown_until - t
            )
            return None

        self._presses.append(t)
        n = len(self._presses)
        self._log.log(f"Factory lock event {n} of {self._count} received", source=event.source)
        if n < self._count:
            return None
        activation = Activation(at=t, press_times=tuple(self._presses))
        self._presses.clear()
        self._cooldown_until = t + self._cooldown
        self._log.log("Lock sequence accepted", presses=self._count)
        self._pending.append(activation)
        if self._on_activation:
            self._on_activation(activation)
        return activation

    def expire(self) -> None:
        """Drop presses that can no longer complete a sequence in the window."""
        now = self._clock.monotonic()
        if self._presses and now - self._presses[0] > self._window:
            self._log.log(
                "Lock sequence incomplete", presses=len(self._presses), needed=self._count
            )
            self._presses.clear()

    def take_activations(self) -> list[Activation]:
        out, self._pending = self._pending, []
        return out


class QueueLockSource:
    """In-memory source used by the simulator and tests."""

    def __init__(self) -> None:
        self._queue: list[LockEvent] = []

    def push(self, event: LockEvent) -> None:
        self._queue.append(event)

    def poll(self) -> list[LockEvent]:
        out, self._queue = self._queue, []
        return out
