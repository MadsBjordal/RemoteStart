"""Wires the Pi application, the safety-controller model and the vehicle
together in deterministic virtual time."""

from __future__ import annotations

from collections.abc import Coroutine
from typing import Any

from gt86_remote_start.clock import FakeClock
from gt86_remote_start.config import AppConfig, config_from_dict
from gt86_remote_start.diagnostics import FaultManager
from gt86_remote_start.event_logger import EventLogger, format_record
from gt86_remote_start.fob_listener import FobCommand, QueueLockSource
from gt86_remote_start.immobilizer_interface import ImmobilizerStatus, StaticImmobilizerInterface
from gt86_remote_start.remote_start import RemoteStartController
from gt86_remote_start.state_machine import State
from sc_model import ScParams

from .simulated_fob import SimulatedFob
from .simulated_safety_controller import SimulatedSafetyController
from .simulated_vehicle import SimulatedVehicleInterface, VehicleWorld

SIM_CONFIG: dict[str, Any] = {
    "remote_start": {"enabled": True, "runtime_seconds": 60, "maximum_runtime_seconds": 120},
    "hardware": {"mode": "simulation"},
    "logging": {"echo_to_console": False},
}


def run_sync(coro: Coroutine[Any, Any, Any]) -> Any:
    """Drive a coroutine that never truly suspends (all simulated I/O)."""
    try:
        coro.send(None)
    except StopIteration as stop:
        return stop.value
    coro.close()
    raise RuntimeError("simulated coroutine suspended unexpectedly")


class SimulationHarness:
    def __init__(
        self,
        config: AppConfig | None = None,
        sc_params: ScParams | None = None,
        echo: bool = False,
    ) -> None:
        self.cfg = config or config_from_dict(SIM_CONFIG)
        self.dt = self.cfg.tick_seconds
        self.clock = FakeClock()
        self.echo = echo
        self.world = VehicleWorld(self.clock)
        self.controller = SimulatedSafetyController(self.clock, self.world, sc_params)
        self.immobilizer = StaticImmobilizerInterface(ImmobilizerStatus.READY)
        self.vehicle_if = SimulatedVehicleInterface(self.world)
        self.lock_source = QueueLockSource()
        self.fob = SimulatedFob(self.clock, self.lock_source, self.controller.notify_lock)
        self.pi_alive = True
        self.pi_boots = 0
        self._boot_pi()

    # -- Pi lifecycle --------------------------------------------------------
    def _boot_pi(self) -> None:
        self.pi_boots += 1
        echo = (lambda line: print(line)) if self.echo else None
        self.log = EventLogger(self.clock, echo=echo)
        self.faults = FaultManager(self.clock, self.log, latch_enabled=True)
        self.pi = RemoteStartController(
            self.cfg,
            self.clock,
            self.log,
            self.faults,
            self.lock_source,
            self.vehicle_if,
            self.immobilizer,
            self.controller.pi_end,
        )
        self.pi.start()

    def crash_pi(self) -> None:
        self.pi_alive = False

    def reboot_pi(self) -> None:
        self.pi_alive = True
        self._boot_pi()

    # -- time ------------------------------------------------------------------
    def step(self) -> None:
        self.clock.advance(self.dt)
        self.world.update(self.dt)
        self.controller.step()
        if self.pi_alive:
            run_sync(self.pi.tick())

    def run(self, seconds: float) -> None:
        for _ in range(max(1, round(seconds / self.dt))):
            self.step()

    def run_until(self, predicate: Any, timeout: float) -> bool:
        for _ in range(max(1, round(timeout / self.dt))):
            if predicate():
                return True
            self.step()
        return bool(predicate())

    @property
    def state(self) -> State:
        return self.pi.state

    # -- driver actions ----------------------------------------------------------
    def boot(self) -> None:
        """Let the system initialise with the car parked and switched off."""
        self.run(2.0)

    def drive_in_and_park(self, gear: str = "N") -> None:
        w = self.world
        w.key_on = True
        w.gear = gear
        w.clutch_pressed = gear != "N"
        w.parking_brake = False
        w.engine.start_on_key()
        self.run(1.5)

    def perform_reservation(self) -> None:
        """Driver: engine running in neutral, pull the brake, key out, exit, lock."""
        self.drive_in_and_park("N")
        w = self.world
        w.parking_brake = True
        self.run(0.5)
        w.key_on = False
        self.run(0.5)
        w.doors_closed = False
        self.run(1.0)
        w.doors_closed = True
        self.run(0.5)
        self.fob.press(FobCommand.LOCK)
        self.run(4.0)

    def press_lock(self, times: int = 3, interval: float = 0.6) -> None:
        for i in range(times):
            self.fob.press(FobCommand.LOCK)
            if i < times - 1:
                self.run(interval)
        self.run(self.dt)

    def records(self) -> list[str]:
        return [format_record(r) for r in self.log.recent()]
