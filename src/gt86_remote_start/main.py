"""Command-line entry point.

    gt86-remote-start check-config [--config config.yaml]
    gt86-remote-start simulate [N ...] [-v]       # automated scenarios
    gt86-remote-start console                      # drive the simulator by hand
    gt86-remote-start run [--config config.yaml]   # service loop (systemd)

In this release ``run`` works in ``simulation`` mode only. ``vehicle`` mode is
locked out in code; ``bench`` mode arrives with the safety-controller
hardware (Phase 4).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import signal
import sys
import time
from pathlib import Path

from .config import ConfigError, load_config
from .fault_codes import FaultCode
from .sd_notify import SystemdNotifier
from .vehicle_interface import LiveOutputsLocked


def _load(path: str):  # noqa: ANN202
    try:
        return load_config(path)
    except ConfigError as err:
        print("Configuration is not valid:", file=sys.stderr)
        for problem in err.problems:
            print(f"  - {problem}", file=sys.stderr)
        sys.exit(2)


def cmd_check_config(args: argparse.Namespace) -> int:
    cfg = _load(args.config)
    print(f"Configuration OK: mode={cfg.hardware.mode}, remote_start.enabled="
          f"{cfg.remote_start.enabled}, runtime={cfg.remote_start.runtime_seconds}s")
    return 0


def cmd_simulate(args: argparse.Namespace) -> int:
    from simulator.run_demo import main as run_demo

    return run_demo([*map(str, args.numbers), *(["-v"] if args.verbose else [])])


CONSOLE_HELP = """commands:
  reserve            drive in, park in neutral, do the reservation procedure, lock
  lock [n]           press LOCK n times (default 3), 0.6 s apart
  unlock             press UNLOCK
  wait <seconds>     let time pass
  door open|close    hood open|close    brake on|off    key on|off    gear N|1
  battery <volts>    resting battery voltage
  nostart            engine will not start      move <kph>   vehicle moves
  crash              Pi crashes                  reboot       Pi restarts
  status             state, engine, reservation  log          recent events
  quit"""


def cmd_console(_args: argparse.Namespace) -> int:
    from simulator.fault_injection import FaultInjector
    from simulator.harness import SimulationHarness

    from .fob_listener import FobCommand

    h = SimulationHarness(echo=True)
    f = FaultInjector(h)
    h.boot()
    print(CONSOLE_HELP)
    w = h.world
    while True:
        try:
            line = input("gt86> ").strip().split()
        except EOFError:
            return 0
        if not line:
            continue
        cmd, *rest = line
        arg = rest[0] if rest else ""
        try:
            if cmd == "quit":
                return 0
            elif cmd == "reserve":
                h.perform_reservation()
            elif cmd == "lock":
                h.press_lock(int(arg or 3))
            elif cmd == "unlock":
                h.fob.press(FobCommand.UNLOCK)
            elif cmd == "wait":
                h.run(float(arg))
            elif cmd == "door":
                w.doors_closed = arg == "close"
            elif cmd == "hood":
                w.hood_closed = arg == "close"
            elif cmd == "brake":
                w.parking_brake = arg == "on"
            elif cmd == "key":
                w.key_on = arg == "on"
            elif cmd == "gear":
                w.gear = arg.upper() or "N"
            elif cmd == "battery":
                f.battery(float(arg))
            elif cmd == "nostart":
                f.engine_wont_start()
            elif cmd == "move":
                f.vehicle_moves(float(arg))
            elif cmd == "crash":
                f.crash_pi()
            elif cmd == "reboot":
                f.reboot_pi()
            elif cmd == "status":
                print(f"state={h.state.name} engine_running={w.engine.running} "
                      f"rpm={w.engine.rpm:.0f} reservation={h.controller.core.reservation.name} "
                      f"controller={h.controller.core.state.name}")
                continue
            elif cmd == "log":
                print("\n".join(h.records()[-30:]))
                continue
            else:
                print(CONSOLE_HELP)
                continue
            h.run(0.2)
        except ValueError:
            print("bad value;", CONSOLE_HELP)


async def _service_loop(args: argparse.Namespace) -> int:
    cfg = _load(args.config)
    if cfg.hardware.mode == "vehicle":
        raise LiveOutputsLocked("vehicle mode is locked out in this release")
    if cfg.hardware.mode == "bench":
        print("bench mode needs the safety-controller hardware (Phase 4)", file=sys.stderr)
        return 3
    from simulator.harness import SimulationHarness

    h = SimulationHarness(config=cfg, echo=cfg.logging.echo_to_console)
    notifier = SystemdNotifier()
    notifier.ready()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):  # Windows
            pass
    status_file = Path(cfg.logging.directory) / "status.json"
    status_file.parent.mkdir(parents=True, exist_ok=True)
    next_status = 0.0
    while not stop.is_set():
        started = time.monotonic()
        h.step()
        notifier.watchdog()  # only reached while the control loop keeps running
        if started >= next_status:
            next_status = started + 2.0
            status_file.write_text(json.dumps(h.pi.report(), indent=1, default=str))
        await asyncio.sleep(max(0.0, cfg.tick_seconds - (time.monotonic() - started)))
    h.log.log("Service stopping", reason="signal")
    h.log.flush(sync=True)
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    try:
        return asyncio.run(_service_loop(args))
    except LiveOutputsLocked as exc:
        print(f"{FaultCode.LIVE_OUTPUTS_LOCKED.name}: {exc}", file=sys.stderr)
        return 4


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gt86-remote-start")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("check-config")
    p.add_argument("--config", default="config.yaml")
    p.set_defaults(fn=cmd_check_config)
    p = sub.add_parser("simulate")
    p.add_argument("numbers", nargs="*", type=int)
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(fn=cmd_simulate)
    p = sub.add_parser("console")
    p.set_defaults(fn=cmd_console)
    p = sub.add_parser("run")
    p.add_argument("--config", default="config.yaml")
    p.set_defaults(fn=cmd_run)
    args = parser.parse_args(argv)
    return int(args.fn(args))


if __name__ == "__main__":
    sys.exit(cli())
