# Installation plan

## A. Development computer (Windows or Linux), works today

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate      Linux: source .venv/bin/activate
pip install -e ".[dev]"
pytest                                   # 137 tests
gt86-remote-start simulate               # 20 scenarios, summary
gt86-remote-start simulate 1 16 -v       # chosen scenarios with full event log
gt86-remote-start console                # drive the simulator by hand (type "reserve", "wait 4", "lock", "wait 5", "status"; "help" lists all)
gt86-remote-start check-config --config config.yaml
```

## B. Raspberry Pi OS Lite (simulation mode only in this release)

1. Flash Raspberry Pi OS Lite (64-bit) to a high-endurance card. Enable SSH, or keep it offline and use the serial console.
2. Turn off unneeded writes: `sudo systemctl disable --now apt-daily.timer apt-daily-upgrade.timer`; consider
   the overlay filesystem (`raspi-config` → Performance → Overlay FS) with `/var/lib/gt86-remote-start` on a
   separate writable partition.
3. Enable the hardware UART for the controller link (`raspi-config` → Interface → Serial: login shell **No**, hardware **Yes**).
4. Copy the project to the Pi and run `sudo deploy/install.sh`. It creates the `gt86rs` system user, a
   virtualenv in `/opt/gt86-remote-start`, the config in `/etc/gt86-remote-start/config.yaml`, and the
   systemd unit.
5. Check: `systemctl status gt86-remote-start`, `journalctl -u gt86-remote-start -f`, and
   `/var/lib/gt86-remote-start/logs/status.json` (state, faults, controller status, recent events).
6. Config changes: edit `/etc/gt86-remote-start/config.yaml`, run `gt86-remote-start check-config --config …`, then
   `sudo systemctl restart gt86-remote-start`.

Restart behaviour: the unit uses `Type=notify` with `WatchdogSec=2`. A hung loop is killed and restarted. A
restart can never start the engine. It goes INITIALIZING → SELF_TEST, and if the controller reports a remote
run still active, the Pi asks it to stop. The controller has already stopped it after 1 s without heartbeats.

## C. Phase 7: controlled validation before any live activation

Live outputs stay locked in code (`LIVE_OUTPUTS_PERMITTED = False`) until **every** step below is done.
The lock is then removed only by a deliberate, reviewed code change.

1. Resolve blockers B1–B7 (`vehicle_research.md`). Get the service manual and the interface module's
   installation guide for the exact car.
2. Bench (no car): controller firmware on its board, every input driven by a signal generator. Run the
   scenario suite against the real firmware (hardware-in-the-loop) and inject wiring faults: open, short to
   ground, short to 12 V.
3. Install the interface module **by its own procedure**, without our controller connected. Verify normal
   key operation and the factory immobilizer.
4. Install the sensors and the controller with the start trigger **disconnected** (LED load only). Drive
   every reservation and cancel path on the car with the wheels chocked. Compare logs from the Pi and the controller.
5. Connect the trigger with the car on level ground, wheels chocked, in neutral, outdoors, with an observer at the
   key. First crank tests with the fuel pump fuse removed, then live.
6. Deliberate negative tests: in gear with the clutch released (wheels chocked, observer), hood open, door
   open, brake off, battery low, link unplugged, Pi power pulled, controller reset.
7. Review and sign-off by a qualified automotive electronics professional. Check that the law and your insurer allow it.
