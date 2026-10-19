"""Very simple engine model: enough to exercise start, stall and stop logic.

Values are illustrative, not FA20 measurements.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SimulatedEngine:
    crank_to_fire_s: float = 0.8
    cranking_rpm: float = 250.0
    idle_rpm: float = 900.0
    will_start: bool = True
    stall_after_s: float | None = None  # stall this long after firing
    running: bool = False
    rpm: float = 0.0
    _crank_time: float = 0.0
    _run_time: float = 0.0
    starts: int = 0

    def update(
        self, dt: float, ignition: bool, starter: bool, immobilizer_ok: bool, forced_stall: bool
    ) -> None:
        if not ignition or forced_stall:
            self.running = False
        if self.running:
            self._run_time += dt
            self.rpm += (self.idle_rpm - self.rpm) * min(1.0, dt * 3)
            if self.stall_after_s is not None and self._run_time >= self.stall_after_s:
                self.running = False
            return
        self._run_time = 0.0
        if ignition and starter:
            self.rpm = self.cranking_rpm
            self._crank_time += dt
            if self.will_start and immobilizer_ok and self._crank_time >= self.crank_to_fire_s:
                self.running = True
                self.starts += 1
                self.rpm = self.idle_rpm + 400
            return
        self._crank_time = 0.0
        self.rpm = max(0.0, self.rpm - 3000.0 * dt)

    def start_on_key(self) -> None:
        """Driver already started the car with the key (for scenario setup)."""
        self.running = True
        self.rpm = self.idle_rpm
