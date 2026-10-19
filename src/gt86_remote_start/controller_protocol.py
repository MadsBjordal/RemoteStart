"""Wire protocol between the Raspberry Pi and the independent safety controller.

Physical link (proposal): 3.3 V UART through a digital isolator, 115200 8N1.

Frame layout (little endian)::

    0xA5 | VER | TYPE | SEQ(u16) | LEN(u8) | PAYLOAD(LEN) | CRC16(u16)

CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF) over VER..PAYLOAD. A frame
with a bad CRC, wrong version or wrong length is dropped and counted; the
decoder resynchronises on the next 0xA5.

Authority: the controller decides. A START_REQUEST is a request with a
bounded validity; the controller re-checks every interlock itself, and
refuses anything it cannot verify. The controller ends any remote run if
heartbeats stop.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntEnum, IntFlag
from typing import Protocol

SOF = 0xA5
VERSION = 1
MAX_PAYLOAD = 64


class MsgType(IntEnum):
    HEARTBEAT = 0x01
    START_REQUEST = 0x02
    STOP_REQUEST = 0x03
    SERVICE_CLEAR = 0x04
    STATUS = 0x81
    START_RESPONSE = 0x82
    STOP_RESPONSE = 0x83


class ControllerState(IntEnum):
    BOOT = 0
    IDLE = 1
    AUTHORIZED = 2  # ignition on, pre-crank checks
    CRANKING = 3
    REMOTE_RUNNING = 4
    SHUTTING_DOWN = 5
    FAULT_LATCHED = 6


class ReservationState(IntEnum):
    NONE = 0
    ARMED = 1  # engine held running by the module, waiting for key-out
    KEY_REMOVED = 2  # key out, engine still held, waiting for driver exit
    DRIVER_EXITED = 3  # door opened and closed, waiting for lock
    VALID = 4  # engine stopped by lock, vehicle secured


class StatusFlag(IntFlag):
    NEUTRAL_A = 1 << 0
    NEUTRAL_B = 1 << 1
    PARKING_BRAKE = 1 << 2
    HOOD_CLOSED = 1 << 3
    DOORS_CLOSED = 1 << 4
    ENGINE_RUNNING = 1 << 5
    MOVING = 1 << 6
    KEY_ON = 1 << 7
    IGNITION_OUT = 1 << 8
    STARTER_OUT = 1 << 9
    REMOTE_ACTIVE = 1 << 10
    HEARTBEAT_OK = 1 << 11


class ControllerFault(IntFlag):
    HEARTBEAT_LOST = 1 << 0
    SENSOR_INVALID = 1 << 1
    SENSOR_CONTRADICTION = 1 << 2
    MOVEMENT = 1 << 3
    CRANK_TIMEOUT = 1 << 4
    STALL = 1 << 5
    RUNTIME_LIMIT = 1 << 6
    LOW_VOLTAGE = 1 << 7
    WATCHDOG_RESET = 1 << 8
    OUTPUT_FEEDBACK_MISMATCH = 1 << 9
    BROWNOUT = 1 << 10
    STOP_UNCONFIRMED = 1 << 11


LATCHING_CONTROLLER_FAULTS = (
    ControllerFault.SENSOR_CONTRADICTION
    | ControllerFault.MOVEMENT
    | ControllerFault.OUTPUT_FEEDBACK_MISMATCH
    | ControllerFault.STOP_UNCONFIRMED
)


def crc16_ccitt(data: bytes, crc: int = 0xFFFF) -> int:
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return crc


@dataclass(frozen=True)
class Frame:
    type: MsgType
    seq: int
    payload: bytes = b""


def encode_frame(frame: Frame) -> bytes:
    if len(frame.payload) > MAX_PAYLOAD:
        raise ValueError("payload too long")
    body = struct.pack("<BBHB", VERSION, frame.type, frame.seq & 0xFFFF, len(frame.payload))
    body += frame.payload
    return bytes([SOF]) + body + struct.pack("<H", crc16_ccitt(body))


class FrameDecoder:
    """Streaming decoder tolerant of noise, partial frames and corruption."""

    def __init__(self) -> None:
        self._buf = bytearray()
        self.crc_errors = 0
        self.format_errors = 0

    def feed(self, data: bytes) -> list[Frame]:
        self._buf += data
        frames: list[Frame] = []
        while True:
            start = self._buf.find(SOF)
            if start < 0:
                self._buf.clear()
                return frames
            del self._buf[:start]
            if len(self._buf) < 6:
                return frames
            ver, mtype, seq, length = struct.unpack_from("<BBHB", self._buf, 1)
            if ver != VERSION or length > MAX_PAYLOAD:
                self.format_errors += 1
                del self._buf[0]
                continue
            total = 1 + 5 + length + 2
            if len(self._buf) < total:
                return frames
            body = bytes(self._buf[1 : 6 + length])
            (crc,) = struct.unpack_from("<H", self._buf, 6 + length)
            if crc != crc16_ccitt(body):
                self.crc_errors += 1
                del self._buf[0]
                continue
            del self._buf[:total]
            try:
                frames.append(Frame(MsgType(mtype), seq, body[5:]))
            except ValueError:
                self.format_errors += 1


# --- payloads ---------------------------------------------------------------


@dataclass(frozen=True)
class Heartbeat:
    uptime_ms: int
    pi_state: int
    FMT = "<IB"

    def pack(self) -> bytes:
        return struct.pack(self.FMT, self.uptime_ms & 0xFFFFFFFF, self.pi_state)

    @classmethod
    def unpack(cls, b: bytes) -> Heartbeat:
        return cls(*struct.unpack(cls.FMT, b))


@dataclass(frozen=True)
class StartRequest:
    nonce: int
    valid_ms: int
    runtime_s: int
    FMT = "<IHH"

    def pack(self) -> bytes:
        return struct.pack(self.FMT, self.nonce, self.valid_ms, self.runtime_s)

    @classmethod
    def unpack(cls, b: bytes) -> StartRequest:
        return cls(*struct.unpack(cls.FMT, b))


@dataclass(frozen=True)
class StartResponse:
    nonce: int
    accepted: bool
    reason: int
    FMT = "<IBH"

    def pack(self) -> bytes:
        return struct.pack(self.FMT, self.nonce, int(self.accepted), self.reason)

    @classmethod
    def unpack(cls, b: bytes) -> StartResponse:
        nonce, acc, reason = struct.unpack(cls.FMT, b)
        return cls(nonce, bool(acc), reason)


@dataclass(frozen=True)
class StopRequest:
    reason: int
    FMT = "<H"

    def pack(self) -> bytes:
        return struct.pack(self.FMT, self.reason)

    @classmethod
    def unpack(cls, b: bytes) -> StopRequest:
        return cls(*struct.unpack(cls.FMT, b))


@dataclass(frozen=True)
class ControllerStatus:
    state: ControllerState
    reservation: ReservationState
    flags: StatusFlag
    valid: StatusFlag  # which input flags are currently valid (diagnosed OK)
    active_faults: ControllerFault
    latched_faults: ControllerFault
    runtime_remaining_s: int
    battery_mv: int
    last_reason: int
    boot_count: int
    FMT = "<BBHHIIHHHH"

    def pack(self) -> bytes:
        return struct.pack(
            self.FMT,
            self.state,
            self.reservation,
            self.flags,
            self.valid,
            self.active_faults,
            self.latched_faults,
            self.runtime_remaining_s,
            self.battery_mv,
            self.last_reason,
            self.boot_count & 0xFFFF,
        )

    @classmethod
    def unpack(cls, b: bytes) -> ControllerStatus:
        st, res, fl, va, af, lf, rr, mv, lr, bc = struct.unpack(cls.FMT, b)
        return cls(
            ControllerState(st),
            ReservationState(res),
            StatusFlag(fl),
            StatusFlag(va),
            ControllerFault(af),
            ControllerFault(lf),
            rr,
            mv,
            lr,
            bc,
        )

    def has(self, flag: StatusFlag) -> bool | None:
        """Tri-state read: None when the controller marks the input invalid."""
        if not self.valid & flag:
            return None
        return bool(self.flags & flag)

    def as_dict(self) -> dict[str, object]:
        return {
            "state": self.state.name,
            "reservation": self.reservation.name,
            "flags": [f.name for f in StatusFlag if self.flags & f],
            "invalid": [f.name for f in StatusFlag if not self.valid & f],
            "active_faults": [f.name for f in ControllerFault if self.active_faults & f],
            "latched_faults": [f.name for f in ControllerFault if self.latched_faults & f],
            "runtime_remaining_s": self.runtime_remaining_s,
            "battery_v": self.battery_mv / 1000,
            "boot_count": self.boot_count,
        }


# --- transport ----------------------------------------------------------------


class ControllerLink(Protocol):
    """Byte transport. Non-blocking: send queues bytes, receive returns what arrived."""

    def send(self, data: bytes) -> None: ...

    def receive(self) -> bytes: ...


class SerialLink:
    """UART transport for bench/vehicle use (requires pyserial)."""

    def __init__(self, port: str, baud: int) -> None:
        try:
            import serial  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - hardware only
            raise RuntimeError("pyserial is required for the serial link") from exc
        self._ser = serial.Serial(port, baud, timeout=0, write_timeout=0.05)

    def send(self, data: bytes) -> None:  # pragma: no cover - hardware only
        self._ser.write(data)

    def receive(self) -> bytes:  # pragma: no cover - hardware only
        n = self._ser.in_waiting
        return self._ser.read(n) if n else b""
