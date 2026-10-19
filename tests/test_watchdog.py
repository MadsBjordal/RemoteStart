"""Heartbeat, hardware-watchdog and restart behaviour."""

from gt86_remote_start.controller_protocol import ControllerFault
from gt86_remote_start.diagnostics import SERVICE_CLEAR_PHRASE
from gt86_remote_start.state_machine import State
from simulator.harness import SimulationHarness
from simulator.scenarios import _running


def test_controller_stops_run_within_heartbeat_timeout():
    h, f = _running()
    f.crash_pi()
    timeout = h.controller.params.heartbeat_timeout_s
    h.run(timeout + 0.2)
    assert not h.world.ctl_ignition
    assert h.controller.core.last_reason.name == "CONTROLLER_COMM_LOST"


def test_controller_hang_drops_outputs_via_hardware_watchdog():
    h, f = _running()
    f.controller_hang(1.0)
    h.run(0.2)
    assert not h.world.ctl_ignition
    h.run(2)
    assert h.controller.core.active & ControllerFault.WATCHDOG_RESET


def test_pi_boot_never_starts_engine():
    h = SimulationHarness()
    h.boot()
    h.perform_reservation()
    h.crash_pi()
    h.run(1)
    h.reboot_pi()
    h.run(10)
    assert h.world.starter_engagements == 0
    assert h.state is State.RESERVATION_VALID  # reservation kept by the controller


def test_pi_restart_during_run_requests_stop():
    h, f = _running()
    f.crash_pi()
    f.reboot_pi()  # restarts before the controller notices
    h.run(5)
    assert not h.world.engine.running
    assert h.state is State.RESERVATION_REQUIRED
    assert h.world.starter_engagements == 1


def test_initialization_times_out_without_controller():
    h = SimulationHarness()
    h.controller.power_loss()
    h.run(7)
    assert h.state is State.FAULT


def test_lockout_needs_service_reset_and_controller_service_input():
    h, f = _running()
    f.vehicle_moves(5)
    h.run(1)
    f.vehicle_moves(0)
    h.run(2)
    assert h.state is State.LOCKOUT
    assert not h.pi.service_reset("wrong words")
    assert h.state is State.LOCKOUT
    # Pi faults cleared, but the controller still holds its latch: back to LOCKOUT.
    assert h.pi.service_reset(SERVICE_CLEAR_PHRASE)
    h.run(3)
    assert h.state is State.LOCKOUT
    h.controller.service_input = True  # physical service switch on the controller
    h.run(0.5)
    assert h.pi.service_reset(SERVICE_CLEAR_PHRASE)
    h.controller.service_input = False
    h.run(5)
    assert h.state is State.RESERVATION_REQUIRED
