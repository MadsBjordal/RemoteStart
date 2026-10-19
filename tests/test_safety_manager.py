from dataclasses import replace

import pytest

from gt86_remote_start.config import SafetyConfig
from gt86_remote_start.controller_protocol import (
    ControllerFault,
    ControllerState,
    ControllerStatus,
    ReservationState,
    StatusFlag,
)
from gt86_remote_start.engine_monitor import EngineState
from gt86_remote_start.fault_codes import FaultCode as F
from gt86_remote_start.immobilizer_interface import ImmobilizerStatus
from gt86_remote_start.safety_manager import SafetyManager
from gt86_remote_start.vehicle_interface import VehicleStatus

NOW = 100.0
ALL_INPUTS = (
    StatusFlag.NEUTRAL_A | StatusFlag.NEUTRAL_B | StatusFlag.PARKING_BRAKE
    | StatusFlag.HOOD_CLOSED | StatusFlag.DOORS_CLOSED | StatusFlag.ENGINE_RUNNING
    | StatusFlag.MOVING | StatusFlag.KEY_ON
)


def good_status(**kw):
    base = VehicleStatus(
        timestamp=NOW, neutral_primary=True, neutral_secondary=True, parking_brake_applied=True,
        hood_closed=True, doors_closed=True, ignition_on=False, engine_rpm=0.0,
        engine_running_signal=False, vehicle_speed_kph=0.0, battery_voltage=12.6,
    )
    return replace(base, **kw)


def ctl(**kw):
    base = ControllerStatus(
        ControllerState.IDLE, ReservationState.VALID,
        StatusFlag.NEUTRAL_A | StatusFlag.NEUTRAL_B | StatusFlag.PARKING_BRAKE
        | StatusFlag.HOOD_CLOSED | StatusFlag.DOORS_CLOSED,
        ALL_INPUTS, ControllerFault(0), ControllerFault(0), 0, 12600, 0, 1,
    )
    return replace(base, **kw)


def decide(status=None, controller=None, link=True, engine=EngineState.STOPPED,
           immo=ImmobilizerStatus.READY, faults=()):
    return SafetyManager(SafetyConfig()).evaluate_start(
        status or good_status(), controller or ctl(), link, engine, immo, list(faults), NOW
    )


def test_all_good_allows_start():
    d = decide()
    assert d.allowed and d.reasons == []


@pytest.mark.parametrize(
    "kw, reason",
    [
        ({"neutral_primary": False, "neutral_secondary": False}, F.NEUTRAL_NOT_VERIFIED),
        ({"neutral_secondary": None}, F.SENSOR_UNKNOWN),
        ({"parking_brake_applied": False}, F.PARKING_BRAKE_NOT_SET),
        ({"parking_brake_applied": None}, F.PARKING_BRAKE_NOT_SET),
        ({"hood_closed": None}, F.HOOD_OPEN),
        ({"doors_closed": False}, F.DOOR_OPEN),
        ({"ignition_on": True}, F.IGNITION_ALREADY_ON),
        ({"vehicle_speed_kph": 3.0}, F.VEHICLE_NOT_STATIONARY),
        ({"battery_voltage": 11.5}, F.BATTERY_LOW),
        ({"battery_voltage": None}, F.SENSOR_UNKNOWN),
        ({"timestamp": NOW - 2}, F.STATUS_STALE),
    ],
)
def test_each_interlock_blocks(kw, reason):
    d = decide(good_status(**kw))
    assert not d.allowed and reason in d.reasons


def test_single_neutral_source_is_never_enough():
    d = decide(good_status(neutral_secondary=False))
    assert not d.allowed
    assert d.reasons[0] is F.SENSOR_CONTRADICTION


def test_pi_and_controller_disagree_is_contradiction():
    d = decide(controller=ctl(flags=ctl().flags & ~StatusFlag.PARKING_BRAKE))
    assert not d.allowed and d.contradictions


@pytest.mark.parametrize(
    "kwargs, reason",
    [
        ({"link": False}, F.CONTROLLER_COMM_LOST),
        ({"controller": ctl(reservation=ReservationState.DRIVER_EXITED)}, F.RESERVATION_NOT_VALID),
        ({"controller": ctl(latched_faults=ControllerFault.MOVEMENT)}, F.CONTROLLER_REPORTED_FAULT),
        ({"engine": EngineState.RUNNING}, F.ENGINE_ALREADY_RUNNING),
        ({"engine": EngineState.UNKNOWN}, F.SENSOR_UNKNOWN),
        ({"immo": ImmobilizerStatus.NOT_PRESENT}, F.IMMOBILIZER_NOT_READY),
        ({"faults": [F.ENGINE_MONITOR_LOST]}, F.ACTIVE_FAULT_PRESENT),
    ],
)
def test_system_conditions_block(kwargs, reason):
    d = decide(**kwargs)
    assert not d.allowed and reason in d.reasons


def test_running_low_voltage_needs_hold_time():
    sm = SafetyManager(SafetyConfig())
    s = good_status(battery_voltage=12.0)
    assert sm.evaluate_running(s, NOW).allowed
    later = replace(s, timestamp=NOW + 6)
    d = sm.evaluate_running(later, NOW + 6)
    assert F.BATTERY_LOW in d.reasons


def test_running_movement_is_first_reason():
    d = SafetyManager(SafetyConfig()).evaluate_running(good_status(vehicle_speed_kph=5), NOW)
    assert d.reasons[0] is F.VEHICLE_MOVEMENT
