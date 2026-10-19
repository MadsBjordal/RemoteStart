"""Simulated vehicle: the physical truth, plus the sensor views derived from it.

Two sensor views exist, as in the real design: what the Pi reads (its own
interface) and what the safety controller reads (its own independent inputs).
Fault injection overrides either view separately. ``MISSING`` makes a value
unknown (``None``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from gt86_remote_start.clock import Clock
from gt86_remote_start.vehicle_interface import VehicleInterface, VehicleStatus
from sc_model import ScInputs

from .simulated_engine import SimulatedEngine

MISSING = object()


@dataclass
class VehicleWorld:
    clock: Clock
    gear: str = "N"  # "N" or a gear like "1", "R"
    clutch_pressed: bool = False
    parking_brake: bool = True
    hood_closed: bool = True
    doors_closed: bool = True
    key_on: bool = False
    rest_voltage: float = 12.6
    immobilizer_module_ready: bool = True  # authorised commercial interface fitted
    engine: SimulatedEngine = field(default_factory=SimulatedEngine)
    speed_kph: float = 0.0
    ctl_ignition: bool = False
    ctl_starter: bool = False
    stuck_ignition_relay: bool = False
    pi_overrides: dict[str, Any] = field(default_factory=dict)
    ctl_overrides: dict[str, Any] = field(default_factory=dict)
    forced_speed: float | None = None
    _lurch_time: float = 0.0
    _prev_running: bool = False
    _moved_ever: bool = False
    starter_engagements: int = 0
    _prev_starter: bool = False
    max_speed: float = 0.0

    # -- physics -------------------------------------------------------------
    @property
    def ignition_powered(self) -> bool:
        return self.key_on or self.ctl_ignition or self.stuck_ignition_relay

    @property
    def starter_engaged(self) -> bool:
        # The remote start interface cranks without the clutch pedal (its
        # interlock handling is exactly why independent safety is needed).
        return self.ctl_starter and self.ignition_powered

    @property
    def battery_voltage(self) -> float:
        if self.starter_engaged:
            return self.rest_voltage - 2.6
        if self.engine.running:
            return self.rest_voltage + 1.6
        return self.rest_voltage

    def update(self, dt: float) -> None:
        if self.starter_engaged and not self._prev_starter:
            self.starter_engagements += 1
        self._prev_starter = self.starter_engaged
        in_gear = self.gear != "N" and not self.clutch_pressed
        immo_ok = self.key_on or (self.ctl_ignition and self.immobilizer_module_ready)
        lurch = in_gear and (self.starter_engaged or self.engine.running)
        self.engine.update(dt, self.ignition_powered, self.starter_engaged, immo_ok, lurch)
        if lurch:
            self._lurch_time = 0.6
        if self.forced_speed is not None:
            self.speed_kph = self.forced_speed
        elif self._lurch_time > 0:
            self._lurch_time -= dt
            self.speed_kph = 3.0
        else:
            self.speed_kph = 0.0
        self.max_speed = max(self.max_speed, self.speed_kph)
        self._moved_ever |= self.speed_kph > 0.3

    @property
    def moved_ever(self) -> bool:
        return self._moved_ever

    # -- sensor views ----------------------------------------------------------
    def _truth(self) -> dict[str, Any]:
        return {
            "neutral_a": self.gear == "N",
            "neutral_b": self.gear == "N",
            "parking_brake": self.parking_brake,
            "hood_closed": self.hood_closed,
            "doors_closed": self.doors_closed,
            "key_on": self.key_on,
            "engine_rpm": self.engine.rpm,
            "engine_running": self.engine.running,
            "speed_kph": self.speed_kph,
            "battery_v": self.battery_voltage,
            "ignition_feedback": self.ctl_ignition or self.stuck_ignition_relay,
            "starter_feedback": self.ctl_starter,
        }

    def _view(self, overrides: dict[str, Any]) -> dict[str, Any]:
        out = self._truth()
        for key, value in overrides.items():
            out[key] = None if value is MISSING else value
        return out

    def pi_status(self) -> VehicleStatus:
        v = self._view(self.pi_overrides)
        return VehicleStatus(
            timestamp=self.clock.monotonic(),
            neutral_primary=v["neutral_a"],
            neutral_secondary=v["neutral_b"],
            parking_brake_applied=v["parking_brake"],
            hood_closed=v["hood_closed"],
            doors_closed=v["doors_closed"],
            ignition_on=v["key_on"],
            engine_rpm=v["engine_rpm"],
            engine_running_signal=v["engine_running"],
            vehicle_speed_kph=v["speed_kph"],
            battery_voltage=v["battery_v"],
            coolant_temp_c=20.0,
        )

    def controller_inputs(self, lock_pulse: bool, service_input: bool = False) -> ScInputs:
        v = self._view(self.ctl_overrides)
        speed = v["speed_kph"]
        mv = v["battery_v"]
        return ScInputs(
            neutral_a=v["neutral_a"],
            neutral_b=v["neutral_b"],
            parking_brake=v["parking_brake"],
            hood_closed=v["hood_closed"],
            doors_closed=v["doors_closed"],
            engine_running=v["engine_running"],
            moving=None if speed is None else speed > 0.3,
            key_on=v["key_on"],
            lock_pulse=lock_pulse,
            battery_mv=None if mv is None else int(mv * 1000),
            ignition_feedback=v["ignition_feedback"],
            starter_feedback=v["starter_feedback"],
            service_input=service_input,
        )


class SimulatedVehicleInterface(VehicleInterface):
    def __init__(self, world: VehicleWorld) -> None:
        self.world = world
        self.fail_reads = False

    async def read_vehicle_status(self) -> VehicleStatus:
        if self.fail_reads:
            raise OSError("simulated input interface failure")
        return self.world.pi_status()
