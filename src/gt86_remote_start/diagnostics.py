"""Fault management and diagnostic reports.

``FaultManager`` keeps the set of active faults. Non-latching faults can be
cleared when their cause goes away; latching faults stay until
``service_clear`` is called deliberately (a service action, never automatic).
Latched faults are persisted to a small file so a reboot does not forget them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .clock import Clock
from .event_logger import EventLogger
from .fault_codes import FaultCode, Severity, info

SERVICE_CLEAR_PHRASE = "I have found and fixed the cause"


@dataclass
class ActiveFault:
    code: FaultCode
    first_seen: float
    last_seen: float
    count: int
    detail: str
    latched: bool


class FaultManager:
    def __init__(
        self,
        clock: Clock,
        logger: EventLogger,
        latch_enabled: bool = True,
        persist_path: str | Path | None = None,
    ) -> None:
        self._clock = clock
        self._log = logger
        self._latch = latch_enabled
        self._faults: dict[FaultCode, ActiveFault] = {}
        self._persist = Path(persist_path) if persist_path else None
        self._load_latched()

    def raise_fault(self, code: FaultCode, detail: str = "") -> ActiveFault:
        now = self._clock.monotonic()
        meta = info(code)
        existing = self._faults.get(code)
        if existing:
            existing.last_seen = now
            existing.count += 1
            return existing
        fault = ActiveFault(code, now, now, 1, detail, meta.latching and self._latch)
        self._faults[code] = fault
        level = {Severity.INFO: "info", Severity.WARNING: "warning", Severity.CRITICAL: "critical"}[
            meta.severity
        ]
        self._log.log(
            "Fault raised",
            level=level,
            code=code,
            latched=fault.latched,
            description=meta.description,
            detail=detail,
        )
        if fault.latched:
            self._save_latched()
        return fault

    def clear(self, code: FaultCode) -> bool:
        fault = self._faults.get(code)
        if not fault or fault.latched:
            return False
        del self._faults[code]
        self._log.log("Fault cleared", code=code)
        return True

    def service_clear(self, confirmation: str) -> list[FaultCode]:
        """Clear latched faults. Requires the exact confirmation phrase."""
        if confirmation != SERVICE_CLEAR_PHRASE:
            self._log.log("Service clear refused", level="warning", reason="bad confirmation")
            return []
        cleared = list(self._faults)
        self._faults.clear()
        self._save_latched()
        self._log.log("Service clear of all faults", level="warning", cleared=cleared)
        return cleared

    def is_active(self, code: FaultCode) -> bool:
        return code in self._faults

    def active(self) -> list[ActiveFault]:
        return list(self._faults.values())

    def has_latched(self) -> bool:
        return any(f.latched for f in self._faults.values())

    def has_critical(self) -> bool:
        return any(info(f.code).severity is Severity.CRITICAL for f in self._faults.values())

    def blocking_start(self) -> list[FaultCode]:
        """Faults that must prevent a new start attempt."""
        return [
            f.code
            for f in self._faults.values()
            if f.latched or info(f.code).severity is Severity.CRITICAL
        ]

    def snapshot(self) -> list[dict[str, Any]]:
        return [
            {
                "code": f.code.name,
                "number": int(f.code),
                "latched": f.latched,
                "count": f.count,
                "detail": f.detail,
                "description": info(f.code).description,
            }
            for f in self._faults.values()
        ]

    def _save_latched(self) -> None:
        if not self._persist:
            return
        latched = [
            {"code": f.code.name, "detail": f.detail} for f in self._faults.values() if f.latched
        ]
        tmp = self._persist.with_suffix(".tmp")
        self._persist.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(latched), encoding="utf-8")
        tmp.replace(self._persist)

    def _load_latched(self) -> None:
        if not self._persist or not self._persist.exists():
            return
        try:
            items = json.loads(self._persist.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # A corrupt fault store is itself treated as a latched fault.
            items = [{"code": FaultCode.INTERNAL_ERROR.name, "detail": "fault store unreadable"}]
        now = self._clock.monotonic()
        for item in items:
            code = FaultCode[item["code"]] if item["code"] in FaultCode.__members__ else (
                FaultCode.INTERNAL_ERROR
            )
            self._faults[code] = ActiveFault(code, now, now, 1, item.get("detail", ""), True)
        if self._faults:
            self._log.log(
                "Latched faults restored after restart",
                level="warning",
                codes=list(self._faults),
            )


def diagnostic_report(
    state: str,
    faults: FaultManager,
    logger: EventLogger,
    controller_status: dict[str, Any] | None,
    vehicle_status: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "state": state,
        "faults": faults.snapshot(),
        "controller": controller_status,
        "vehicle": vehicle_status,
        "recent_events": logger.recent(30),
    }
