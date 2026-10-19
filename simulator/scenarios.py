"""The simulation scenarios from the project brief, each with its expected
safe state. Used by tests/test_fault_injection.py and by run_demo.py.

A scenario passing proves only that the *software model* behaves as
specified in simulation. It proves nothing about real hardware.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from gt86_remote_start.fault_codes import FaultCode
from gt86_remote_start.fob_listener import FobCommand, LockEvent
from gt86_remote_start.state_machine import State

from .fault_injection import FaultInjector
from .harness import SimulationHarness

S = State


@dataclass
class ScenarioResult:
    number: int
    name: str
    expected: str
    checks: list[tuple[str, bool]] = field(default_factory=list)
    log: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(ok for _, ok in self.checks)

    def check(self, description: str, ok: bool) -> None:
        self.checks.append((description, bool(ok)))


def _armed(echo: bool = False) -> tuple[SimulationHarness, FaultInjector]:
    """Booted system with a valid reservation, waiting > the press window."""
    h = SimulationHarness(echo=echo)
    h.boot()
    h.perform_reservation()
    h.run(4.0)
    return h, FaultInjector(h)


def _running(echo: bool = False) -> tuple[SimulationHarness, FaultInjector]:
    h, f = _armed(echo)
    h.press_lock(3)
    h.run_until(lambda: h.state is S.ENGINE_RUNNING, 10)
    return h, f


def _no_start(r: ScenarioResult, h: SimulationHarness) -> None:
    r.check("starter never engaged", h.world.starter_engagements == 0)
    r.check("engine not running", not h.world.engine.running)
    r.check("vehicle never moved", not h.world.moved_ever)


def _stopped_safely(r: ScenarioResult, h: SimulationHarness) -> None:
    r.check("engine stopped", not h.world.engine.running)
    r.check("controller outputs off", not (h.world.ctl_ignition or h.world.ctl_starter))
    r.check("starter engaged at most once", h.world.starter_engagements <= 1)


def s01_valid_triple_lock(echo: bool = False) -> ScenarioResult:
    r = ScenarioResult(1, "Valid triple-lock request", "Engine runs, stops at runtime limit, "
                       "returns to RESERVATION_REQUIRED (reservation used once)")
    h, _ = _running(echo)
    r.check("engine running under remote control", h.state is S.ENGINE_RUNNING)
    r.check("starter engaged exactly once", h.world.starter_engagements == 1)
    h.run(h.cfg.remote_start.runtime_seconds + 5)
    _stopped_safely(r, h)
    r.check("state RESERVATION_REQUIRED", h.state is S.RESERVATION_REQUIRED)
    h.run(4.0)
    h.press_lock(3)
    h.run(6.0)
    r.check("second triple lock does not restart (reservation consumed)",
            h.world.starter_engagements == 1)
    r.log = h.records()
    return r


def s02_double_press(echo: bool = False) -> ScenarioResult:
    r = ScenarioResult(2, "Accidental double press", "No start; stays RESERVATION_VALID")
    h, _ = _armed(echo)
    h.press_lock(2)
    h.run(6.0)
    _no_start(r, h)
    r.check("state RESERVATION_VALID", h.state is S.RESERVATION_VALID)
    r.log = h.records()
    return r


def s03_slow_presses(echo: bool = False) -> ScenarioResult:
    r = ScenarioResult(3, "Excessive delay between presses", "No start; sequence expires")
    h, _ = _armed(echo)
    h.press_lock(3, interval=1.8)
    h.run(6.0)
    _no_start(r, h)
    r.check("state RESERVATION_VALID", h.state is S.RESERVATION_VALID)
    r.log = h.records()
    return r


def s04_duplicate_events(echo: bool = False) -> ScenarioResult:
    r = ScenarioResult(4, "Duplicate receiver events", "One press repeated counts once; no start")
    h, _ = _armed(echo)
    first = h.fob.press(FobCommand.LOCK)
    h.fob.inject(first)  # same event id twice
    h.fob.inject(LockEvent(FobCommand.LOCK, h.clock.monotonic(), True, "echo-1", "bounce"))
    h.run(0.05)
    h.fob.inject(LockEvent(FobCommand.LOCK, h.clock.monotonic(), True, "echo-2", "bounce"))
    h.fob.inject(LockEvent(FobCommand.LOCK, h.clock.monotonic(), False, None, "door switch"))
    h.run(6.0)
    _no_start(r, h)
    r.log = h.records()
    return r


def s05_parked_in_gear(echo: bool = False) -> ScenarioResult:
    r = ScenarioResult(5, "Vehicle parked in gear", "Reservation cannot be established; no crank. "
                       "A neutral sensor stuck at 'neutral' is caught by the second source")
    h = SimulationHarness(echo=echo)
    f = FaultInjector(h)
    h.boot()
    h.drive_in_and_park("1")
    w = h.world
    w.parking_brake = True
    h.run(0.5)
    w.key_on = False
    w.engine.running = False
    w.clutch_pressed = False
    h.run(1.0)
    w.doors_closed = False
    h.run(0.5)
    w.doors_closed = True
    h.fob.press(FobCommand.LOCK)
    h.run(5.0)
    h.press_lock(3)
    h.run(6.0)
    _no_start(r, h)
    r.check("reservation never valid", h.controller.core.reservation.name == "NONE")
    # Part b: in gear, but neutral sensor A stuck reporting neutral.
    f.sensor("neutral_a", True)
    h.run(2.0)
    r.check("stuck neutral sensor -> contradiction lockout", h.state is S.LOCKOUT)
    r.check("controller latched sensor contradiction",
            bool(h.controller.core.latched))
    _no_start(r, h)
    r.log = h.records()
    return r


def s06_brake_released(echo: bool = False) -> ScenarioResult:
    r = ScenarioResult(6, "Parking brake released", "Reservation cancelled; triple lock refused")
    h, _ = _armed(echo)
    h.world.parking_brake = False
    h.run(1.0)
    h.press_lock(3)
    h.run(6.0)
    _no_start(r, h)
    r.check("state RESERVATION_REQUIRED", h.state is S.RESERVATION_REQUIRED)
    r.log = h.records()
    return r


def s07_hood_opened(echo: bool = False) -> ScenarioResult:
    r = ScenarioResult(7, "Hood opened", "Reservation cancelled; and a run stops if hood opens")
    h, _ = _armed(echo)
    h.world.hood_closed = False
    h.run(1.0)
    h.press_lock(3)
    h.run(6.0)
    _no_start(r, h)
    h2, _ = _running(echo)
    h2.world.hood_closed = False
    h2.run(2.0)
    _stopped_safely(r, h2)
    r.log = h.records() + ["--- run interrupted by hood ---"] + h2.records()
    return r


def s08_door_after_reservation(echo: bool = False) -> ScenarioResult:
    r = ScenarioResult(8, "Door opened after reservation", "Reservation cancelled; no start")
    h, _ = _armed(echo)
    h.world.doors_closed = False
    h.run(1.0)
    h.world.doors_closed = True
    h.run(1.0)
    h.press_lock(3)
    h.run(6.0)
    _no_start(r, h)
    r.check("state RESERVATION_REQUIRED", h.state is S.RESERVATION_REQUIRED)
    r.log = h.records()
    return r


def s09_engine_already_running(echo: bool = False) -> ScenarioResult:
    r = ScenarioResult(9, "Engine already running", "No starter operation; STANDBY")
    h = SimulationHarness(echo=echo)
    h.boot()
    h.drive_in_and_park("N")
    h.world.parking_brake = True
    h.run(4.0)
    h.press_lock(3)
    h.run(6.0)
    r.check("starter never engaged", h.world.starter_engagements == 0)
    r.check("state STANDBY (driver in control)", h.state in (S.STANDBY, S.RESERVATION_VALID)
            and h.state is S.STANDBY)
    r.log = h.records()
    return r


def s10_engine_fails_to_start(echo: bool = False) -> ScenarioResult:
    r = ScenarioResult(10, "Engine fails to start", "One bounded crank, no retry, "
                       "RESERVATION_REQUIRED, START_FAILED logged")
    h, f = _armed(echo)
    f.engine_wont_start()
    h.press_lock(3)
    h.run(20.0)
    r.check("starter engaged exactly once", h.world.starter_engagements == 1)
    _stopped_safely(r, h)
    r.check("state RESERVATION_REQUIRED", h.state is S.RESERVATION_REQUIRED)
    r.check("START_FAILED raised", h.faults.is_active(FaultCode.START_FAILED))
    r.log = h.records()
    return r


def s11_battery_voltage(echo: bool = False) -> ScenarioResult:
    r = ScenarioResult(11, "Battery voltage drops", "Low battery refuses start; a run with failing "
                       "charging voltage is shut down")
    h, f = _armed(echo)
    f.battery(11.6)
    h.run(1.0)
    h.press_lock(3)
    h.run(6.0)
    _no_start(r, h)
    h2, f2 = _running(echo)
    f2.battery(10.6)  # running voltage now 12.2 V: charging has failed
    h2.run(8.0)
    _stopped_safely(r, h2)
    r.check("run ended in RESERVATION_REQUIRED", h2.state is S.RESERVATION_REQUIRED)
    r.log = h.records() + ["--- run with failing charge voltage ---"] + h2.records()
    return r


def s12_controller_disconnects(echo: bool = False) -> ScenarioResult:
    r = ScenarioResult(12, "Safety controller disconnects", "Controller stops the engine on "
                       "heartbeat loss; Pi FAULT; recovers to RESERVATION_REQUIRED, no restart")
    h, f = _running(echo)
    f.disconnect_link()
    h.run(2.0)
    _stopped_safely(r, h)
    r.check("Pi in FAULT", h.state is S.FAULT)
    f.reconnect_link()
    h.run(15.0)
    r.check("recovered to RESERVATION_REQUIRED", h.state is S.RESERVATION_REQUIRED)
    r.check("no second crank", h.world.starter_engagements == 1)
    r.log = h.records()
    return r


def s13_pi_crash(echo: bool = False) -> ScenarioResult:
    r = ScenarioResult(13, "Raspberry Pi crashes", "Controller stops engine (heartbeat loss); "
                       "rebooted Pi never starts the engine")
    h, f = _running(echo)
    f.crash_pi()
    h.run(2.0)
    _stopped_safely(r, h)
    f.reboot_pi()
    h.run(10.0)
    r.check("rebooted Pi in RESERVATION_REQUIRED", h.state is S.RESERVATION_REQUIRED)
    r.check("no second crank after reboot", h.world.starter_engagements == 1)
    r.log = h.records()
    return r


def s14_link_corruption(echo: bool = False) -> ScenarioResult:
    r = ScenarioResult(14, "GPIO / serial communication failure", "Corrupt frames rejected by CRC; "
                       "treated as link loss: engine stopped, Pi FAULT")
    h, f = _running(echo)
    f.corrupt_link()
    h.run(2.0)
    _stopped_safely(r, h)
    r.check("Pi in FAULT", h.state is S.FAULT)
    dec = h.controller._decoder
    r.check("controller rejected the corrupt frames (CRC/format)",
            dec.crc_errors + dec.format_errors > 0)
    h3, f3 = _armed(echo)
    f3.pi_input_failure()
    h3.press_lock(3)
    h3.run(6.0)
    _no_start(r, h3)
    r.log = h.records() + ["--- Pi input interface failing ---"] + h3.records()
    return r


def s15_engine_signal_lost(echo: bool = False) -> ScenarioResult:
    r = ScenarioResult(15, "Engine monitoring signal disappears", "Pi requests stop and goes to "
                       "FAULT; engine stopped")
    h, f = _running(echo)
    f.sensor_missing("engine_rpm", view="pi")
    h.run(3.0)
    _stopped_safely(r, h)
    r.check("Pi in FAULT", h.state is S.FAULT)
    r.check("ENGINE_MONITOR_LOST raised", h.faults.is_active(FaultCode.ENGINE_MONITOR_LOST))
    r.log = h.records()
    return r


def s16_vehicle_movement(echo: bool = False) -> ScenarioResult:
    r = ScenarioResult(16, "Unexpected vehicle movement", "Immediate stop; latched LOCKOUT on Pi "
                       "and controller")
    h, f = _running(echo)
    f.vehicle_moves(4.0)
    h.run(0.2)
    r.check("controller outputs off within 0.2 s",
            not (h.world.ctl_ignition or h.world.ctl_starter))
    f.vehicle_moves(0.0)
    h.run(2.0)
    _stopped_safely(r, h)
    r.check("Pi in LOCKOUT", h.state is S.LOCKOUT)
    r.check("controller latched", bool(h.controller.core.latched))
    h.run(4.0)
    h.press_lock(3)
    h.run(6.0)
    r.check("no restart while locked out", h.world.starter_engagements == 1)
    r.log = h.records()
    return r


def s17_contradictory_sensors(echo: bool = False) -> ScenarioResult:
    r = ScenarioResult(17, "Sensors report contradictory information", "Pi sees neutral sources "
                       "disagree at the safety check: LOCKOUT, no crank")
    h, f = _armed(echo)
    f.sensor("neutral_b", False, view="pi")
    h.press_lock(3)
    h.run(6.0)
    _no_start(r, h)
    r.check("Pi in LOCKOUT", h.state is S.LOCKOUT)
    r.log = h.records()
    return r


def s18_controller_restart(echo: bool = False) -> ScenarioResult:
    r = ScenarioResult(18, "Controller restarts during an active sequence", "Outputs drop, "
                       "reservation lost, no restart; also after a watchdog reset during crank")
    h, f = _running(echo)
    f.controller_power_cycle(0.3)
    h.run(5.0)
    _stopped_safely(r, h)
    r.check("Pi in RESERVATION_REQUIRED", h.state is S.RESERVATION_REQUIRED)
    r.check("reservation lost", h.controller.core.reservation.name == "NONE")
    h2, f2 = _armed(echo)
    h2.press_lock(3)
    h2.run_until(lambda: h2.world.ctl_starter, 5)
    f2.controller_hang(0.5)
    h2.run(10.0)
    r.check("watchdog reset during crank: outputs off",
            not (h2.world.ctl_ignition or h2.world.ctl_starter))
    r.check("watchdog reset during crank: one crank only", h2.world.starter_engagements == 1)
    r.check("watchdog reset during crank: Pi back in a safe idle state",
            h2.state in (S.RESERVATION_REQUIRED, S.FAULT))
    r.log = h.records() + ["--- watchdog reset during crank ---"] + h2.records()
    return r


def s19_stuck_relay(echo: bool = False) -> ScenarioResult:
    r = ScenarioResult(19, "Output feedback mismatch (stuck ignition relay)", "Controller latches; "
                       "Pi LOCKOUT")
    h = SimulationHarness(echo=echo)
    f = FaultInjector(h)
    h.boot()
    f.stuck_ignition_relay()
    h.run(2.0)
    r.check("Pi in LOCKOUT", h.state is S.LOCKOUT)
    r.check("controller latched", bool(h.controller.core.latched))
    r.log = h.records()
    return r


def s20_power_restored(echo: bool = False) -> ScenarioResult:
    r = ScenarioResult(20, "Power restored to the whole system", "No start after power returns; "
                       "reservation is not kept")
    h, f = _armed(echo)
    h.controller.power_loss()
    f.crash_pi()
    h.run(2.0)
    h.controller.power_restore()
    f.reboot_pi()
    h.run(10.0)
    _no_start(r, h)
    r.check("Pi in RESERVATION_REQUIRED", h.state is S.RESERVATION_REQUIRED)
    r.log = h.records()
    return r


SCENARIOS: list[Callable[..., ScenarioResult]] = [
    s01_valid_triple_lock,
    s02_double_press,
    s03_slow_presses,
    s04_duplicate_events,
    s05_parked_in_gear,
    s06_brake_released,
    s07_hood_opened,
    s08_door_after_reservation,
    s09_engine_already_running,
    s10_engine_fails_to_start,
    s11_battery_voltage,
    s12_controller_disconnects,
    s13_pi_crash,
    s14_link_corruption,
    s15_engine_signal_lost,
    s16_vehicle_movement,
    s17_contradictory_sensors,
    s18_controller_restart,
    s19_stuck_relay,
    s20_power_restored,
]
