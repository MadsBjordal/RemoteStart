# Testing

Run `pytest` (130 tests) and `gt86-remote-start simulate` (20 scenarios). Everything runs on Windows or Linux
in deterministic virtual time; the whole suite takes about 1.5 s.

**These results show the software model behaves as specified. They do not validate hardware or vehicle safety.**

## Unit tests

| File | Covers |
|---|---|
| `test_fob_listener.py` | 3 presses in window; double press; window exceeded; debounce; duplicate ids; unauthenticated, stale and future events; unlock resets; one sequence triggers once; cooldown; configurable count |
| `test_config.py` | Shipped config is valid and safe; live outputs, disabled safety requirements, auto-restart, bad runtimes, wrong types, unknown keys all refused |
| `test_controller_protocol.py` | CRC check value; round trip through noise and split reads; corrupt frame dropped and counted; status tri-state |
| `test_state_machine.py` | Table covers every state; forbidden transitions (no start without reservation, no skipping the safety check, no restart while running, no retry, lockout only via service); FAULT/LOCKOUT reachable from all |
| `test_safety_manager.py` | Every interlock blocks; unknown blocks; a single neutral source is never enough; Pi↔controller disagreement; system conditions; voltage hold time |
| `test_engine_monitor.py` | Running needs two signals for 1 s; start failure; stall; implausible rpm; signal loss; runtime exceeded; OBD-II decoding |
| `test_diagnostics.py` | Latched faults persist across restarts; corrupt store latches; service clear needs the phrase; log buffering, fsync of warnings, rotation |
| `test_safety_core.py` | Firmware model: safe power-on defaults; refusals without reservation or heartbeat; nonce replay; unknown input; latch survives power cycle |
| `test_timeouts.py` | Runtime limit; crank bounded by the controller; reservation lock press cannot count toward a start; reservation timeout; unconfirmed stop latches |
| `test_watchdog.py` | Controller stops on heartbeat loss; hardware watchdog drops outputs; Pi boot never starts; Pi restart mid-run stops the engine; init timeout; lockout needs both service actions |
| `test_main.py` | CLI: config check, bad config, vehicle mode locked out, simulate |
| `test_fault_injection.py` | Runs every scenario below |

## Scenarios (simulator/scenarios.py) and expected safe state

| # | Scenario | Expected safe state (all pass in simulation) |
|---|---|---|
| 1 | Valid triple lock | Starts once, runs, stops at the runtime limit, RESERVATION_REQUIRED; a second triple lock does nothing (reservation used up) |
| 2 | Accidental double press | No start; RESERVATION_VALID |
| 3 | Excessive delay | No start; sequence expires |
| 4 | Duplicate receiver events | Repeats, bounces and unauthenticated locks count at most once; no start |
| 5 | Parked in gear | Reservation cannot be made; no crank. Neutral A stuck at "neutral" → contradiction → LOCKOUT |
| 6 | Parking brake released | Reservation cancelled; start refused |
| 7 | Hood opened | Reservation cancelled; a running engine stops |
| 8 | Door opened after reservation | Reservation cancelled; start refused |
| 9 | Engine already running | Starter never engaged; STANDBY |
| 10 | Engine fails to start | One crank ≤ 3 s, no retry, START_FAILED, RESERVATION_REQUIRED |
| 11 | Battery voltage drops | Start refused below 12.0 V; a run with charge voltage < 12.5 V for 5 s stops |
| 12 | Controller disconnects | Controller stops the engine after 1 s; Pi FAULT; recovers to RESERVATION_REQUIRED without restarting |
| 13 | Pi crashes | Controller stops the engine; the rebooted Pi does not start it |
| 14 | GPIO / serial failure | Corrupt frames rejected; treated as link loss → engine stopped, FAULT; a failing Pi input interface blocks starting |
| 15 | Engine monitoring signal lost | Pi requests stop → FAULT; engine stopped |
| 16 | Unexpected movement | Outputs off within 0.2 s; latched LOCKOUT on Pi and controller; no restart |
| 17 | Contradictory sensors | Pi's neutral sources disagree at the safety check → LOCKOUT; no crank |
| 18 | Controller restarts mid-sequence | Outputs drop, reservation lost, Pi → RESERVATION_REQUIRED; watchdog reset during crank → one crank only |
| 19 | Stuck ignition relay | Output read-back mismatch → controller latch → Pi LOCKOUT |
| 20 | Whole-system power restored | No start; reservation not kept |

## Not covered yet

Hardware-in-the-loop against real firmware (Phase 4); timing jitter and real serial behaviour; EMC;
real sensor bounce; long-duration soak tests; the real lock-event interface.
