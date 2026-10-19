"""Pi-side safety pre-checks.

These checks are a *second opinion*, never the authority. The independent
safety controller re-checks everything from its own inputs and can refuse.
The Pi checks so it can refuse early, explain why, and catch disagreement
between its view of the vehicle and the controller's view.

Rules: unknown is never safe; one neutral reading is never enough; the two
neutral sources must agree; any disagreement is a contradiction fault.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import SafetyConfig
from .controller_protocol import ControllerState, ControllerStatus, ReservationState, StatusFlag
from .engine_monitor import EngineState
from .fault_codes import FaultCode
from .immobilizer_interface import ImmobilizerStatus
from .vehicle_interface import VehicleStatus


@dataclass
class SafetyDecision:
    allowed: bool
    reasons: list[FaultCode] = field(default_factory=list)
    contradictions: list[str] = field(default_factory=list)

    @property
    def primary_reason(self) -> FaultCode:
        return self.reasons[0] if self.reasons else FaultCode.NONE


class SafetyManager:
    def __init__(self, cfg: SafetyConfig) -> None:
        self._cfg = cfg
        self._low_voltage_since: float | None = None

    def _common(
        self, s: VehicleStatus, now: float, reasons: list[FaultCode], contradictions: list[str]
    ) -> None:
        c = self._cfg
        if now - s.timestamp > c.max_status_age_seconds:
            reasons.append(FaultCode.STATUS_STALE)
        a, b = s.neutral_primary, s.neutral_secondary
        if a is None or b is None:
            reasons.append(FaultCode.SENSOR_UNKNOWN)
            reasons.append(FaultCode.NEUTRAL_NOT_VERIFIED)
        elif a != b:
            contradictions.append("neutral sources disagree")
            reasons.append(FaultCode.NEUTRAL_NOT_VERIFIED)
        elif not a:
            reasons.append(FaultCode.NEUTRAL_NOT_VERIFIED)
        if s.parking_brake_applied is not True:
            reasons.append(FaultCode.PARKING_BRAKE_NOT_SET)
        if s.hood_closed is not True:
            reasons.append(FaultCode.HOOD_OPEN)
        if s.doors_closed is not True:
            reasons.append(FaultCode.DOOR_OPEN)
        if s.vehicle_speed_kph is None:
            reasons.append(FaultCode.SENSOR_UNKNOWN)
        elif s.vehicle_speed_kph > c.stationary_speed_kph:
            reasons.append(FaultCode.VEHICLE_NOT_STATIONARY)
        if s.battery_voltage is None:
            reasons.append(FaultCode.SENSOR_UNKNOWN)

    def _cross_check(self, s: VehicleStatus, ctl: ControllerStatus, out: list[str]) -> None:
        pairs = (
            (s.neutral_primary, ctl.has(StatusFlag.NEUTRAL_A), "neutral A"),
            (s.neutral_secondary, ctl.has(StatusFlag.NEUTRAL_B), "neutral B"),
            (s.parking_brake_applied, ctl.has(StatusFlag.PARKING_BRAKE), "parking brake"),
            (s.hood_closed, ctl.has(StatusFlag.HOOD_CLOSED), "hood"),
        )
        for pi_view, ctl_view, name in pairs:
            if pi_view is not None and ctl_view is not None and pi_view != ctl_view:
                out.append(f"Pi and controller disagree on {name}")

    def evaluate_start(
        self,
        s: VehicleStatus,
        ctl: ControllerStatus | None,
        controller_link_ok: bool,
        engine_state: EngineState,
        immobilizer: ImmobilizerStatus,
        blocking_faults: list[FaultCode],
        now: float,
    ) -> SafetyDecision:
        reasons: list[FaultCode] = []
        contradictions: list[str] = []
        c = self._cfg
        if blocking_faults:
            reasons.append(FaultCode.ACTIVE_FAULT_PRESENT)
        if not controller_link_ok or ctl is None:
            reasons.append(FaultCode.CONTROLLER_COMM_LOST)
        else:
            if ctl.reservation is not ReservationState.VALID:
                reasons.append(FaultCode.RESERVATION_NOT_VALID)
            if ctl.state is not ControllerState.IDLE:
                reasons.append(FaultCode.CONTROLLER_STATE_MISMATCH)
            if ctl.latched_faults:
                reasons.append(FaultCode.CONTROLLER_REPORTED_FAULT)
            self._cross_check(s, ctl, contradictions)
        self._common(s, now, reasons, contradictions)
        if s.ignition_on is not False:
            reasons.append(
                FaultCode.SENSOR_UNKNOWN if s.ignition_on is None else FaultCode.IGNITION_ALREADY_ON
            )
        if engine_state is not EngineState.STOPPED:
            reasons.append(
                FaultCode.SENSOR_UNKNOWN
                if engine_state is EngineState.UNKNOWN
                else FaultCode.ENGINE_ALREADY_RUNNING
            )
        if s.battery_voltage is not None:
            if s.battery_voltage < c.min_start_voltage:
                reasons.append(FaultCode.BATTERY_LOW)
            elif s.battery_voltage > c.max_voltage:
                reasons.append(FaultCode.BATTERY_HIGH)
        if immobilizer is not ImmobilizerStatus.READY:
            reasons.append(FaultCode.IMMOBILIZER_NOT_READY)
        if contradictions:
            reasons.insert(0, FaultCode.SENSOR_CONTRADICTION)
        return SafetyDecision(not reasons, _unique(reasons), contradictions)

    def evaluate_running(self, s: VehicleStatus, now: float) -> SafetyDecision:
        """Conditions that must end a remote run. Unknown values end it too."""
        reasons: list[FaultCode] = []
        contradictions: list[str] = []
        self._common(s, now, reasons, contradictions)
        if s.vehicle_speed_kph is not None and s.vehicle_speed_kph > self._cfg.stationary_speed_kph:
            reasons.insert(0, FaultCode.VEHICLE_MOVEMENT)
        v = s.battery_voltage
        if v is not None and v < self._cfg.min_running_voltage:
            self._low_voltage_since = self._low_voltage_since or now
            if now - self._low_voltage_since >= self._cfg.low_voltage_hold_seconds:
                reasons.append(FaultCode.BATTERY_LOW)
        else:
            self._low_voltage_since = None
        if v is not None and v > self._cfg.max_voltage:
            reasons.append(FaultCode.BATTERY_HIGH)
        if contradictions:
            reasons.insert(0, FaultCode.SENSOR_CONTRADICTION)
        return SafetyDecision(not reasons, _unique(reasons), contradictions)


def _unique(codes: list[FaultCode]) -> list[FaultCode]:
    seen: list[FaultCode] = []
    for c in codes:
        if c not in seen:
            seen.append(c)
    return seen
