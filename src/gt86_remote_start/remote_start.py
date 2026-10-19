"""Remote-start orchestration on the Raspberry Pi.

``RemoteStartController.tick()`` is called every ``tick_seconds``. Each tick:

1. reads frames from the safety controller and sends a heartbeat when due;
2. reads vehicle status and the immobilizer interface status;
3. feeds lock events to the fob listener;
4. checks the controller link and controller-reported faults;
5. runs the handler of the current state.

Nothing here can energise ignition or starter: the Pi only *asks* the safety
controller, which decides from its own inputs.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable

from .clock import Clock
from .config import AppConfig
from .controller_protocol import (
    ControllerFault,
    ControllerLink,
    ControllerState,
    ControllerStatus,
    Frame,
    FrameDecoder,
    Heartbeat,
    MsgType,
    ReservationState,
    StartRequest,
    StartResponse,
    StatusFlag,
    StopRequest,
    encode_frame,
)
from .diagnostics import FaultManager, diagnostic_report
from .engine_monitor import EngineEvent, EngineMonitor, EngineState
from .event_logger import EventLogger
from .fault_codes import FaultCode, code_from_int, info
from .fob_listener import Activation, FobListener, LockEventSource
from .immobilizer_interface import ImmobilizerInterface, ImmobilizerStatus
from .safety_manager import SafetyManager
from .state_machine import REMOTE_ACTIVE_STATES, InvalidTransition, State, StateMachine
from .vehicle_interface import VehicleInterface, VehicleStatus

S = State
_REMOTE_CTL_STATES = frozenset(
    {ControllerState.AUTHORIZED, ControllerState.CRANKING, ControllerState.REMOTE_RUNNING}
)
_TRANSIENT_FAULTS = (
    FaultCode.CONTROLLER_COMM_LOST,
    FaultCode.SELF_TEST_FAILED,
    FaultCode.ENGINE_MONITOR_LOST,
    FaultCode.CONTROLLER_STATE_MISMATCH,
    FaultCode.RUNTIME_EXCEEDED,
)


class RemoteStartController:
    def __init__(
        self,
        cfg: AppConfig,
        clock: Clock,
        logger: EventLogger,
        faults: FaultManager,
        lock_source: LockEventSource,
        vehicle: VehicleInterface,
        immobilizer: ImmobilizerInterface,
        link: ControllerLink,
        nonce_source: Callable[[], int] = lambda: secrets.randbits(32),
    ) -> None:
        self.cfg = cfg
        self._clock = clock
        self.log = logger
        self.faults = faults
        self._locks = lock_source
        self._vehicle = vehicle
        self._immobilizer = immobilizer
        self._link = link
        self._nonce = nonce_source
        self.sm = StateMachine(clock, logger)
        self.fob = FobListener(cfg.remote_start, clock, logger)
        self.engine = EngineMonitor(cfg.engine, clock)
        self.safety = SafetyManager(cfg.safety)
        self._decoder = FrameDecoder()
        self._seq = 0
        self._crc_errors_seen = 0
        self.ctl: ControllerStatus | None = None
        self._ctl_at: float | None = None
        self._ctl_boot: int | None = None
        self._last_hb = float("-inf")
        self._started_at = clock.monotonic()
        self._pending: tuple[int, float] | None = None
        self._start_reply: StartResponse | None = None
        self._last_stop = float("-inf")
        self._valid_since: float | None = None
        self.status: VehicleStatus | None = None
        self.immobilizer_status = ImmobilizerStatus.UNKNOWN
        self._read_error_logged = False
        self._ctl_was_reset = False

    # ------------------------------------------------------------------ public
    @property
    def state(self) -> State:
        return self.sm.state

    def start(self) -> None:
        self.log.log("Application start", mode=self.cfg.hardware.mode)
        self.sm.go(S.INITIALIZING, "application start")

    def service_reset(self, confirmation: str) -> bool:
        """Leave LOCKOUT after a person has found and fixed the cause."""
        if self.state is not S.LOCKOUT:
            return False
        if not self.faults.service_clear(confirmation):
            return False
        self._send(MsgType.SERVICE_CLEAR, b"\x00\x00\x00\x00")
        # Forget the controller's pre-reset status (and any frames already in
        # flight) so self test only uses status sent after the clear.
        self._link.receive()
        self._decoder = FrameDecoder()
        self.ctl, self._ctl_at = None, None
        self.sm.go(S.INITIALIZING, "service reset")
        return True

    def report(self) -> dict[str, object]:
        return diagnostic_report(
            self.state.name,
            self.faults,
            self.log,
            self.ctl.as_dict() if self.ctl else None,
            self.status.as_dict() if self.status else None,
        )

    @property
    def link_ok(self) -> bool:
        return (
            self._ctl_at is not None
            and self._clock.monotonic() - self._ctl_at <= self.cfg.controller.status_timeout_seconds
        )

    async def tick(self) -> None:
        try:
            self._receive()
            self._heartbeat()
            await self._read_inputs()
            events = self.engine.update(self.status) if self.status else self.engine.check()
            for ev in self._locks.poll():
                self.fob.feed(ev)
            self.fob.expire()
            activations = self.fob.take_activations()
            if self.state in (S.SYSTEM_OFF,):
                return
            if self._supervise(events):
                return
            self._run_state(activations, events)
        except InvalidTransition as exc:
            self._fail(FaultCode.INTERNAL_ERROR, str(exc))
        except Exception as exc:  # noqa: BLE001 - any bug must end in a safe state
            self._fail(FaultCode.INTERNAL_ERROR, f"{type(exc).__name__}: {exc}")
        finally:
            self.log.maybe_flush()

    # ------------------------------------------------------------- link layer
    def _send(self, mtype: MsgType, payload: bytes) -> None:
        self._seq = (self._seq + 1) & 0xFFFF
        try:
            self._link.send(encode_frame(Frame(mtype, self._seq, payload)))
        except OSError as exc:
            self.log.log("Controller link send failed", level="warning", error=str(exc))

    def _heartbeat(self) -> None:
        now = self._clock.monotonic()
        if now - self._last_hb >= self.cfg.controller.heartbeat_interval_seconds:
            self._last_hb = now
            uptime = int((now - self._started_at) * 1000)
            pi_state = list(State).index(self.state)
            self._send(MsgType.HEARTBEAT, Heartbeat(uptime, pi_state).pack())

    def _receive(self) -> None:
        try:
            data = self._link.receive()
        except OSError as exc:
            self.log.log("Controller link read failed", level="warning", error=str(exc))
            return
        for frame in self._decoder.feed(data):
            try:
                if frame.type is MsgType.STATUS:
                    self._on_status(ControllerStatus.unpack(frame.payload))
                elif frame.type is MsgType.START_RESPONSE:
                    self._start_reply = StartResponse.unpack(frame.payload)
            except Exception as exc:  # noqa: BLE001 - a bad frame is dropped, never fatal
                self.log.log("Controller frame rejected", level="warning", error=str(exc))
        errors = self._decoder.crc_errors + self._decoder.format_errors
        if errors != self._crc_errors_seen:
            self.log.log(
                "Controller link frame errors",
                level="warning",
                new_errors=errors - self._crc_errors_seen,
            )
            self._crc_errors_seen = errors

    def _on_status(self, st: ControllerStatus) -> None:
        if self._ctl_boot is not None and st.boot_count != self._ctl_boot:
            self.faults.raise_fault(FaultCode.CONTROLLER_RESET, f"boot {st.boot_count}")
            self.log.log("Safety controller restarted", level="warning", boot=st.boot_count)
            self.faults.clear(FaultCode.CONTROLLER_RESET)
            self._ctl_was_reset = True
        self._ctl_boot = st.boot_count
        if self.ctl is None or st.reservation != self.ctl.reservation:
            self.log.log("Reservation state", reservation=st.reservation)
        if st.reservation is ReservationState.VALID:
            if self._valid_since is None:
                self._valid_since = self._clock.monotonic()
        else:
            self._valid_since = None
        self.ctl = st
        self._ctl_at = self._clock.monotonic()

    def _send_stop(self, reason: FaultCode) -> None:
        now = self._clock.monotonic()
        if now - self._last_stop >= self.cfg.controller.stop_resend_seconds:
            self._last_stop = now
            self._send(MsgType.STOP_REQUEST, StopRequest(int(reason)).pack())

    def _ctl_remote_active(self) -> bool:
        if self.ctl is None:
            return True  # unknown is not safe
        return self.ctl.state in _REMOTE_CTL_STATES or bool(
            self.ctl.flags & StatusFlag.REMOTE_ACTIVE
        )

    # ----------------------------------------------------------------- inputs
    async def _read_inputs(self) -> None:
        try:
            self.status = await self._vehicle.read_vehicle_status()
            self._read_error_logged = False
        except Exception as exc:  # noqa: BLE001 - keep last sample; it goes stale
            if not self._read_error_logged:
                self.log.log("Vehicle status read failed", level="warning", error=str(exc))
                self._read_error_logged = True
        try:
            self.immobilizer_status = await self._immobilizer.status()
        except Exception:  # noqa: BLE001
            self.immobilizer_status = ImmobilizerStatus.UNKNOWN

    # ------------------------------------------------------------- supervision
    def _fail(self, code: FaultCode, detail: str = "") -> None:
        """Raise a fault, request a stop, and go to FAULT or LOCKOUT."""
        fault = self.faults.raise_fault(code, detail)
        self._last_stop = float("-inf")
        self._send_stop(code)
        self.engine.end_run()
        self._pending = None
        target = S.LOCKOUT if fault.latched or self.faults.has_latched() else S.FAULT
        if self.state is not target and self.state is not S.LOCKOUT:
            self.sm.go(target, f"{code.name}: {detail}" if detail else code.name)

    def _supervise(self, events: list[EngineEvent]) -> bool:
        """Checks valid in every state. Returns True when the tick is done."""
        st = self.state
        if st in (S.INITIALIZING, S.FAULT, S.LOCKOUT):
            return False
        if not self.link_ok:
            self._fail(FaultCode.CONTROLLER_COMM_LOST, "no valid status frame")
            return True
        assert self.ctl is not None
        if self._ctl_was_reset:
            self._ctl_was_reset = False
            if st in REMOTE_ACTIVE_STATES:
                self._shutdown(FaultCode.CONTROLLER_RESET, "controller restarted; outputs dropped")
                return True
        if self.ctl.latched_faults:
            names = [f.name for f in ControllerFault if self.ctl.latched_faults & f]
            if self.ctl.latched_faults & ControllerFault.MOVEMENT:
                self.faults.raise_fault(FaultCode.VEHICLE_MOVEMENT, "reported by controller")
            self._fail(FaultCode.CONTROLLER_REPORTED_FAULT, ",".join(names))
            return True
        if EngineEvent.IMPLAUSIBLE in events:
            self._fail(FaultCode.ENGINE_SIGNAL_IMPLAUSIBLE, f"rpm={self.engine.rpm}")
            return True
        if EngineEvent.SIGNAL_LOST in events and st not in (S.SHUTDOWN_REQUESTED,):
            self._fail(FaultCode.ENGINE_MONITOR_LOST, "no rpm/running signal")
            return True
        return False

    def _idle_target(self) -> State:
        ctl, s = self.ctl, self.status
        if ctl is not None and ctl.reservation is ReservationState.VALID:
            return S.RESERVATION_VALID
        driver = (s is not None and s.ignition_on is not False) or (
            self.engine.state is not EngineState.STOPPED
        )
        return S.STANDBY if driver else S.RESERVATION_REQUIRED

    def _go_idle(self, reason: str) -> None:
        target = self._idle_target()
        if not self.sm.can_go(target):
            target = S.RESERVATION_REQUIRED
        self.sm.go(target, reason)

    def _reject(self, reasons: list[FaultCode], by: str) -> None:
        self.log.log(
            "Safety controller rejected start" if by == "controller" else "Remote start rejected",
            by=by,
            reason=reasons[0] if reasons else FaultCode.NONE,
            all_reasons=reasons,
        )

    def _shutdown(self, reason: FaultCode, detail: str = "") -> None:
        meta = info(reason)
        level = "info" if meta.severity == 0 else "warning"
        self.log.log("Shutdown requested", level=level, reason=reason, detail=detail)
        self._last_stop = float("-inf")
        self._send_stop(reason)
        self.sm.go(S.SHUTDOWN_REQUESTED, reason.name)

    # ----------------------------------------------------------------- states
    def _run_state(self, activations: list[Activation], events: list[EngineEvent]) -> None:
        handler = getattr(self, f"_state_{self.state.name.lower()}")
        handler(activations, events)

    def _state_initializing(self, _a: list[Activation], _e: list[EngineEvent]) -> None:
        # Only trust status the controller sent after we (re)started: a reply to
        # a heartbeat sent before a restart or service reset may be outdated.
        settle = 2 * self.cfg.controller.heartbeat_interval_seconds
        fresh = self._ctl_at is not None and self._ctl_at >= self.sm.entered_at + settle
        if self.link_ok and fresh:
            self.sm.go(S.SELF_TEST, "controller status received")
        elif self.sm.time_in_state > self.cfg.controller.init_timeout_seconds:
            self._fail(FaultCode.SELF_TEST_FAILED, "no status from safety controller")

    def _state_self_test(self, _a: list[Activation], _e: list[EngineEvent]) -> None:
        assert self.ctl is not None
        if self.faults.has_latched():
            self._fail(FaultCode.ACTIVE_FAULT_PRESENT, "latched fault from before restart")
            return
        if self._ctl_remote_active():
            self.log.log(
                "Remote run active at start-up; stopping",
                level="warning",
                controller_state=self.ctl.state,
            )
            self.faults.raise_fault(FaultCode.UNEXPECTED_ENGINE_STATE, "remote run at start-up")
            self._shutdown(FaultCode.UNEXPECTED_ENGINE_STATE, "Pi restarted during a remote run")
            return
        fresh = (
            self.status is not None
            and self._clock.monotonic() - self.status.timestamp
            <= self.cfg.safety.max_status_age_seconds
        )
        known = self.engine.state in (EngineState.STOPPED, EngineState.RUNNING)
        immo_known = self.immobilizer_status is not ImmobilizerStatus.UNKNOWN
        if fresh and known and immo_known:
            if self.immobilizer_status is not ImmobilizerStatus.READY:
                self.log.log(
                    "No authorized immobilizer interface; remote start impossible",
                    level="warning",
                    status=self.immobilizer_status,
                )
            self.faults.clear(FaultCode.UNEXPECTED_ENGINE_STATE)
            self.log.log("Self test passed")
            self.sm.go(S.STANDBY, "self test passed")
        elif self.sm.time_in_state > self.cfg.controller.init_timeout_seconds:
            self._fail(
                FaultCode.SELF_TEST_FAILED,
                f"status fresh={fresh} engine={self.engine.state.value} immobilizer={immo_known}",
            )

    def _idle(self, activations: list[Activation]) -> None:
        target = self._idle_target()
        if target is not self.state:
            if self.state is S.RESERVATION_VALID:
                self.log.log("Reservation cancelled", reservation=self.ctl and self.ctl.reservation)
            self.sm.go(target, "vehicle state")
        for act in activations:
            self.log.log("Remote start requested", presses=len(act.press_times))
            if self.state is S.RESERVATION_VALID:
                assert self._valid_since is not None
                if act.press_times[0] <= self._valid_since:
                    self.log.log("Reservation validity check requested")
                    self._reject([FaultCode.RESERVATION_NOT_VALID], "pi")
                    self.log.log(
                        "Sequence began before reservation was complete; ignored",
                    )
                    continue
                self.sm.go(S.START_REQUESTED, "triple lock")
                return
            self.log.log("Reservation validity check requested")
            reason = (
                FaultCode.IGNITION_ALREADY_ON if self.state is S.STANDBY
                else FaultCode.RESERVATION_NOT_VALID
            )
            self._reject([reason], "pi")

    def _state_standby(self, a: list[Activation], _e: list[EngineEvent]) -> None:
        self._idle(a)

    def _state_reservation_required(self, a: list[Activation], _e: list[EngineEvent]) -> None:
        self._idle(a)

    def _state_reservation_valid(self, a: list[Activation], _e: list[EngineEvent]) -> None:
        self._idle(a)

    def _state_start_requested(self, _a: list[Activation], _e: list[EngineEvent]) -> None:
        if not self.cfg.remote_start.enabled:
            self._reject([FaultCode.REMOTE_START_DISABLED], "pi")
            self._go_idle("remote start disabled")
            return
        self.sm.go(S.SAFETY_CHECK, "checking interlocks")

    def _state_safety_check(self, _a: list[Activation], _e: list[EngineEvent]) -> None:
        now = self._clock.monotonic()
        if self._pending is None:
            assert self.status is not None
            self.log.log("Reservation validity check requested")
            decision = self.safety.evaluate_start(
                self.status,
                self.ctl,
                self.link_ok,
                self.engine.state,
                self.immobilizer_status,
                self.faults.blocking_start(),
                now,
            )
            if decision.contradictions:
                self._fail(FaultCode.SENSOR_CONTRADICTION, "; ".join(decision.contradictions))
                return
            if not decision.allowed:
                self._reject(decision.reasons, "pi")
                self._go_idle("pre-check refused")
                return
            nonce = self._nonce() & 0xFFFFFFFF
            req = StartRequest(
                nonce, self.cfg.controller.start_validity_ms, self.cfg.remote_start.runtime_seconds
            )
            self._start_reply = None
            self._pending = (nonce, now + self.cfg.controller.start_response_timeout_seconds)
            self._send(MsgType.START_REQUEST, req.pack())
            self.log.log("Start request sent to safety controller", nonce=f"{nonce:08x}")
            return
        nonce, deadline = self._pending
        reply = self._start_reply
        if reply is not None and reply.nonce == nonce:
            self._pending = None
            if reply.accepted:
                self.log.log("Safety controller authorized start")
                self.engine.begin_start_attempt(self.cfg.controller.crank_confirm_timeout_seconds)
                self.sm.go(S.START_AUTHORIZED, "controller accepted")
            else:
                self._reject([code_from_int(reply.reason)], "controller")
                self._go_idle("controller refused")
            return
        if now > deadline:
            self._pending = None
            self.log.log("No start response from safety controller", level="warning")
            self._shutdown(FaultCode.CONTROLLER_COMM_LOST, "start response timeout")

    def _state_start_authorized(self, _a: list[Activation], _e: list[EngineEvent]) -> None:
        assert self.ctl is not None
        if self.ctl.state in (ControllerState.CRANKING, ControllerState.REMOTE_RUNNING):
            self.log.log("Starter engaged by safety controller")
            self.sm.go(S.CRANK_REQUESTED, "controller cranking")
        elif self.ctl.state is not ControllerState.AUTHORIZED and self.sm.time_in_state > 0.5:
            reason = code_from_int(self.ctl.last_reason)
            self.log.log("Safety controller aborted start", level="warning", reason=reason)
            self._shutdown(reason, "aborted before crank")
        elif self.sm.time_in_state > self.cfg.controller.authorized_timeout_seconds:
            self._shutdown(FaultCode.CONTROLLER_STATE_MISMATCH, "crank never began")

    def _state_crank_requested(self, _a: list[Activation], events: list[EngineEvent]) -> None:
        assert self.ctl is not None
        if (
            self.engine.state is EngineState.RUNNING
            and self.ctl.state is ControllerState.REMOTE_RUNNING
        ):
            self.engine.begin_run(self.cfg.remote_start.maximum_runtime_seconds)
            self.log.log(
                "Engine running confirmed",
                rpm=self.engine.rpm,
                runtime_s=self.cfg.remote_start.runtime_seconds,
            )
            self.sm.go(S.ENGINE_RUNNING, "rpm and running signal confirmed")
            return
        failed = EngineEvent.START_FAILED in events or (
            self.ctl.state is ControllerState.IDLE and self.sm.time_in_state > 0.5
        )
        if failed:
            self.faults.raise_fault(
                FaultCode.START_FAILED, f"controller reason {code_from_int(self.ctl.last_reason).name}"
            )
            self.log.log("Engine did not start; no automatic retry", level="warning")
            self._shutdown(FaultCode.START_FAILED)

    def _state_engine_running(self, activations: list[Activation], events: list[EngineEvent]) -> None:
        assert self.ctl is not None and self.status is not None
        now = self._clock.monotonic()
        if self.ctl.state is not ControllerState.REMOTE_RUNNING:
            if self.status.ignition_on:
                self.log.log("Driver took over with the key; remote run ended")
                self.engine.end_run()
                self.sm.go(S.STANDBY, FaultCode.DRIVER_TOOK_OVER.name)
            else:
                reason = code_from_int(self.ctl.last_reason)
                level = "info" if info(reason).severity == 0 else "warning"
                self.log.log("Safety controller ended remote run", level=level, reason=reason)
                self._shutdown(reason, "ended by controller")
            return
        decision = self.safety.evaluate_running(self.status, now)
        if decision.contradictions:
            self._fail(FaultCode.SENSOR_CONTRADICTION, "; ".join(decision.contradictions))
            return
        if FaultCode.VEHICLE_MOVEMENT in decision.reasons:
            self._fail(FaultCode.VEHICLE_MOVEMENT, f"speed={self.status.vehicle_speed_kph}")
            return
        if not decision.allowed:
            self._shutdown(decision.primary_reason, "interlock lost while running")
            return
        if EngineEvent.STALLED in events or self.engine.state is EngineState.STOPPED:
            self.faults.raise_fault(FaultCode.ENGINE_STALLED)
            self._shutdown(FaultCode.ENGINE_STALLED)
            return
        if EngineEvent.RUNTIME_EXCEEDED in events:
            self._fail(FaultCode.RUNTIME_EXCEEDED, f"elapsed={self.engine.run_elapsed:.0f}s")
            return
        if activations:
            self.log.log("Remote stop requested by key fob")
            self._shutdown(FaultCode.USER_STOP)
            return
        if self.engine.run_elapsed >= self.cfg.remote_start.runtime_seconds:
            self._shutdown(FaultCode.RUNTIME_LIMIT_REACHED, f"{self.engine.run_elapsed:.0f}s")

    def _state_shutdown_requested(self, _a: list[Activation], _e: list[EngineEvent]) -> None:
        assert self.ctl is not None
        if self._ctl_remote_active():
            self._send_stop(FaultCode.USER_STOP)
        else:
            ctl_engine = self.ctl.has(StatusFlag.ENGINE_RUNNING)
            driver = self.status is not None and self.status.ignition_on is True
            stopped = self.engine.confirmed_stopped() or (
                self.engine.state is EngineState.UNKNOWN and ctl_engine is False
            )
            if driver:
                self.engine.end_run()
                self.sm.go(S.STANDBY, "driver has the key on")
                return
            if stopped:
                self.engine.end_run()
                self.log.log("Engine stop confirmed")
                self.sm.go(S.RESERVATION_REQUIRED, "stopped; reservation must be re-established")
                return
        if self.sm.time_in_state > self.cfg.controller.shutdown_timeout_seconds:
            self._fail(FaultCode.ENGINE_STOP_UNCONFIRMED, "engine still running or unknown")

    def _state_fault(self, _a: list[Activation], _e: list[EngineEvent]) -> None:
        if self.faults.has_latched():
            self.sm.go(S.LOCKOUT, "latched fault")
            return
        if self._ctl_remote_active():
            self._send_stop(FaultCode.ACTIVE_FAULT_PRESENT)
        if (
            self.link_ok
            and not self._ctl_remote_active()
            and self.sm.time_in_state >= self.cfg.controller.fault_recovery_seconds
        ):
            for code in _TRANSIENT_FAULTS:
                self.faults.clear(code)
            if not self.faults.blocking_start():
                self.sm.go(S.INITIALIZING, "fault cleared; re-running self test")

    def _state_lockout(self, _a: list[Activation], _e: list[EngineEvent]) -> None:
        if self._ctl_remote_active():
            self._send_stop(FaultCode.ACTIVE_FAULT_PRESENT)


def is_remote_active(state: State) -> bool:
    return state in REMOTE_ACTIVE_STATES
