# GT86 / FR-S remote start (Raspberry Pi)

A custom, offline remote-start system for a 2012 Toyota GT86 / Scion FR-S with a 6-speed manual gearbox:
**press the original fob's LOCK button three times within 3 s** to start, and three times again to stop.

> **Status: Phases 1 and 2. Simulation only.** Live ignition and starter outputs are locked out in code
> (`config.LIVE_OUTPUTS_PERMITTED = False`). Nothing here has been validated on a vehicle. Software
> simulation does not prove the system is safe.

## How it is built

- **Raspberry Pi Zero 2 W**: application controller (fob sequence, state machine, diagnostics). It has
  **no** ignition or starter output; it can only *ask*.
- **Independent safety controller** (automotive MCU, Phase 4): reads its own sensors, runs the
  manual-gearbox reservation procedure, and alone decides and sequences a start. Outputs default OFF.
  It stops a remote run if the Pi goes silent.
- **Certified remote-start / immobilizer data interface** (e.g. Fortin EVO-ONE for the exact car): high-current
  switching, immobilizer authorisation, clutch-interlock handling. The factory immobilizer is never defeated.

Read `docs/vehicle_research.md` first: it lists what is verified, what is proposed and what is still
unresolved. The biggest open question is whether this car exposes a *fob-specific* lock signal at all (B1).

## Quick start (Windows or Linux)

```bash
python -m venv .venv && .venv/Scripts/activate      # Linux: source .venv/bin/activate
pip install -e ".[dev]"
pytest                                   # 130 tests
gt86-remote-start simulate               # 20 safety scenarios
gt86-remote-start simulate 1 -v          # one scenario with its event log
gt86-remote-start console                # try it by hand: reserve, wait 4, lock, status
```

## Layout

```text
src/gt86_remote_start/   Pi application (config, fob_listener, state_machine, remote_start,
                         safety_manager, controller_protocol, vehicle/immobilizer interfaces,
                         engine_monitor, obd_pids, diagnostics, fault_codes, event_logger, main)
firmware/safety_controller/  controller spec + executable reference model (sc_model)
simulator/               vehicle, engine, fob, link, watchdog, fault injection, scenarios
tests/                   unit, integration and scenario tests
deploy/                  systemd unit and Raspberry Pi install script
docs/                    vehicle_research (feasibility), architecture, safety_design,
                         hardware_requirements, installation_plan, testing
```
