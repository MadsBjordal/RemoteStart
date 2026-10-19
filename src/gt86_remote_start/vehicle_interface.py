"""Vehicle state abstraction (read side).

Every field is ``Optional``: ``None`` means *unknown* and is never treated as
safe. The Raspberry Pi only READS vehicle state. It has no ignition or
starter output at all: start and stop are requests to the independent safety
controller (``controller_protocol.py``), which alone drives the start
interface. This is a deliberate change from a VehicleInterface with
``request_start()``: the Pi cannot energise anything even if its software is
wrong.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from typing import Any

from .config import LIVE_OUTPUTS_PERMITTED
from .fault_codes import FaultCode


@dataclass(frozen=True)
class VehicleStatus:
    timestamp: float  # Pi monotonic time when sampled
    neutral_primary: bool | None = None
    neutral_secondary: bool | None = None
    parking_brake_applied: bool | None = None
    hood_closed: bool | None = None
    doors_closed: bool | None = None
    ignition_on: bool | None = None  # key in ON/START (driver), not remote hold
    engine_rpm: float | None = None
    engine_running_signal: bool | None = None
    vehicle_speed_kph: float | None = None
    battery_voltage: float | None = None
    coolant_temp_c: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class VehicleInterface(ABC):
    @abstractmethod
    async def read_vehicle_status(self) -> VehicleStatus:
        """Return the latest sample. Must not block for longer than one tick."""


class LiveOutputsLocked(RuntimeError):
    code = FaultCode.LIVE_OUTPUTS_LOCKED


class VehicleHardwareInterface(VehicleInterface):
    """Placeholder for the real input hardware.

    The inputs depend on vehicle-specific signals that are not verified yet
    (docs/vehicle_research.md, "Unresolved"). It refuses to construct until
    that work is done, so nothing can be wired to it by accident.
    """

    def __init__(self) -> None:
        if not LIVE_OUTPUTS_PERMITTED:
            raise LiveOutputsLocked(
                "Vehicle hardware interface is locked: vehicle signals are unverified"
            )
        raise NotImplementedError("Phase 7: implement after signal verification")

    async def read_vehicle_status(self) -> VehicleStatus:  # pragma: no cover
        raise NotImplementedError
