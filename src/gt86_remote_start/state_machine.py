"""Remote-start state definitions and transition validation.

The allowed transitions are data (``TRANSITIONS``). Any transition not in the
table is refused, logged as a critical error, and sends the machine to FAULT.
Entering FAULT or LOCKOUT is always allowed from any state, because stopping
must never be blocked by the table. The behaviour in each state lives in
``remote_start.py``; docs/architecture.md documents every state.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .clock import Clock
from .event_logger import EventLogger


class State(Enum):
    SYSTEM_OFF = "SYSTEM_OFF"
    INITIALIZING = "INITIALIZING"
    SELF_TEST = "SELF_TEST"
    STANDBY = "STANDBY"
    RESERVATION_REQUIRED = "RESERVATION_REQUIRED"
    RESERVATION_VALID = "RESERVATION_VALID"
    START_REQUESTED = "START_REQUESTED"
    SAFETY_CHECK = "SAFETY_CHECK"
    START_AUTHORIZED = "START_AUTHORIZED"
    CRANK_REQUESTED = "CRANK_REQUESTED"
    ENGINE_RUNNING = "ENGINE_RUNNING"
    SHUTDOWN_REQUESTED = "SHUTDOWN_REQUESTED"
    FAULT = "FAULT"
    LOCKOUT = "LOCKOUT"


S = State

ALWAYS_ALLOWED = frozenset({S.FAULT, S.LOCKOUT})

TRANSITIONS: dict[State, frozenset[State]] = {
    S.SYSTEM_OFF: frozenset({S.INITIALIZING}),
    S.INITIALIZING: frozenset({S.SELF_TEST, S.SYSTEM_OFF}),
    S.SELF_TEST: frozenset({S.STANDBY, S.SHUTDOWN_REQUESTED, S.SYSTEM_OFF}),
    S.STANDBY: frozenset({S.RESERVATION_REQUIRED, S.RESERVATION_VALID, S.SYSTEM_OFF}),
    S.RESERVATION_REQUIRED: frozenset({S.STANDBY, S.RESERVATION_VALID, S.SYSTEM_OFF}),
    S.RESERVATION_VALID: frozenset(
        {S.START_REQUESTED, S.RESERVATION_REQUIRED, S.STANDBY, S.SYSTEM_OFF}
    ),
    S.START_REQUESTED: frozenset({S.SAFETY_CHECK, S.RESERVATION_VALID, S.RESERVATION_REQUIRED}),
    S.SAFETY_CHECK: frozenset(
        {S.START_AUTHORIZED, S.RESERVATION_VALID, S.RESERVATION_REQUIRED, S.SHUTDOWN_REQUESTED}
    ),
    S.START_AUTHORIZED: frozenset({S.CRANK_REQUESTED, S.SHUTDOWN_REQUESTED}),
    S.CRANK_REQUESTED: frozenset({S.ENGINE_RUNNING, S.SHUTDOWN_REQUESTED}),
    S.ENGINE_RUNNING: frozenset({S.SHUTDOWN_REQUESTED, S.STANDBY}),
    S.SHUTDOWN_REQUESTED: frozenset({S.RESERVATION_REQUIRED, S.STANDBY}),
    S.FAULT: frozenset({S.INITIALIZING, S.SYSTEM_OFF}),
    S.LOCKOUT: frozenset({S.INITIALIZING}),
}

# States in which the vehicle may be under remote control (ignition held or
# starter possibly engaged). Leaving them requires a confirmed stop.
REMOTE_ACTIVE_STATES = frozenset({S.START_AUTHORIZED, S.CRANK_REQUESTED, S.ENGINE_RUNNING})


class InvalidTransition(RuntimeError):
    pass


@dataclass(frozen=True)
class TransitionRecord:
    at: float
    source: State
    target: State
    reason: str


class StateMachine:
    def __init__(self, clock: Clock, logger: EventLogger, initial: State = S.SYSTEM_OFF) -> None:
        self._clock = clock
        self._log = logger
        self.state = initial
        self.entered_at = clock.monotonic()
        self.history: list[TransitionRecord] = []

    def can_go(self, target: State) -> bool:
        return target in ALWAYS_ALLOWED or target in TRANSITIONS[self.state]

    def go(self, target: State, reason: str) -> None:
        if target is self.state:
            return
        if not self.can_go(target):
            self._log.log(
                "Rejected state transition",
                level="critical",
                source=self.state,
                target=target,
                reason=reason,
            )
            raise InvalidTransition(f"{self.state.name} -> {target.name} is not allowed")
        record = TransitionRecord(self._clock.monotonic(), self.state, target, reason)
        self.history.append(record)
        if len(self.history) > 200:
            del self.history[:100]
        level = "critical" if target in ALWAYS_ALLOWED else "info"
        self._log.log("State", level=level, source=self.state, target=target, reason=reason)
        self.state = target
        self.entered_at = record.at

    @property
    def time_in_state(self) -> float:
        return self._clock.monotonic() - self.entered_at
