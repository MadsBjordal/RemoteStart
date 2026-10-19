"""Interactive simulator console (``gt86-remote-start console``).

Time only moves when a command moves it: ``wait`` runs the given seconds and
every other action command runs 0.2 s, so the effect is visible. ``status``
and ``log`` never move time.
"""

from __future__ import annotations

from collections.abc import Callable

from gt86_remote_start.fob_listener import FobCommand

from .fault_injection import FaultInjector
from .harness import SimulationHarness

HELP = """commands (time moves only with wait; other actions run 0.2 s):
  reserve            drive in, park in neutral, do the reservation procedure, lock
  lock [n]           press LOCK n times (default 3), 0.6 s apart
  unlock             press UNLOCK
  wait <seconds>     let time pass
  door open|close    hood open|close    brake on|off    key on|off    gear N|1
  battery <volts>    resting battery voltage (normal 12.6)
  nostart            engine will not start      move <kph>   vehicle moves (0 = stop)
  crash              Pi crashes                  reboot       Pi restarts
  status             what the car and system are doing        log [n]   recent events
  reset              a fresh car and system (clears injected faults)
  help               this list               quit"""

STEP = 0.2


class Console:
    def __init__(self, echo: bool = True) -> None:
        self._echo = echo
        self.reset()

    def reset(self) -> None:
        self.h = SimulationHarness(echo=self._echo)
        self.f = FaultInjector(self.h)
        self.injected: list[str] = []
        self._t0 = self.h.clock.monotonic()
        self.h.boot()

    @property
    def clock(self) -> float:
        return self.h.clock.monotonic() - self._t0

    def status(self) -> str:
        h, w = self.h, self.h.world
        core = h.controller.core
        out = "ignition" if w.ctl_ignition else ""
        out += ("+starter" if out else "starter") if w.ctl_starter else ""
        faults = ", ".join(f["code"] + (" (latched)" if f["latched"] else "")
                           for f in h.faults.snapshot()) or "none"
        lines = [
            f"time      t={self.clock:.1f} s",
            f"Pi        {h.state.name}" + ("" if h.pi_alive else "  (CRASHED: type reboot)"),
            f"engine    {'running' if w.engine.running else 'off'}, {w.engine.rpm:.0f} rpm"
            + (", will not start" if not w.engine.will_start else ""),
            f"car       gear {w.gear}, brake {'on' if w.parking_brake else 'off'}, "
            f"doors {'closed' if w.doors_closed else 'OPEN'}, hood "
            f"{'closed' if w.hood_closed else 'OPEN'}, key {'on' if w.key_on else 'off'}, "
            f"speed {w.speed_kph:.1f} km/h, battery {w.battery_voltage:.1f} V",
            f"controller {core.state.name}, reservation {core.reservation.name}, "
            f"outputs {out or 'off'}",
            f"faults    {faults}",
            f"injected  {', '.join(self.injected) or 'none'}",
        ]
        if h.state.name == "START_AUTHORIZED":
            lines.append("note      pre-crank wait (2 s): type wait 5 to see the crank")
        if h.state.name == "LOCKOUT":
            lines.append("note      latched fault: type reset for a fresh system")
        return "\n".join(lines)

    def _inject(self, label: str, active: bool = True) -> None:
        name = label.split()[0]
        self.injected = [i for i in self.injected if i.split()[0] != name]
        if active:
            self.injected.append(label)

    def handle(self, line: str) -> str | None:
        """Run one command. Returns text to print, or None to quit."""
        parts = line.strip().split()
        if not parts:
            return ""
        cmd, rest = parts[0].lower(), parts[1:]
        arg = rest[0].lower() if rest else ""
        h, w, f = self.h, self.h.world, self.f

        def onoff(name: str, a: str, b: str, setter: Callable[[bool], None]) -> str:
            if arg not in (a, b):
                return f"use: {name} {a}|{b}"
            setter(arg == a)
            h.run(STEP)
            return f"ok: {name} {arg}"

        try:
            if cmd in ("quit", "exit"):
                return None
            if cmd == "help":
                return HELP
            if cmd == "status":
                return self.status()
            if cmd == "log":
                return "\n".join(h.records()[-int(arg or 30):])
            if cmd == "reset":
                self.reset()
                return "ok: fresh car and system"
            if cmd == "reserve":
                h.perform_reservation()
                return f"ok: reservation procedure done -> reservation {h.controller.core.reservation.name}"
            if cmd == "lock":
                n = int(arg or 3)
                h.press_lock(n)
                h.run(STEP)
                return f"ok: pressed LOCK {n}x" + (" (start sequences need wait 5)" if n >= 3 else "")
            if cmd == "unlock":
                h.fob.press(FobCommand.UNLOCK)
                h.run(STEP)
                return "ok: pressed UNLOCK"
            if cmd == "wait":
                seconds = float(arg)
                if not 0 < seconds <= 3600:
                    return "use: wait <seconds> (0-3600)"
                h.run(seconds)
                return f"ok: waited {seconds:g} s"
            if cmd == "door":
                return onoff("door", "close", "open", lambda v: setattr(w, "doors_closed", v))
            if cmd == "hood":
                return onoff("hood", "close", "open", lambda v: setattr(w, "hood_closed", v))
            if cmd == "brake":
                return onoff("brake", "on", "off", lambda v: setattr(w, "parking_brake", v))
            if cmd == "key":
                return onoff("key", "on", "off", lambda v: setattr(w, "key_on", v))
            if cmd == "gear":
                w.gear = (arg or "n").upper()
                h.run(STEP)
                return f"ok: gear {w.gear}"
            if cmd == "battery":
                volts = float(arg)
                f.battery(volts)
                self._inject(f"battery {volts:g} V", volts != 12.6)
                h.run(STEP)
                return f"ok: resting battery {volts:g} V"
            if cmd == "nostart":
                f.engine_wont_start()
                self._inject("nostart")
                h.run(STEP)
                return "ok: engine will not start (until reset)"
            if cmd == "move":
                kph = float(arg)
                f.vehicle_moves(kph if kph > 0 else 0.0)
                w.forced_speed = kph if kph > 0 else None
                self._inject(f"move {kph:g} km/h", kph > 0)
                h.run(STEP)
                return f"ok: vehicle speed {kph:g} km/h" + (" (move 0 stops it)" if kph else "")
            if cmd == "crash":
                f.crash_pi()
                h.run(STEP)
                return "ok: Pi crashed (the safety controller keeps running)"
            if cmd == "reboot":
                f.reboot_pi()
                h.run(STEP)
                return "ok: Pi restarted"
        except ValueError:
            return f"bad value for {cmd}; type help"
        return f"unknown command {cmd!r}; type help"


def run_console(input_fn: Callable[[str], str] = input, print_fn: Callable[[str], None] = print) -> int:
    console = Console(echo=True)
    print_fn(HELP)
    while True:
        try:
            line = input_fn("gt86> ")
        except EOFError:
            return 0
        reply = console.handle(line)
        if reply is None:
            return 0
        if reply:
            print_fn(reply)
