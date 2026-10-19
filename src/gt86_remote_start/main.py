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


def cmd_console(_args: argparse.Namespace) -> int:
    from simulator.console import run_console

    return run_console()


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
