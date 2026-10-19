"""Timing behaviour of the whole Pi + controller loop in simulation."""

from gt86_remote_start.state_machine import State
from simulator.fault_injection import FaultInjector
from simulator.harness import SimulationHarness
from simulator.scenarios import _running


def test_runtime_limit_stops_engine():
    h, _ = _running()
    t0 = h.clock.monotonic()
    assert h.run_until(lambda: not h.world.engine.running, 120)
    elapsed = h.clock.monotonic() - t0
    assert 55 <= elapsed <= h.cfg.remote_start.runtime_seconds + 2


def test_crank_is_bounded_by_controller():
    h, f = SimulationHarness(), None
    h.boot()
    h.perform_reservation()
    h.run(4)
    FaultInjector(h).engine_wont_start()
    h.press_lock(3)
    crank_ticks = 0
    for _ in range(400):
        h.step()
        crank_ticks += h.world.starter_engaged
    assert crank_ticks * h.dt <= h.controller.params.max_crank_s + 0.1
    assert h.world.starter_engagements == 1


def test_no_start_if_sequence_overlaps_reservation_completion():
    """The lock that completes the reservation must not count toward a start."""
    h = SimulationHarness()
    h.boot()
    h.drive_in_and_park("N")
    w = h.world
    w.parking_brake = True
    h.run(0.5)
    w.key_on = False
    h.run(0.5)
    w.doors_closed = False
    h.run(0.5)
    w.doors_closed = True
    h.run(0.5)
    h.press_lock(3, interval=0.8)  # first press completes the reservation
    h.run(8)
    assert h.world.starter_engagements == 0


def test_reservation_procedure_times_out():
    h = SimulationHarness()
    h.boot()
    h.drive_in_and_park("N")
    h.world.parking_brake = True
    h.run(0.5)
    assert h.controller.core.reservation.name == "ARMED"
    h.run(200)  # driver never removes the key
    assert h.controller.core.reservation.name == "NONE"
    assert h.state is State.STANDBY


def test_shutdown_timeout_latches_when_engine_will_not_stop():
    h, f = _running()
    f.stuck_ignition_relay()  # ignition stays powered: the engine cannot be stopped
    h.run(10)  # past the activation cooldown, so the fob may request a stop
    h.press_lock(3)
    h.run(15)
    assert h.state is State.LOCKOUT
