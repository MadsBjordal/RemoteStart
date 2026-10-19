from gt86_remote_start.config import EngineConfig
from gt86_remote_start.engine_monitor import EngineEvent, EngineMonitor, EngineState
from gt86_remote_start.obd_pids import ObdDecodeError, decode_mode01_response
from gt86_remote_start.vehicle_interface import VehicleStatus

import pytest


def sample(clock, rpm, sig):
    return VehicleStatus(clock.monotonic(), engine_rpm=rpm, engine_running_signal=sig)


def feed(mon, clock, rpm, sig, seconds, dt=0.1):
    events = []
    for _ in range(round(seconds / dt)):
        clock.advance(dt)
        events += mon.update(sample(clock, rpm, sig))
    return events


def test_start_needs_both_signals_for_confirm_time(clock):
    mon = EngineMonitor(EngineConfig(), clock)
    feed(mon, clock, 0, False, 0.5)
    assert mon.state is EngineState.STOPPED
    mon.begin_start_attempt(5)
    assert feed(mon, clock, 250, False, 0.5) == []  # cranking is not running
    events = feed(mon, clock, 900, True, 1.2)
    assert EngineEvent.STARTED in events and mon.state is EngineState.RUNNING


def test_start_failure_after_timeout(clock):
    mon = EngineMonitor(EngineConfig(), clock)
    feed(mon, clock, 0, False, 0.3)
    mon.begin_start_attempt(2)
    events = feed(mon, clock, 250, False, 3)
    assert events.count(EngineEvent.START_FAILED) == 1


def test_stall_detected_during_run(clock):
    mon = EngineMonitor(EngineConfig(), clock)
    feed(mon, clock, 900, True, 1.5)
    mon.begin_run(600)
    assert EngineEvent.STALLED in feed(mon, clock, 0, False, 0.5)


def test_rpm_without_running_signal_is_implausible(clock):
    mon = EngineMonitor(EngineConfig(), clock)
    assert EngineEvent.IMPLAUSIBLE in feed(mon, clock, 2000, False, 1.5)
    assert mon.state is EngineState.UNKNOWN


def test_out_of_range_rpm_is_implausible(clock):
    mon = EngineMonitor(EngineConfig(), clock)
    clock.advance(0.1)
    assert mon.update(sample(clock, 20000, True)) == [EngineEvent.IMPLAUSIBLE]


def test_signal_loss_reported_once(clock):
    mon = EngineMonitor(EngineConfig(), clock)
    feed(mon, clock, 900, True, 1.5)
    events = feed(mon, clock, None, None, 3)
    assert events.count(EngineEvent.SIGNAL_LOST) == 1
    assert mon.state is EngineState.UNKNOWN


def test_runtime_exceeded(clock):
    mon = EngineMonitor(EngineConfig(), clock)
    feed(mon, clock, 900, True, 1.5)
    mon.begin_run(2)
    assert EngineEvent.RUNTIME_EXCEEDED in feed(mon, clock, 900, True, 3)


def test_obd_standard_pids():
    assert decode_mode01_response(bytes([0x41, 0x0C, 0x0B, 0xB8])) == ("engine_rpm", 750.0)
    assert decode_mode01_response(bytes([0x41, 0x05, 0x5A])) == ("coolant_temp_c", 50.0)
    assert decode_mode01_response(bytes([0x41, 0x42, 0x31, 0x2C])) == (
        "control_module_voltage", 12.588)
    with pytest.raises(ObdDecodeError):
        decode_mode01_response(bytes([0x41, 0x99, 0x00]))
