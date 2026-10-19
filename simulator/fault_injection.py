"""Named fault injections for the simulation harness."""

from __future__ import annotations

from typing import Any

from .harness import SimulationHarness
from .simulated_vehicle import MISSING


class FaultInjector:
    def __init__(self, h: SimulationHarness) -> None:
        self.h = h

    # sensors ("pi", "controller" or "both" views)
    def sensor(self, name: str, value: Any, view: str = "both") -> None:
        if view in ("pi", "both"):
            self.h.world.pi_overrides[name] = value
        if view in ("controller", "both"):
            self.h.world.ctl_overrides[name] = value

    def sensor_missing(self, name: str, view: str = "both") -> None:
        self.sensor(name, MISSING, view)

    def clear_sensors(self) -> None:
        self.h.world.pi_overrides.clear()
        self.h.world.ctl_overrides.clear()

    # link
    def disconnect_link(self) -> None:
        self.h.controller.pi_to_ctl.connected = False
        self.h.controller.ctl_to_pi.connected = False

    def reconnect_link(self) -> None:
        self.h.controller.pi_to_ctl.connected = True
        self.h.controller.ctl_to_pi.connected = True

    def corrupt_link(self, on: bool = True) -> None:
        self.h.controller.pi_to_ctl.corrupt = on
        self.h.controller.ctl_to_pi.corrupt = on

    # Pi
    def crash_pi(self) -> None:
        self.h.crash_pi()

    def reboot_pi(self) -> None:
        self.h.reboot_pi()

    def pi_input_failure(self, on: bool = True) -> None:
        self.h.vehicle_if.fail_reads = on

    # controller
    def controller_power_cycle(self, off_seconds: float = 0.5) -> None:
        self.h.controller.power_loss()
        self.h.run(off_seconds)
        self.h.controller.power_restore()

    def controller_hang(self, seconds: float) -> None:
        self.h.controller.hang(seconds)

    # vehicle
    def battery(self, rest_voltage: float) -> None:
        self.h.world.rest_voltage = rest_voltage

    def engine_wont_start(self) -> None:
        self.h.world.engine.will_start = False

    def engine_stalls_after(self, seconds: float) -> None:
        self.h.world.engine.stall_after_s = seconds

    def vehicle_moves(self, kph: float) -> None:
        self.h.world.forced_speed = kph

    def stuck_ignition_relay(self) -> None:
        self.h.world.stuck_ignition_relay = True
