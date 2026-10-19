# Independent safety controller

Status: **specification + executable reference model** (`sc_model/core.py`). The C firmware
for the real microcontroller is Phase 4. It must reproduce the model's behaviour, and the
scenario suite is to be re-run against it on a hardware-in-the-loop bench.

## Responsibilities

- Read its own inputs: neutral A, neutral B, parking brake, hood, doors, engine running (tach),
  movement (VSS), key-on, lock command, battery voltage, and read-back of its own outputs.
- Run the manual-gearbox reservation procedure (`../../docs/safety_design.md`).
- Accept or refuse START_REQUEST, then sequence: ignition enable → 2 s pre-crank → start request for at most
  3 s → running, or a failed start.
- End a remote run on: runtime (≤ 900 s hard cap, whatever the Pi asks), heartbeat loss > 1 s,
  interlock lost or unknown, door opened, stall. Hand over if the key is turned on.
- Latch in NVM: movement while outputs active, neutral contradiction > 0.5 s, output read-back mismatch
  > 0.3 s, stop not confirmed within 8 s. Clear only with the physical service input.

## Firmware rules (for Phase 4)

- Super-loop at ≥ 100 Hz. No dynamic memory. The window watchdog is serviced only at the end of a complete loop.
- Output pins configured as OFF before the clock tree and peripherals are set up. External pull-downs keep
  them OFF during reset and while the MCU is unprogrammed.
- Brown-out reset enabled. Reset cause recorded (power / watchdog / brownout) and reported in STATUS.
- Inputs sampled with debounce. Resistor-coded switches give OPEN / CLOSED / FAULT (FAULT = invalid).
- NVM writes only when a latch changes (wear). Double-buffered with a CRC; a corrupt record reads as latched.
- Protocol exactly as `src/gt86_remote_start/controller_protocol.py`, including CRC-16/CCITT-FALSE and the nonce rules.
- Candidate MCUs: AEC-Q100 parts with an independent watchdog and ECC flash (e.g. NXP S32K1 family).
  Pair with an external window watchdog.

## Parameters (proposed, not validated)

See `ScParams` in `sc_model/core.py`: heartbeat timeout 1.0 s, reservation timeout 180 s, engine
stop after lock 6 s, pre-crank 2 s, max crank 3 s, hard max runtime 900 s, min start voltage 12.0 V,
contradiction 0.5 s, output read-back 0.3 s, stop confirm 8 s.
