"""Structured, wear-aware event log.

Each event is one JSON line with wall-clock and monotonic timestamps.

Storage wear: routine (INFO) events are buffered in RAM and written in one
append every ``flush_interval_s`` (or when the buffer fills). WARNING and
CRITICAL events are written and fsync'ed immediately, so the events that
explain a fault survive a sudden power loss. Files rotate by size.
"""

from __future__ import annotations

import json
import os
import time
from collections import deque
from collections.abc import Callable
from enum import Enum
from pathlib import Path
from typing import Any

from .clock import Clock
from .fault_codes import Severity

LEVELS = {"info": Severity.INFO, "warning": Severity.WARNING, "critical": Severity.CRITICAL}


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.name
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, float):
        return round(value, 4)
    return value


class EventLogger:
    def __init__(
        self,
        clock: Clock,
        path: str | Path | None = None,
        flush_interval_s: float = 30.0,
        max_bytes: int = 1_000_000,
        backup_count: int = 5,
        buffer_limit: int = 200,
        recent_limit: int = 500,
        echo: Callable[[str], None] | None = None,
    ) -> None:
        self._clock = clock
        self._path = Path(path) if path else None
        self._flush_interval = flush_interval_s
        self._max_bytes = max_bytes
        self._backups = backup_count
        self._buffer: list[str] = []
        self._buffer_limit = buffer_limit
        self._recent: deque[dict[str, Any]] = deque(maxlen=recent_limit)
        self._last_flush = clock.monotonic()
        self._echo = echo
        if self._path:
            self._path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, event: str, level: str = "info", **fields: Any) -> dict[str, Any]:
        if level not in LEVELS:
            raise ValueError(f"unknown log level {level!r}")
        record = {
            "wall": round(self._clock.wall(), 3),
            "mono": round(self._clock.monotonic(), 3),
            "level": level,
            "event": event,
            **{k: _jsonable(v) for k, v in fields.items()},
        }
        self._recent.append(record)
        if self._echo:
            self._echo(format_record(record))
        if self._path:
            self._buffer.append(json.dumps(record, separators=(",", ":")))
            urgent = LEVELS[level] >= Severity.WARNING
            if urgent or len(self._buffer) >= self._buffer_limit:
                self.flush(sync=urgent)
        return record

    def maybe_flush(self) -> None:
        if self._clock.monotonic() - self._last_flush >= self._flush_interval:
            self.flush()

    def flush(self, sync: bool = False) -> None:
        self._last_flush = self._clock.monotonic()
        if not self._path or not self._buffer:
            return
        data = "\n".join(self._buffer) + "\n"
        self._buffer.clear()
        self._rotate_if_needed(len(data))
        with open(self._path, "a", encoding="utf-8") as fh:
            fh.write(data)
            if sync:
                fh.flush()
                os.fsync(fh.fileno())

    def _rotate_if_needed(self, incoming: int) -> None:
        assert self._path
        try:
            size = self._path.stat().st_size
        except FileNotFoundError:
            return
        if size + incoming <= self._max_bytes:
            return
        for i in range(self._backups - 1, 0, -1):
            src = self._path.with_suffix(f".{i}.jsonl")
            if src.exists():
                src.replace(self._path.with_suffix(f".{i + 1}.jsonl"))
        self._path.replace(self._path.with_suffix(".1.jsonl"))

    def recent(self, n: int | None = None, event: str | None = None) -> list[dict[str, Any]]:
        items = [r for r in self._recent if event is None or r["event"] == event]
        return items if n is None else items[-n:]

    def events(self) -> list[str]:
        return [r["event"] for r in self._recent]


def format_record(record: dict[str, Any]) -> str:
    stamp = time.strftime("%H:%M:%S", time.localtime(record["wall"]))
    extra = " ".join(
        f"{k}={v}" for k, v in record.items() if k not in ("wall", "mono", "level", "event")
    )
    level = "" if record["level"] == "info" else f"[{record['level'].upper()}] "
    return f"{stamp} - {level}{record['event']}" + (f" ({extra})" if extra else "")
