"""Minimal systemd notify client (READY / WATCHDOG), no dependencies."""

from __future__ import annotations

import os
import socket


class SystemdNotifier:
    def __init__(self) -> None:
        addr = os.environ.get("NOTIFY_SOCKET")
        self._addr: str | None = None
        if addr:
            self._addr = "\0" + addr[1:] if addr.startswith("@") else addr

    @property
    def enabled(self) -> bool:
        return self._addr is not None

    def notify(self, message: str) -> None:
        if not self._addr:
            return
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            try:
                sock.connect(self._addr)
                sock.sendall(message.encode())
            except OSError:
                pass

    def ready(self) -> None:
        self.notify("READY=1")

    def watchdog(self) -> None:
        self.notify("WATCHDOG=1")
