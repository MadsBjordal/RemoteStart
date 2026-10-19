"""Simulated factory key fob.

A press goes through the (simulated) factory receiver, which authenticates
it, locks the doors, and makes the validated lock command visible to both
the Pi's lock-event interface and the safety controller's lock input.
"""

from __future__ import annotations

from collections.abc import Callable

from gt86_remote_start.clock import Clock
from gt86_remote_start.fob_listener import FobCommand, LockEvent, QueueLockSource


class SimulatedFob:
    def __init__(
        self,
        clock: Clock,
        pi_source: QueueLockSource,
        controller_lock: Callable[[], None],
    ) -> None:
        self._clock = clock
        self._source = pi_source
        self._controller_lock = controller_lock
        self._counter = 0
        self.pi_receiver_ok = True

    def press(self, command: FobCommand = FobCommand.LOCK) -> LockEvent:
        self._counter += 1
        event = LockEvent(
            command=command,
            received_at=self._clock.monotonic(),
            authenticated=True,
            event_id=f"fob-{self._counter}",
            source="simulated-receiver",
        )
        if self.pi_receiver_ok:
            self._source.push(event)
        if command is FobCommand.LOCK:
            self._controller_lock()
        return event

    def inject(self, event: LockEvent) -> None:
        """Push a raw event (duplicates, unauthenticated, stale...)."""
        self._source.push(event)
