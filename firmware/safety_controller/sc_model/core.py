"""Executable reference model of the independent safety controller.

This is the *specification* the real firmware (Phase 4, C on an automotive
microcontroller) must match. It is deliberately written like firmware: no
dynamic behaviour beyond fixed state, a ``step(now, inputs)`` super-loop and
a ``receive(now, frame)`` message handler. The simulator runs it; the same
scenario tests will later run against the firmware on a hardware-in-the-loop
bench.

Authority and defaults
  * Outputs (ignition hold, start request) default OFF at power-on, on any
    fault, on heartbeat loss and on watchdog reset.
  * The controller reads its OWN inputs; it never trusts the Pi's view.
  * Unknown (invalid) inputs are never treated as safe.
  * Critical faults latch in non-volatile memory and survive power cycles.
    Only a physical service input may clear them.

Reservation procedure (manual transmission), modelled on the documented
commercial approach (Fortin EVO-ONE "ready/reservation mode"); the exact
procedure must still be validated before vehicle use:
  1. Engine running on the key, both neutral sources = neutral, parking
     brake applied, hood closed, vehicle stationary.
  2. Driver applies the parking brake (rising edge) -> ARMED: the controller
     holds ignition so the engine keeps running when the key is removed.
  3. Key removed while the engine keeps running -> KEY_REMOVED. A running
     engine with the key out and the clutch released is evidence that no
     gear is engaged (in gear the car would stall or move).
  4. A door opens and closes (driver exits) -> DRIVER_EXITED.
  5. Factory lock command observed -> hold released, engine must stop within
     a deadline -> VALID.
  Cancelled by: any door opening after VALID, hood open, parking brake
  released, neutral lost or disputed, movement, key turned on, a sensor
  becoming invalid, heartbeat loss during the hold, a timeout, power loss
  (reservation lives in RAM only).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from gt86_remote_start.controller_protocol import (
    LATCHING_CONTROLLER_FAULTS,
    ControllerFault,
    ControllerState,
    ControllerStatus,
    Frame,
    MsgType,
    ReservationState,
    StartRequest,
    StartResponse,
    StatusFlag,
    StopRequest,
)
from gt86_remote_start.fault_codes import FaultCode

CS, RS, CF, F = ControllerState, ReservationState, ControllerFault, FaultCode


@dataclass(frozen=True)
class ScParams:
    """Proposed values, not validated vehicle thresholds."""

    heartbeat_timeout_s: float = 1.0
    reservation_timeout_s: float = 180.0
    lock_stop_confirm_s: float = 6.0
    pre_crank_s: float = 2.0
    max_crank_s: float = 3.0
    hard_max_runtime_s: int = 900
    min_start_mv: int = 12000
    contradiction_s: float = 0.5
    output_feedback_s: float = 0.3
    stop_confirm_s: float = 8.0
    allow_link_service_clear: bool = False


@dataclass(frozen=True)
class ScInputs:
    neutral_a: bool | None = None
    neutral_b: bool | None = None
    parking_brake: bool | None = None
    hood_closed: bool | None = None
    doors_closed: bool | None = None
    engine_running: bool | None = None
    moving: bool | None = None
    key_on: bool | None = None
    lock_pulse: bool = False
    battery_mv: int | None = None
    ignition_feedback: bool | None = None
    starter_feedback: bool | None = None
    service_input: bool = False


@dataclass
class ScOutputs:
    ignition: bool = False  # ignition hold / remote ignition request
    starter: bool = False  # start request to the start interface


@dataclass
class SafetyCore:
    params: ScParams
    nvm: dict[str, Any] = field(default_factory=dict)  # survives power cycles

    def __post_init__(self) -> None:
        self.nvm.setdefault("latched", 0)
        self.nvm.setdefault("boot_count", 0)
        self.power_on(0.0)

    # ---------------------------------------------------------------- reset
    def power_on(self, now: float, cause: str = "power") -> None:
        self.nvm["boot_count"] += 1
        self.state = CS.BOOT
        self.out = ScOutputs()
        self.reservation = RS.NONE
        self.active = CF(0)
        if cause == "watchdog":
            self.active |= CF.WATCHDOG_RESET
        elif cause == "brownout":
            self.active |= CF.BROWNOUT
        self.last_reason = F.NONE
        self.inp = ScInputs()
        self._prev_brake: bool | None = None
        self.last_hb: float | None = None
        self.used_nonces: list[int] = []
        self.res_started = 0.0
        self.door_opened = False
        self.lock_deadline: float | None = None
        self.auth_until = 0.0
        self.crank_at = 0.0
        self.crank_started = 0.0
        self.run_started = 0.0
        self.run_limit = 0
        self.shutdown_since = 0.0
        self.contradiction_since: float | None = None
        self.mismatch_since: float | None = None
        self.now = now

    @property
    def latched(self) -> CF:
        return CF(self.nvm["latched"])

    def _latch(self, fault: CF, reason: FaultCode) -> None:
        self.nvm["latched"] = int(self.latched | fault)
        self.active |= fault
        self._outputs_off()
        self.reservation = RS.NONE
        self.state = CS.FAULT_LATCHED
        self.last_reason = reason

    def _outputs_off(self) -> None:
        self.out = ScOutputs()

    def hb_ok(self) -> bool:
        return self.last_hb is not None and self.now - self.last_hb <= self.params.heartbeat_timeout_s

    # -------------------------------------------------------------- messages
    def receive(self, now: float, frame: Frame) -> list[tuple[MsgType, bytes]]:
        self.now = now
        if frame.type is MsgType.HEARTBEAT:
            self.last_hb = now
            return [(MsgType.STATUS, self.status().pack())]
        if frame.type is MsgType.START_REQUEST:
            req = StartRequest.unpack(frame.payload)
            reason = self._start_refusal(req)
            if reason is F.NONE:
                self._authorize(req)
            self.used_nonces = (self.used_nonces + [req.nonce])[-16:]
            accepted = reason is F.NONE
            return [(MsgType.START_RESPONSE, StartResponse(req.nonce, accepted, reason).pack())]
        if frame.type is MsgType.STOP_REQUEST:
            reason = FaultCode(StopRequest.unpack(frame.payload).reason)
            self._stop(reason)
            return [(MsgType.STOP_RESPONSE, StopRequest(int(reason)).pack())]
        if frame.type is MsgType.SERVICE_CLEAR:
            if self.inp.service_input or self.params.allow_link_service_clear:
                self.nvm["latched"] = 0
                self.active = CF(0)
                self._outputs_off()
                self.state = CS.IDLE
                self.last_reason = F.NONE
            return []
        return []

    def _start_refusal(self, req: StartRequest) -> FaultCode:
        i = self.inp
        if self.latched:
            return F.CONTROLLER_REPORTED_FAULT
        if self.state is not CS.IDLE:
            return F.CONTROLLER_STATE_MISMATCH
        if not self.hb_ok():
            return F.CONTROLLER_COMM_LOST
        if req.nonce in self.used_nonces:
            return F.START_REQUEST_REPLAYED
        if self.reservation is not RS.VALID:
            return F.RESERVATION_NOT_VALID
        required = (i.neutral_a, i.neutral_b, i.parking_brake, i.hood_closed, i.doors_closed,
                    i.engine_running, i.moving, i.key_on, i.battery_mv, i.ignition_feedback,
                    i.starter_feedback)
        if any(v is None for v in required):
            return F.SENSOR_UNKNOWN
        if not (i.neutral_a and i.neutral_b):
            return F.NEUTRAL_NOT_VERIFIED
        if not i.parking_brake:
            return F.PARKING_BRAKE_NOT_SET
        if not i.hood_closed:
            return F.HOOD_OPEN
        if not i.doors_closed:
            return F.DOOR_OPEN
        if i.engine_running:
            return F.ENGINE_ALREADY_RUNNING
        if i.key_on:
            return F.IGNITION_ALREADY_ON
        if i.moving:
            return F.VEHICLE_NOT_STATIONARY
        assert i.battery_mv is not None
        if i.battery_mv < self.params.min_start_mv:
            return F.BATTERY_LOW
        if req.valid_ms / 1000 < self.params.pre_crank_s:
            return F.START_REQUEST_EXPIRED
        return F.NONE

    def _authorize(self, req: StartRequest) -> None:
        self.state = CS.AUTHORIZED
        self.reservation = RS.NONE  # a reservation is used once
        self.out = ScOutputs(ignition=True, starter=False)
        self.auth_until = self.now + req.valid_ms / 1000
        self.crank_at = self.now + self.params.pre_crank_s
        self.run_limit = min(req.runtime_s, self.params.hard_max_runtime_s)
        self.last_reason = F.NONE

    def _stop(self, reason: FaultCode) -> None:
        if self.state in (CS.AUTHORIZED, CS.CRANKING, CS.REMOTE_RUNNING):
            self._begin_shutdown(reason)
        elif self.reservation is not RS.NONE and self.state is CS.IDLE:
            self._cancel_reservation(reason)

    # ----------------------------------------------------------- super-loop
    def step(self, now: float, inp: ScInputs) -> ScOutputs:
        self.now = now
        self.inp = inp
        self._diagnose_inputs()
        if self.state is CS.FAULT_LATCHED or self.latched:
            if self.state is not CS.FAULT_LATCHED:
                self.state = CS.FAULT_LATCHED
            self._outputs_off()
            self.reservation = RS.NONE
            return self.out
        if self.state is CS.BOOT:
            self.state = CS.IDLE
        handler = {
            CS.IDLE: self._step_idle,
            CS.AUTHORIZED: self._step_authorized,
            CS.CRANKING: self._step_cranking,
            CS.REMOTE_RUNNING: self._step_running,
            CS.SHUTTING_DOWN: self._step_shutting_down,
        }[self.state]
        handler()
        self._check_output_feedback()
        self._prev_brake = inp.parking_brake
        return self.out

    def _diagnose_inputs(self) -> None:
        i = self.inp
        values = (i.neutral_a, i.neutral_b, i.parking_brake, i.hood_closed, i.doors_closed,
                  i.engine_running, i.moving, i.key_on, i.battery_mv)
        if any(v is None for v in values):
            self.active |= CF.SENSOR_INVALID
        else:
            self.active &= ~CF.SENSOR_INVALID
        if i.battery_mv is not None and i.battery_mv < self.params.min_start_mv:
            self.active |= CF.LOW_VOLTAGE
        else:
            self.active &= ~CF.LOW_VOLTAGE
        hb = CF.HEARTBEAT_LOST
        self.active = (self.active | hb) if not self.hb_ok() else (self.active & ~hb)
        if i.neutral_a is not None and i.neutral_b is not None and i.neutral_a != i.neutral_b:
            self.contradiction_since = self.contradiction_since or self.now
            if self.now - self.contradiction_since >= self.params.contradiction_s:
                self._latch(CF.SENSOR_CONTRADICTION, F.SENSOR_CONTRADICTION)
        else:
            self.contradiction_since = None
        held = self.out.ignition or self.out.starter
        if i.moving and held:
            self._latch(CF.MOVEMENT, F.VEHICLE_MOVEMENT)

    def _check_output_feedback(self) -> None:
        i = self.inp
        bad = (
            i.ignition_feedback is None
            or i.starter_feedback is None
            or i.ignition_feedback != self.out.ignition
            or i.starter_feedback != self.out.starter
        )
        if bad:
            self.mismatch_since = self.mismatch_since or self.now
            if self.now - self.mismatch_since >= self.params.output_feedback_s:
                self._latch(CF.OUTPUT_FEEDBACK_MISMATCH, F.OUTPUT_FEEDBACK_MISMATCH)
        else:
            self.mismatch_since = None

    def _parked_secure(self) -> FaultCode:
        """Interlocks that must hold throughout reservation and remote run."""
        i = self.inp
        if i.neutral_a is None or i.neutral_b is None or i.hood_closed is None:
            return F.SENSOR_UNKNOWN
        if i.parking_brake is None or i.moving is None or i.key_on is None:
            return F.SENSOR_UNKNOWN
        if not (i.neutral_a and i.neutral_b):
            return F.NEUTRAL_NOT_VERIFIED
        if not i.parking_brake:
            return F.PARKING_BRAKE_NOT_SET
        if not i.hood_closed:
            return F.HOOD_OPEN
        if i.moving:
            return F.VEHICLE_NOT_STATIONARY
        return F.NONE

    # ---- reservation (in IDLE) ---------------------------------------------
    def _cancel_reservation(self, reason: FaultCode) -> None:
        self.reservation = RS.NONE
        self.out = ScOutputs()
        self.lock_deadline = None
        self.last_reason = reason

    def _step_idle(self) -> None:
        i, r = self.inp, self.reservation
        if r is RS.NONE:
            rising = self._prev_brake is False and i.parking_brake is True
            if (
                rising
                and self._parked_secure() is F.NONE
                and i.engine_running is True
                and i.key_on is True
                and self.hb_ok()
                and not self.latched
            ):
                self.reservation = RS.ARMED
                self.res_started = self.now
                self.door_opened = False
                self.out = ScOutputs(ignition=True)
            return
        reason = self._parked_secure()
        if reason is F.NONE and r is not RS.VALID:
            if self.now - self.res_started > self.params.reservation_timeout_s:
                reason = F.RESERVATION_NOT_VALID
            elif not self.hb_ok():
                reason = F.CONTROLLER_COMM_LOST
        if reason is not F.NONE:
            self._cancel_reservation(reason)
            return
        if r is RS.ARMED:
            if i.engine_running is not True:
                self._cancel_reservation(F.ENGINE_STALLED)
            elif i.key_on is False:
                self.reservation = RS.KEY_REMOVED
        elif r in (RS.KEY_REMOVED, RS.DRIVER_EXITED):
            if i.key_on:
                self._cancel_reservation(F.IGNITION_ALREADY_ON)
            elif self.lock_deadline is not None:
                if i.engine_running is False:
                    self.reservation = RS.VALID
                    self.lock_deadline = None
                elif self.now > self.lock_deadline:
                    self._latch(CF.STOP_UNCONFIRMED, F.ENGINE_STOP_UNCONFIRMED)
            elif i.engine_running is not True:
                self._cancel_reservation(F.ENGINE_STALLED)
            elif r is RS.KEY_REMOVED:
                if i.doors_closed is False:
                    self.door_opened = True
                elif i.doors_closed and self.door_opened:
                    self.reservation = RS.DRIVER_EXITED
            elif r is RS.DRIVER_EXITED and i.lock_pulse and i.doors_closed:
                self.out = ScOutputs()  # release hold: engine stops
                self.lock_deadline = self.now + self.params.lock_stop_confirm_s
            elif r is RS.DRIVER_EXITED and i.doors_closed is False:
                self.reservation = RS.KEY_REMOVED  # door opened again: wait for close
                self.door_opened = True
        elif r is RS.VALID:
            if i.doors_closed is not True:
                self._cancel_reservation(F.DOOR_OPEN)
            elif i.key_on or i.engine_running:
                self._cancel_reservation(F.IGNITION_ALREADY_ON)

    # ---- remote run ------------------------------------------------------------
    def _run_abort_reason(self) -> FaultCode:
        i = self.inp
        if not self.hb_ok():
            self.active |= CF.HEARTBEAT_LOST
            return F.CONTROLLER_COMM_LOST
        reason = self._parked_secure()
        if reason is not F.NONE:
            return reason
        if i.doors_closed is not True:
            return F.DOOR_OPEN
        if i.engine_running is None:
            return F.SENSOR_UNKNOWN
        return F.NONE

    def _begin_shutdown(self, reason: FaultCode) -> None:
        self._outputs_off()
        self.state = CS.SHUTTING_DOWN
        self.shutdown_since = self.now
        self.last_reason = reason

    def _step_authorized(self) -> None:
        if self.inp.key_on:
            self._begin_shutdown(F.DRIVER_TOOK_OVER)
            return
        reason = self._run_abort_reason()
        if reason is not F.NONE:
            self._begin_shutdown(reason)
            return
        if self.now < self.crank_at:
            return
        if self.now > self.auth_until:
            self._begin_shutdown(F.START_REQUEST_EXPIRED)
        elif self.inp.engine_running:
            self._begin_shutdown(F.UNEXPECTED_ENGINE_STATE)
        else:
            self.out = ScOutputs(ignition=True, starter=True)
            self.state = CS.CRANKING
            self.crank_started = self.now

    def _step_cranking(self) -> None:
        if self.inp.key_on:
            self._begin_shutdown(F.DRIVER_TOOK_OVER)
            return
        reason = self._run_abort_reason()
        if reason is not F.NONE:
            self._begin_shutdown(reason)
            return
        if self.inp.engine_running:
            self.out = ScOutputs(ignition=True, starter=False)
            self.state = CS.REMOTE_RUNNING
            self.run_started = self.now
        elif self.now - self.crank_started >= self.params.max_crank_s:
            self.active |= CF.CRANK_TIMEOUT
            self._begin_shutdown(F.START_FAILED)

    def _step_running(self) -> None:
        i = self.inp
        if i.key_on:
            # Driver turned the key: hand over. The engine keeps running on the key.
            self._outputs_off()
            self.state = CS.IDLE
            self.last_reason = F.DRIVER_TOOK_OVER
            return
        reason = self._run_abort_reason()
        if reason is not F.NONE:
            self._begin_shutdown(reason)
        elif i.engine_running is False:
            self.active |= CF.STALL
            self._begin_shutdown(F.ENGINE_STALLED)
        elif self.now - self.run_started >= self.run_limit:
            self.active |= CF.RUNTIME_LIMIT
            self._begin_shutdown(F.RUNTIME_LIMIT_REACHED)

    def _step_shutting_down(self) -> None:
        self._outputs_off()
        if self.inp.engine_running is False or self.inp.key_on:
            self.state = CS.IDLE
            self.active &= ~(CF.CRANK_TIMEOUT | CF.STALL | CF.RUNTIME_LIMIT)
        elif self.now - self.shutdown_since > self.params.stop_confirm_s:
            self._latch(CF.STOP_UNCONFIRMED, F.ENGINE_STOP_UNCONFIRMED)

    # ---------------------------------------------------------------- status
    def runtime_remaining(self) -> int:
        if self.state is not CS.REMOTE_RUNNING:
            return 0
        return max(0, int(self.run_limit - (self.now - self.run_started)))

    def status(self) -> ControllerStatus:
        i = self.inp
        flags, valid = StatusFlag(0), StatusFlag(0)
        for flag, value in (
            (StatusFlag.NEUTRAL_A, i.neutral_a),
            (StatusFlag.NEUTRAL_B, i.neutral_b),
            (StatusFlag.PARKING_BRAKE, i.parking_brake),
            (StatusFlag.HOOD_CLOSED, i.hood_closed),
            (StatusFlag.DOORS_CLOSED, i.doors_closed),
            (StatusFlag.ENGINE_RUNNING, i.engine_running),
            (StatusFlag.MOVING, i.moving),
            (StatusFlag.KEY_ON, i.key_on),
            (StatusFlag.IGNITION_OUT, self.out.ignition),
            (StatusFlag.STARTER_OUT, self.out.starter),
            (
                StatusFlag.REMOTE_ACTIVE,
                self.state in (CS.AUTHORIZED, CS.CRANKING, CS.REMOTE_RUNNING),
            ),
            (StatusFlag.HEARTBEAT_OK, self.hb_ok()),
        ):
            if value is not None:
                valid |= flag
                if value:
                    flags |= flag
        return ControllerStatus(
            state=self.state,
            reservation=self.reservation,
            flags=flags,
            valid=valid,
            active_faults=self.active,
            latched_faults=self.latched & LATCHING_CONTROLLER_FAULTS,
            runtime_remaining_s=self.runtime_remaining(),
            battery_mv=i.battery_mv or 0,
            last_reason=int(self.last_reason),
            boot_count=self.nvm["boot_count"],
        )
