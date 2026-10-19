# Safety design

> Simulation results do not validate safety. Nothing in this project may be
> connected to live ignition or starter circuits until the Phase 7 validation
> in `installation_plan.md` has been completed and signed off by a qualified
> automotive electronics professional.

## Top hazards

| # | Hazard | Main defences |
|---|---|---|
| H1 | Starter cranks with a gear engaged: the car lurches into people or objects | Two independent neutral sources that must agree; reservation procedure proves the car was left out of gear; parking brake required; movement detection latches; crank time limited by the controller; certified clutch-interlock handling (no simple bypass) |
| H2 | Engine starts unintended (bug, reboot, power return, noise) | Start needs a fresh nonce-bound request + a VALID reservation + every interlock verified by the controller's own inputs; outputs default OFF; reservation in RAM only (lost on power loss); never starts on boot; a sequence overlapping the reservation's own lock press is ignored; one-shot cooldown |
| H3 | Engine keeps running unattended (stuck output, lost comms) | Controller stops on heartbeat loss (1 s), runtime cap in controller (≤ 900 s) and Pi; output readback latches a stuck relay; stop must be confirmed within 8–10 s or it latches; final power-kill relay (Phase 7) |
| H4 | Running engine and a person entering or opening the hood (fan, belt) | Hood or door opening ends the run immediately |
| H5 | Theft: the remote start makes the car drivable | Immobilizer authorisation only from a certified interface in its remote mode; a driver turning the key hands over; doors locked; a door opening ends the run |
| H6 | Exhaust in an enclosed space (garage) | **Cannot be detected by this system.** Owner procedure: never reserve the car in an enclosed space. Consider a CO sensor input in Phase 7 |
| H7 | Battery drained so the car cannot be started by hand | Start refused below 12.0 V; run ended if charge voltage < 12.5 V for 5 s; Pi power management (hardware_requirements §2) |

## Rules applied everywhere

1. **Unknown is unsafe.** Every sensor value is tri-state (`None` = unknown). Unknown blocks starting and ends a run.
2. **One neutral reading is never proof.** Sources A and B must agree. Disagreement for 0.5 s is a latched fault (`SENSOR_CONTRADICTION`).
3. **Fail-safe wiring (Phase 7).** Each safety input is wired so that a broken wire reads as the unsafe state, and is resistor-coded so open/short faults are detectable (they report as invalid).
4. **No automatic retry.** A failed start consumes the reservation; the driver must re-establish it in person. `automatic_restart_enabled` cannot be turned on.
5. **Latch when the cause is uncertain.** Movement, contradiction, stuck output and unconfirmed stop latch in both the Pi (persisted file) and the controller (NVM). Clearing needs a person: the exact phrase on the Pi, and the controller's physical service input.
6. **Bounded authority.** A START_REQUEST carries a single-use nonce and a validity window (3 s). The controller refuses replays, requests without a fresh heartbeat, and any request it cannot verify.
7. **Stopping is never blocked.** FAULT and LOCKOUT are reachable from every state and keep sending STOP while the controller is remote-active.
8. **Configuration cannot weaken safety.** Validation refuses disabling any `safety.require_*`, enabling live outputs (locked in code), or runtimes above 1200 s.

## Reservation procedure (manual gearbox)

Concept modelled on the documented commercial "ready/reservation mode" for manual
cars (Fortin). It **must be checked against the chosen interface's own procedure**
before vehicle use, and the two must not conflict.

1. Stop the car on level ground. Engine running on the key. Gear lever in neutral, foot off the clutch.
2. Apply the parking brake (the controller looks for the *applying* edge). Controller → ARMED and holds ignition.
3. Remove the key. The engine keeps running. → KEY_REMOVED. If the engine stalls now, the reservation is cancelled (it may have been in gear).
4. Get out and close the door. → DRIVER_EXITED.
5. Press LOCK on the fob. The hold is released and the engine stops within 6 s. → VALID.

Cancelled by: any door opening after VALID, hood open, parking brake released, neutral lost or disputed, movement,
key turned on, a sensor going invalid, heartbeat loss during the hold, 180 s without completing, or power loss.

After this, three LOCK presses within 3 s (and not before the reservation was
complete) request a start. The reservation is then used up.

## Scenario evidence (simulation only)

See `testing.md`. All 20 scenarios reach their expected safe state in the model,
including scenarios 1–18 from the brief.

## Known residual risks / limitations

- The simulated engine, vehicle and sensors are simplistic. Real sensors bounce,
  drift and fail in ways not modelled.
- Fob-specific lock detection is unresolved (vehicle_research B1).
- The Pi's stop requests go over the same UART as everything else; the
  independent stop paths are the controller's own logic and, in Phase 7, the power-kill relay.
- The 10 s activation cooldown means a stop by fob is accepted only from 10 s after the start sequence.
- A person who deliberately re-establishes a reservation in an unsafe place (garage, slope) is not protected by this system.
