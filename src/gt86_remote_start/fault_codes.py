"""Fault and rejection reason codes.

One numeric code space is shared by Pi-side faults, start-rejection reasons
and the reason field of the safety-controller protocol, so a log line, a
protocol NACK and a diagnostic report all name the same thing.

Severity:
    INFO      - a normal refusal or event (e.g. reservation not valid).
    WARNING   - something went wrong but the system stays usable.
    CRITICAL  - the system must stop any remote run immediately.

Latching faults survive until an explicit service reset. They are reserved
for conditions where the cause is uncertain and a retry could be unsafe.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class Severity(IntEnum):
    INFO = 0
    WARNING = 1
    CRITICAL = 2


class FaultCode(IntEnum):
    NONE = 0
    # Configuration / platform
    CONFIG_INVALID = 1
    SELF_TEST_FAILED = 2
    LIVE_OUTPUTS_LOCKED = 3
    INTERNAL_ERROR = 4
    WATCHDOG_RESET = 5
    POWER_INTERRUPTED = 6
    REMOTE_START_DISABLED = 7
    # Safety controller link
    CONTROLLER_COMM_LOST = 20
    CONTROLLER_PROTOCOL_ERROR = 21
    CONTROLLER_REPORTED_FAULT = 22
    CONTROLLER_REJECTED_START = 23
    CONTROLLER_RESET = 24
    CONTROLLER_STATE_MISMATCH = 25
    START_REQUEST_EXPIRED = 26
    START_REQUEST_REPLAYED = 27
    # Start preconditions (rejection reasons)
    RESERVATION_NOT_VALID = 40
    NEUTRAL_NOT_VERIFIED = 41
    PARKING_BRAKE_NOT_SET = 42
    HOOD_OPEN = 43
    DOOR_OPEN = 44
    ENGINE_ALREADY_RUNNING = 45
    BATTERY_LOW = 46
    BATTERY_HIGH = 47
    VEHICLE_NOT_STATIONARY = 48
    IGNITION_ALREADY_ON = 49
    IMMOBILIZER_NOT_READY = 50
    STATUS_STALE = 51
    SENSOR_UNKNOWN = 52
    ACTIVE_FAULT_PRESENT = 53
    # Sensor plausibility
    SENSOR_CONTRADICTION = 60
    VEHICLE_MOVEMENT = 61
    ENGINE_SIGNAL_IMPLAUSIBLE = 62
    OUTPUT_FEEDBACK_MISMATCH = 63
    # Engine
    START_FAILED = 80
    ENGINE_STALLED = 81
    RUNTIME_LIMIT_REACHED = 82
    RUNTIME_EXCEEDED = 83
    ENGINE_MONITOR_LOST = 84
    ENGINE_STOP_UNCONFIRMED = 85
    UNEXPECTED_ENGINE_STATE = 86
    DRIVER_TOOK_OVER = 87
    USER_STOP = 88


@dataclass(frozen=True)
class FaultInfo:
    severity: Severity
    latching: bool
    description: str


_I, _W, _C = Severity.INFO, Severity.WARNING, Severity.CRITICAL

FAULT_INFO: dict[FaultCode, FaultInfo] = {
    FaultCode.NONE: FaultInfo(_I, False, "No fault"),
    FaultCode.CONFIG_INVALID: FaultInfo(_C, False, "Configuration failed validation"),
    FaultCode.SELF_TEST_FAILED: FaultInfo(_C, False, "Start-up self test failed"),
    FaultCode.LIVE_OUTPUTS_LOCKED: FaultInfo(
        _C, False, "Live vehicle outputs are locked out in this release"
    ),
    FaultCode.INTERNAL_ERROR: FaultInfo(_C, True, "Unexpected software error"),
    FaultCode.WATCHDOG_RESET: FaultInfo(_W, False, "Recovered from a watchdog reset"),
    FaultCode.POWER_INTERRUPTED: FaultInfo(_W, False, "Power was interrupted"),
    FaultCode.REMOTE_START_DISABLED: FaultInfo(_I, False, "Remote start disabled in config"),
    FaultCode.CONTROLLER_COMM_LOST: FaultInfo(_C, False, "No valid status from safety controller"),
    FaultCode.CONTROLLER_PROTOCOL_ERROR: FaultInfo(_W, False, "Corrupt or unexpected frame"),
    FaultCode.CONTROLLER_REPORTED_FAULT: FaultInfo(
        _C, True, "Safety controller reports a latched fault"
    ),
    FaultCode.CONTROLLER_REJECTED_START: FaultInfo(_I, False, "Safety controller refused start"),
    FaultCode.CONTROLLER_RESET: FaultInfo(_W, False, "Safety controller restarted"),
    FaultCode.CONTROLLER_STATE_MISMATCH: FaultInfo(
        _C, False, "Safety controller state disagrees with expected state"
    ),
    FaultCode.START_REQUEST_EXPIRED: FaultInfo(_I, False, "Start authorization expired"),
    FaultCode.START_REQUEST_REPLAYED: FaultInfo(_W, False, "Start request nonce reused"),
    FaultCode.RESERVATION_NOT_VALID: FaultInfo(_I, False, "Reservation mode not established"),
    FaultCode.NEUTRAL_NOT_VERIFIED: FaultInfo(_I, False, "Neutral not verified by both sources"),
    FaultCode.PARKING_BRAKE_NOT_SET: FaultInfo(_I, False, "Parking brake not applied"),
    FaultCode.HOOD_OPEN: FaultInfo(_I, False, "Hood open or hood switch unknown"),
    FaultCode.DOOR_OPEN: FaultInfo(_I, False, "A door is open"),
    FaultCode.ENGINE_ALREADY_RUNNING: FaultInfo(_I, False, "Engine already running"),
    FaultCode.BATTERY_LOW: FaultInfo(_W, False, "Battery voltage below limit"),
    FaultCode.BATTERY_HIGH: FaultInfo(_W, False, "Battery voltage above limit"),
    FaultCode.VEHICLE_NOT_STATIONARY: FaultInfo(_I, False, "Vehicle speed not zero"),
    FaultCode.IGNITION_ALREADY_ON: FaultInfo(_I, False, "Ignition already on (driver in control)"),
    FaultCode.IMMOBILIZER_NOT_READY: FaultInfo(
        _I, False, "No authorized immobilizer interface ready"
    ),
    FaultCode.STATUS_STALE: FaultInfo(_W, False, "Vehicle status is too old"),
    FaultCode.SENSOR_UNKNOWN: FaultInfo(_W, False, "A required sensor value is unknown"),
    FaultCode.ACTIVE_FAULT_PRESENT: FaultInfo(_I, False, "An active fault blocks starting"),
    FaultCode.SENSOR_CONTRADICTION: FaultInfo(_C, True, "Sensors contradict each other"),
    FaultCode.VEHICLE_MOVEMENT: FaultInfo(_C, True, "Unexpected vehicle movement"),
    FaultCode.ENGINE_SIGNAL_IMPLAUSIBLE: FaultInfo(_C, True, "Engine signals implausible"),
    FaultCode.OUTPUT_FEEDBACK_MISMATCH: FaultInfo(
        _C, True, "Output feedback does not match command (stuck relay?)"
    ),
    FaultCode.START_FAILED: FaultInfo(_W, False, "Engine did not start; no automatic retry"),
    FaultCode.ENGINE_STALLED: FaultInfo(_W, False, "Engine stopped unexpectedly"),
    FaultCode.RUNTIME_LIMIT_REACHED: FaultInfo(_I, False, "Configured runtime elapsed"),
    FaultCode.RUNTIME_EXCEEDED: FaultInfo(_C, False, "Engine ran beyond permitted runtime"),
    FaultCode.ENGINE_MONITOR_LOST: FaultInfo(_C, False, "Engine monitoring signal lost"),
    FaultCode.ENGINE_STOP_UNCONFIRMED: FaultInfo(
        _C, True, "Engine stop could not be confirmed"
    ),
    FaultCode.UNEXPECTED_ENGINE_STATE: FaultInfo(_W, False, "Unexpected engine state"),
    FaultCode.DRIVER_TOOK_OVER: FaultInfo(_I, False, "Driver turned the key; remote run ended"),
    FaultCode.USER_STOP: FaultInfo(_I, False, "Stop requested by key fob"),
}


def info(code: FaultCode) -> FaultInfo:
    return FAULT_INFO[code]


def code_from_int(value: int) -> FaultCode:
    """Decode a reason number from the wire; unknown numbers are an error."""
    try:
        return FaultCode(value)
    except ValueError:
        return FaultCode.CONTROLLER_PROTOCOL_ERROR
