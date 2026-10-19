"""Direct tests of the safety-controller reference model (firmware spec)."""

from gt86_remote_start.controller_protocol import (
    ControllerState,
    Frame,
    MsgType,
    ReservationState,
    StartRequest,
    StartResponse,
)
from gt86_remote_start.fault_codes import FaultCode
from sc_model import SafetyCore, ScInputs, ScParams

PARKED = dict(neutral_a=True, neutral_b=True, parking_brake=True, hood_closed=True,
              doors_closed=True, engine_running=False, moving=False, key_on=False,
              battery_mv=12600, ignition_feedback=False, starter_feedback=False)


def hb(core, now):
    core.receive(now, Frame(MsgType.HEARTBEAT, 1, b"\x00" * 5))


def start(core, now, nonce=1):
    out = core.receive(now, Frame(MsgType.START_REQUEST, 2, StartRequest(nonce, 3000, 600).pack()))
    return StartResponse.unpack(out[0][1])


def test_power_on_defaults_safe():
    core = SafetyCore(ScParams())
    assert core.out.ignition is False and core.out.starter is False
    assert core.reservation is ReservationState.NONE
    out = core.step(0.1, ScInputs())  # all inputs unknown
    assert not out.ignition and not out.starter


def test_start_refused_without_reservation():
    core = SafetyCore(ScParams())
    hb(core, 1.0)
    core.step(1.0, ScInputs(**PARKED))
    r = start(core, 1.0)
    assert not r.accepted and r.reason == FaultCode.RESERVATION_NOT_VALID


def test_start_refused_without_heartbeat():
    core = SafetyCore(ScParams())
    core.step(1.0, ScInputs(**PARKED))
    core.reservation = ReservationState.VALID
    r = start(core, 5.0)
    assert not r.accepted and r.reason == FaultCode.CONTROLLER_COMM_LOST


def test_nonce_cannot_be_replayed():
    core = SafetyCore(ScParams())
    hb(core, 1.0)
    core.step(1.0, ScInputs(**PARKED))
    core.reservation = ReservationState.VALID
    assert start(core, 1.0, nonce=9).accepted
    core.state = ControllerState.IDLE
    core.reservation = ReservationState.VALID
    r = start(core, 1.1, nonce=9)
    assert not r.accepted and r.reason == FaultCode.START_REQUEST_REPLAYED


def test_unknown_input_refuses_start():
    core = SafetyCore(ScParams())
    hb(core, 1.0)
    core.step(1.0, ScInputs(**{**PARKED, "hood_closed": None}))
    core.reservation = ReservationState.VALID
    r = start(core, 1.0)
    assert not r.accepted


def test_latched_fault_survives_power_cycle():
    nvm: dict = {}
    core = SafetyCore(ScParams(), nvm)
    core.step(1.0, ScInputs(**{**PARKED, "neutral_b": False}))
    core.step(2.0, ScInputs(**{**PARKED, "neutral_b": False}))
    assert core.state is ControllerState.FAULT_LATCHED
    core2 = SafetyCore(ScParams(), nvm)
    core2.step(3.0, ScInputs(**PARKED))
    assert core2.state is ControllerState.FAULT_LATCHED
    hb(core2, 3.0)
    assert not start(core2, 3.0).accepted
