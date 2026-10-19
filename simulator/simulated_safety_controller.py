"""Runs the safety-controller reference model against the simulated vehicle.

Also models what the real hardware adds around the firmware: a byte link to
the Pi, a hardware watchdog (a hung MCU loses its outputs and is reset), and
power loss (outputs drop instantly, RAM state is lost, NVM is kept).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from gt86_remote_start.clock import Clock
from gt86_remote_start.controller_protocol import Frame, FrameDecoder, encode_frame
from sc_model import SafetyCore, ScParams

from .simulated_vehicle import VehicleWorld


@dataclass
class Pipe:
    """One direction of the serial link, with fault injection."""

    buffer: bytearray = field(default_factory=bytearray)
    connected: bool = True
    corrupt: bool = False
    _flip: int = 0

    def write(self, data: bytes) -> None:
        if not self.connected:
            return
        if self.corrupt:
            data = bytes(b ^ 0x5A if (i + self._flip) % 3 == 0 else b for i, b in enumerate(data))
            self._flip += 1
        self.buffer += data

    def read(self) -> bytes:
        out = bytes(self.buffer)
        self.buffer.clear()
        return out


class LinkEnd:
    def __init__(self, tx: Pipe, rx: Pipe) -> None:
        self._tx, self._rx = tx, rx

    def send(self, data: bytes) -> None:
        self._tx.write(data)

    def receive(self) -> bytes:
        return self._rx.read()


class SimulatedSafetyController:
    def __init__(self, clock: Clock, world: VehicleWorld, params: ScParams | None = None) -> None:
        self._clock = clock
        self.world = world
        self.params = params or ScParams()
        self.nvm: dict[str, object] = {}
        self.core = SafetyCore(self.params, self.nvm)
        self.pi_to_ctl = Pipe()
        self.ctl_to_pi = Pipe()
        self.pi_end = LinkEnd(self.pi_to_ctl, self.ctl_to_pi)
        self._decoder = FrameDecoder()
        self._seq = 0
        self._lock_pending = False
        self.service_input = False
        self.powered = True
        self.hung_until: float | None = None
        self.watchdog_s = 0.1
        self._hang_started = 0.0
        self.core.power_on(clock.monotonic())

    def notify_lock(self) -> None:
        """The controller's own input saw a factory lock command."""
        self._lock_pending = True

    def power_loss(self) -> None:
        self.powered = False
        self._drive(False, False)

    def power_restore(self) -> None:
        self.powered = True
        self.core.power_on(self._clock.monotonic(), "power")
        self._decoder = FrameDecoder()

    def hang(self, seconds: float) -> None:
        self.hung_until = self._clock.monotonic() + seconds
        self._hang_started = self._clock.monotonic()

    def _drive(self, ignition: bool, starter: bool) -> None:
        self.world.ctl_ignition = ignition
        self.world.ctl_starter = starter

    def step(self) -> None:
        now = self._clock.monotonic()
        if not self.powered:
            self.pi_to_ctl.read()
            return
        if self.hung_until is not None:
            if now - self._hang_started >= self.watchdog_s:
                # Hardware watchdog: outputs drop, MCU resets once the hang ends.
                self._drive(False, False)
            if now < self.hung_until:
                return
            self.hung_until = None
            self.core.power_on(now, "watchdog")
        for frame in self._decoder.feed(self.pi_to_ctl.read()):
            for mtype, payload in self.core.receive(now, frame):
                self._seq = (self._seq + 1) & 0xFFFF
                self.ctl_to_pi.write(encode_frame(Frame(mtype, self._seq, payload)))
        inputs = self.world.controller_inputs(self._lock_pending, self.service_input)
        self._lock_pending = False
        out = self.core.step(now, inputs)
        self._drive(out.ignition, out.starter)
