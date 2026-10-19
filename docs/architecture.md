# Architecture

## Principle: the Pi asks, the safety controller decides

```text
 Factory fob ──RF──► Factory keyless receiver ──► Body electronics ──► door locks
                         │ (validated lock command; interface UNRESOLVED, see vehicle_research §2)
                         ├─────────────── isolated listen-only tap ───────────────┐
                         ▼                                                        ▼
 ┌──────────────────────────────┐   UART + digital isolator   ┌────────────────────────────────┐
 │ Raspberry Pi Zero 2 W        │ ◄── STATUS / responses ───── │ Independent safety controller  │
 │ (application controller)     │ ─── HEARTBEAT 5 Hz ───────► │ (automotive MCU, own inputs,   │
 │  fob_listener (3× lock)      │ ─── START_REQUEST(nonce) ─► │  hardware watchdog, NVM latch) │
 │  state machine               │ ─── STOP_REQUEST ─────────► │                                │
 │  engine_monitor (OBD-II)     │                              │  reservation procedure         │
 │  diagnostics + event log     │   NO ignition/starter        │  start/run/stop sequencing     │
 │  NO high-current outputs     │   outputs on the Pi at all   │  outputs default OFF           │
 └──────────┬───────────────────┘                              └──────┬───────────────┬─────────┘
            │ OBD-II (isolated CAN, listen/                           │ own inputs     │ gated start trigger,
            │ standard PIDs only)                                     │ (neutral A+B,  │ forced-stop input,
            ▼                                                         │ brake, hood,   │ power kill relay
      Engine ECU                                                      │ doors, tach,   ▼
                                                                      │ VSS, key-on,  ┌────────────────────────┐
                                                                      │ output        │ Certified remote-start │
                                                                      │ readback)     │ + immobilizer data     │
                                                                      ▼               │ interface (e.g. Fortin │
                                                               vehicle sensors        │ EVO-ONE + T-harness)   │
                                                                                      └──────────┬─────────────┘
                                                                                                 ▼
                                                                         ignition / starter / immobilizer (factory)
```

Why this split:

- A software bug, crash or hacked Pi cannot energise anything. The Pi has no
  output to ignition or starter circuits.
- The controller reads its **own** sensors, re-checks every interlock, and limits
  crank time and run time itself. It stops a remote run if the Pi's heartbeat
  stops for more than 1 s.
- The high-current switching, the clutch-interlock handling and the immobilizer
  authorisation stay in a certified product. In the model they are the abstract
  outputs `ignition` (remote ignition enable / hold) and `starter` (start request).
  Mapping them onto the module's inputs is Phase 7 work.
- The example `VehicleInterface.request_start()` from the brief was deliberately
  **not** put on the Pi. `VehicleInterface` is read-only; start and stop go
  through `controller_protocol.py`.

## Code map

| File | Responsibility |
|---|---|
| `src/gt86_remote_start/config.py` | YAML loading, type checks, release locks (live outputs locked, safety requirements cannot be disabled) |
| `fob_listener.py` | Triple-lock detection (monotonic time, debounce, de-duplication, stale and unauthenticated events ignored, cooldown) |
| `state_machine.py` | States and the allowed-transition table |
| `remote_start.py` | Orchestrator: per-tick behaviour of each state |
| `safety_manager.py` | Pi-side pre-checks (a second opinion) and Pi↔controller cross-checks |
| `controller_protocol.py` | Framed, CRC-checked protocol; message and status definitions |
| `vehicle_interface.py` | Read-only vehicle status (unknown = `None`); the hardware placeholder refuses to construct |
| `immobilizer_interface.py` | Reports whether an authorised interface is READY; nothing more |
| `engine_monitor.py` + `obd_pids.py` | Running confirmation from two signals; standard OBD-II PIDs only |
| `diagnostics.py`, `fault_codes.py`, `event_logger.py` | Faults (latching, persisted), reports, wear-aware JSON-lines log |
| `main.py`, `sd_notify.py` | CLI, systemd service loop and watchdog |
| `firmware/safety_controller/sc_model/` | **Executable specification** of the safety controller firmware |
| `simulator/` | Vehicle, engine, fob, link, hardware watchdog, power loss, fault injection, scenarios |

The Pi logic is tick-driven and non-blocking: `tick()` runs every 50 ms and
never waits. I/O sits at the edges (`async` vehicle and immobilizer reads,
non-blocking serial). This makes every test deterministic in virtual time.

## Remote-start state machine (Pi)

Entering **FAULT** or **LOCKOUT** is allowed from every state. A transition
outside the table is refused, logged as critical, and leads to FAULT. Every
transition is logged with its reason. In every state except INITIALIZING, FAULT
and LOCKOUT, the supervisor first checks: controller status fresh (≤ 1 s),
no controller latched faults, engine signals plausible, engine monitoring present.

| State | Entered when | Leaves to | Refuses / notes | Timeout |
|---|---|---|---|---|
| SYSTEM_OFF | process created | INITIALIZING (`start()`) | everything | — |
| INITIALIZING | start, fault recovery, service reset | SELF_TEST when a status arrives that was sent ≥ 2 heartbeats after entry | old status frames are ignored | 5 s → FAULT (SELF_TEST_FAILED) |
| SELF_TEST | controller answering | STANDBY if status fresh, engine state known and immobilizer status known; SHUTDOWN_REQUESTED if the controller is in a remote run (Pi restarted mid-run) | latched fault → LOCKOUT | 5 s → FAULT |
| STANDBY | driver in control (key on or engine running) | RESERVATION_VALID / RESERVATION_REQUIRED from controller and vehicle state | triple lock refused (IGNITION_ALREADY_ON) | — |
| RESERVATION_REQUIRED | parked, reservation not established | RESERVATION_VALID, STANDBY | triple lock refused (RESERVATION_NOT_VALID) | — |
| RESERVATION_VALID | controller reports reservation VALID | START_REQUESTED on a triple lock **that began after** the reservation became valid; RESERVATION_REQUIRED if cancelled | a sequence overlapping the reservation's own lock is refused | — |
| START_REQUESTED | accepted triple lock | SAFETY_CHECK; back if `remote_start.enabled` is false | — | one tick |
| SAFETY_CHECK | | Pi pre-check (reservation, neutral A=B=true, brake, hood, doors, stationary, key off, engine stopped, battery 12.0–15.5 V, immobilizer READY, no blocking faults, Pi↔controller agree) → START_REQUEST with a random nonce and 3 s validity → START_AUTHORIZED if accepted | a contradiction latches (LOCKOUT); a refusal is logged with reasons → idle state | 1 s without reply → SHUTDOWN_REQUESTED (it may have accepted) |
| START_AUTHORIZED | controller accepted | CRANK_REQUESTED when the controller reports CRANKING/RUNNING; SHUTDOWN_REQUESTED if it aborted | starting is never assumed | 6 s → SHUTDOWN_REQUESTED |
| CRANK_REQUESTED | controller cranking | ENGINE_RUNNING only when RPM ≥ 500 **and** the running signal are true for 1 s **and** the controller reports REMOTE_RUNNING | failed start → START_FAILED, **no retry** → SHUTDOWN_REQUESTED | 8 s → START_FAILED |
| ENGINE_RUNNING | confirmed running | SHUTDOWN_REQUESTED on: runtime reached (600 s), triple lock (after the 10 s cooldown), any interlock lost or unknown, charge voltage < 12.5 V for 5 s, stall, controller ended the run; STANDBY if the driver turned the key; LOCKOUT on movement or contradiction | starter is never requested while running | max runtime 900 s → RUNTIME_EXCEEDED fault |
| SHUTDOWN_REQUESTED | any stop | RESERVATION_REQUIRED once controller is idle and engine stop is confirmed (reservation is used up); STANDBY if the key is on | STOP resent every 0.5 s | 10 s → ENGINE_STOP_UNCONFIRMED (latched) |
| FAULT | non-latched critical fault | INITIALIZING after 5 s with link healthy and no remote run (transient faults cleared, self test re-run) | sends STOP while the controller is remote-active | — |
| LOCKOUT | latched fault | INITIALIZING only via `service_reset("I have found and fixed the cause")`; controller latches additionally need its physical service input | sends STOP while the controller is remote-active | — |

## Safety controller state machine (reference model)

```text
BOOT ─► IDLE ──START_REQUEST ok──► AUTHORIZED ──2 s pre-crank──► CRANKING ──engine running──► REMOTE_RUNNING
         ▲  (reservation sub-states                │ abort                │ ≥ 3 s: CRANK_TIMEOUT       │ runtime / stall /
         │   NONE→ARMED→KEY_REMOVED→               ▼                      ▼                            │ interlock / hb loss
         │   DRIVER_EXITED→VALID)            SHUTTING_DOWN ◄──────────────┴────────────────────────────┘
         └────────── engine stopped ──────────────┘   (8 s without stop → latch)
 any latched fault ──► FAULT_LATCHED (outputs off; survives power cycles; physical service input clears)
```

The full rules are in `firmware/safety_controller/sc_model/core.py` and
`firmware/safety_controller/README.md`.

## Protocol summary

UART frames: `A5 VER TYPE SEQ16 LEN PAYLOAD CRC16`. Messages: HEARTBEAT (Pi, 5 Hz) → STATUS
(controller replies, see `ControllerStatus`); START_REQUEST(nonce, valid_ms, runtime_s) →
START_RESPONSE(nonce, accepted, reason); STOP_REQUEST(reason) → STOP_RESPONSE; SERVICE_CLEAR
(only effective with the controller's physical service input). Status input bits carry a
validity mask, so the Pi can tell "false" from "unknown". Reasons share one numeric space with
`fault_codes.FaultCode`.
