"""Engine state monitoring (Pi side, advisory).

The engine is only considered RUNNING when two independent indications agree
for ``start_confirm_seconds``: RPM at or above ``running_rpm_min`` AND the
engine-running signal (e.g. a charge/oil-pressure style input chosen during
hardware integration) is true. A starter request is never taken as proof.

The safety controller has its own engine-running input and acts on it
independently; this monitor lets the Pi detect failures, report them and
request a stop. Any monitoring failure leads to a conservative result
(UNKNOWN state, which blocks starting and ends a remote run).
"""

from __future__ import annotations

from enum import Enum

from .clock import Clock
from .config import EngineConfig
from .vehicle_interface import VehicleStatus


class EngineState(Enum):
    UNKNOWN = "unknown"
    STOPPED = "stopped"
    TRANSITION = "transition"  # cranking, stopping or not yet confirmed
    RUNNING = "running"


class EngineEvent(Enum):
    STARTED = "started"
    START_FAILED = "start_failed"
    STALLED = "stalled"
    RUNTIME_EXCEEDED = "runtime_exceeded"
    SIGNAL_LOST = "signal_lost"
    IMPLAUSIBLE = "implausible"


class EngineMonitor:
    def __init__(self, cfg: EngineConfig, clock: Clock) -> None:
        self._cfg = cfg
        self._clock = clock
        self.state = EngineState.UNKNOWN
        self.rpm: float | None = None
        self._last_valid: float | None = None
        self._running_since: float | None = None
        self._contradiction_since: float | None = None
        self._attempt_deadline: float | None = None
        self._run_started: float | None = None
        self._run_limit: float | None = None
        self._reported: set[EngineEvent] = set()

    # -- control from the state machine -------------------------------------
    def begin_start_attempt(self, timeout_s: float) -> None:
        self._attempt_deadline = self._clock.monotonic() + timeout_s
        self._reported.discard(EngineEvent.START_FAILED)
        self._reported.discard(EngineEvent.STARTED)

    def begin_run(self, limit_s: float) -> None:
        self._attempt_deadline = None
        self._run_started = self._clock.monotonic()
        self._run_limit = limit_s
        self._reported.discard(EngineEvent.STALLED)
        self._reported.discard(EngineEvent.RUNTIME_EXCEEDED)

    def end_run(self) -> None:
        self._attempt_deadline = None
        self._run_started = None
        self._run_limit = None

    @property
    def run_elapsed(self) -> float:
        if self._run_started is None:
            return 0.0
        return self._clock.monotonic() - self._run_started

    # -- samples ------------------------------------------------------------
    def _emit(self, event: EngineEvent, out: list[EngineEvent]) -> None:
        if event not in self._reported:
            self._reported.add(event)
            out.append(event)

    def update(self, s: VehicleStatus) -> list[EngineEvent]:
        out: list[EngineEvent] = []
        c = self._cfg
        now = self._clock.monotonic()
        rpm, sig = s.engine_rpm, s.engine_running_signal
        if rpm is None or sig is None:
            # One source missing: no valid sample; check() reports loss.
            out += self.check()
            return out
        if rpm < 0 or rpm > c.implausible_rpm:
            self.state = EngineState.UNKNOWN
            self._emit(EngineEvent.IMPLAUSIBLE, out)
            return out
        self._last_valid = now
        self._reported.discard(EngineEvent.SIGNAL_LOST)
        self.rpm = rpm

        contradictory = (rpm >= c.running_rpm_min and not sig) or (rpm <= c.stopped_rpm_max and sig)
        if contradictory:
            self._contradiction_since = self._contradiction_since or now
            if now - self._contradiction_since >= c.contradiction_seconds:
                self.state = EngineState.UNKNOWN
                self._emit(EngineEvent.IMPLAUSIBLE, out)
                return out
        else:
            self._contradiction_since = None

        previous = self.state
        if rpm >= c.running_rpm_min and sig:
            self._running_since = self._running_since or now
            if now - self._running_since >= c.start_confirm_seconds:
                self.state = EngineState.RUNNING
        elif rpm <= c.stopped_rpm_max and not sig:
            self._running_since = None
            self.state = EngineState.STOPPED
        else:
            self._running_since = None
            if self.state is not EngineState.UNKNOWN:
                self.state = EngineState.TRANSITION

        if self.state is EngineState.RUNNING and self._attempt_deadline is not None:
            self._attempt_deadline = None
            self._emit(EngineEvent.STARTED, out)
        if (
            previous is EngineState.RUNNING
            and self.state is not EngineState.RUNNING
            and self._run_started is not None
        ):
            self._emit(EngineEvent.STALLED, out)
        out += self.check()
        return out

    def check(self) -> list[EngineEvent]:
        out: list[EngineEvent] = []
        now = self._clock.monotonic()
        if self._last_valid is None or now - self._last_valid > self._cfg.signal_timeout_seconds:
            if self._last_valid is not None or self._run_started or self._attempt_deadline:
                self.state = EngineState.UNKNOWN
                self._emit(EngineEvent.SIGNAL_LOST, out)
        if self._attempt_deadline is not None and now > self._attempt_deadline:
            self._attempt_deadline = None
            self._emit(EngineEvent.START_FAILED, out)
        if (
            self._run_started is not None
            and self._run_limit is not None
            and now - self._run_started > self._run_limit
        ):
            self._emit(EngineEvent.RUNTIME_EXCEEDED, out)
        return out

    def confirmed_stopped(self) -> bool:
        return self.state is EngineState.STOPPED
