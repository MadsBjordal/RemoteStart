"""Decoding of standard OBD-II Mode 01 PIDs (SAE J1979 / ISO 15031-5).

Only standardised, publicly documented PIDs are here. No model-specific
(proprietary) CAN identifiers or scalings are included: those for the
FR-S/GT86 are unverified and must not be guessed.
"""

from __future__ import annotations

from collections.abc import Callable

# PID -> (name, number of data bytes, decoder)
_PIDS: dict[int, tuple[str, int, Callable[[bytes], float]]] = {
    0x05: ("coolant_temp_c", 1, lambda d: d[0] - 40.0),
    0x0C: ("engine_rpm", 2, lambda d: (256 * d[0] + d[1]) / 4.0),
    0x0D: ("vehicle_speed_kph", 1, lambda d: float(d[0])),
    0x42: ("control_module_voltage", 2, lambda d: (256 * d[0] + d[1]) / 1000.0),
}


class ObdDecodeError(ValueError):
    pass


def decode_mode01_response(payload: bytes) -> tuple[str, float]:
    """Decode a Mode 01 response payload: 0x41, PID, data bytes."""
    if len(payload) < 3 or payload[0] != 0x41:
        raise ObdDecodeError("not a Mode 01 response")
    pid = payload[1]
    if pid not in _PIDS:
        raise ObdDecodeError(f"unsupported PID 0x{pid:02X}")
    name, size, fn = _PIDS[pid]
    data = payload[2 : 2 + size]
    if len(data) != size:
        raise ObdDecodeError(f"PID 0x{pid:02X} needs {size} data bytes")
    return name, fn(data)


def mode01_request(pid: int) -> bytes:
    if pid not in _PIDS:
        raise ObdDecodeError(f"unsupported PID 0x{pid:02X}")
    return bytes([0x01, pid])
