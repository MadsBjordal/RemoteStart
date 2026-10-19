# Phase 1: Vehicle research and feasibility report

Every statement here is labelled:

- **[VERIFIED]**: backed by a source listed at the end, for the stated scope only.
- **[PROPOSED]**: our engineering design choice.
- **[UNRESOLVED]**: not verified for this car. Must be confirmed before hardware work.

No factory wire colours, connector pin numbers, CAN identifiers or ECU signal
names are given anywhere in this project, because none have been verified for
this exact vehicle.

## 1. The exact vehicle

The owner's VIN (kept out of this repository) decodes as follows. The check digit is valid.

| Item | Finding | Status |
|---|---|---|
| Builder | `JF1`: Subaru (Fuji Heavy Industries), Japan | [VERIFIED]: standard world manufacturer code |
| Model | `ZNAA1`: **Scion FR-S, North American market** (ZN6 platform) | [VERIFIED]: matches decoded FR-S VINs |
| Model year | 10th character `E` = **2014** (not 2012) | [VERIFIED]: ISO 3779 year code |
| Transmission | Not reliably encoded. Decoders label `ZNAA1` as 6AT even for cars sold as manual | Owner states **6-speed manual** |
| Key type | Mechanical key with transponder (no Smart Entry / push start) | Owner states key; [UNRESOLVED] the remote/key form |
| Factory alarm / hood switch | | [UNRESOLVED] |

**Consequence:** use the North American FR-S service manual and installation guides for **model
year 2014**. European GT86 documents do not apply, and 2013 documents must be checked against 2014.

## 2. Key fob and lock-command detection

**Factory architecture.** The fob talks to a keyless-entry receiver. A used
receiver part for 2013–2019 BRZ / FR-S is listed under the part number 88831CA030 [VERIFIED as a part listing only]. It
authenticates the rolling code and passes a lock request to the body
electronics, which drives the door-lock actuators. Whether that request is a
discrete wire, a serial line or a CAN message on this car is **[UNRESOLVED]**.

**Important finding.** Fortin's Q&A says *"OEM remote monitoring not available
on the key models of the FR-S/BR-Z"* [VERIFIED as Fortin's statement]. In plain terms, the leading
commercial interface cannot see factory fob presses on key-start cars through its
data connection. That points to the lock request not being readable as data on
these cars. This is the **biggest risk to the "triple-lock with the original
fob" requirement**.

Candidate detection methods, in order of preference:

| # | Method | Fob vs other locks? | Status |
|---|---|---|---|
| A | Tap the validated output of the keyless receiver to the body ECU (isolated, high-impedance, listen only). | Yes, if that line carries only receiver commands. | [UNRESOLVED]: needs the service-manual circuit for the receiver. |
| B | Listen-only CAN for a body "remote lock" message. | Yes, if such a message exists. | [UNRESOLVED]: Fortin's statement suggests none is usable on key models. Never guess IDs. |
| C | Lock-actuator drive pulse **plus** the hazard-lamp "answer-back" flash. On many Toyota/Subaru cars only a wireless lock flashes the hazards; the interior switch does not. | Probably, if confirmed. | [UNRESOLVED]: confirm on the car, including what a second LOCK press does when already locked. |
| D | Lock-actuator pulse alone. | **No.** The interior switch, auto-lock and the key cylinder look the same. | Rejected as the only signal. |

**[PROPOSED]** The software is ready for any of A–C. `fob_listener.py` consumes
`LockEvent(authenticated=...)`, and only an interface that can prove the event
came from the receiver may set `authenticated=True`. If none of A–C works
on this car, the honest alternatives are:

1. accept a different gesture that the car *can* distinguish (for example LOCK, UNLOCK, LOCK
   if the receiver line is readable but repeat-LOCK is not), or
2. use the commercial module's own OEM-remote programming, if Fortin supports it for
   the variant, or
3. accept a second transmitter (which the owner does not want).

We do **not** decode or replay the fob's radio signal, and never will.

## 3. Factory immobilizer

- The immobilizer reads the transponder in the key head and lets the
  engine ECU run only after authorisation. [UNRESOLVED: chip type and
  authorisation sequence for the variant. They are not needed for our design.]
- **Compatible way to keep it [PROPOSED]:** a commercial immobilizer *data
  interface*. Fortin's materials point 2013 Toyota/Scion **regular-key** cars to the
  **EVO-ONE** (remote starter and bypass module in one) with the **TOY3**
  T-harness [VERIFIED as Fortin's product guidance; fitment to a 2014 manual FR-S UNRESOLVED, see B3].
  The module is programmed to the car by its own documented procedure. It grants
  authorisation only during its remote-start mode, and normal key starting stays as
  from the factory. The factory immobilizer is never removed or permanently disabled.
- Fortin's EVO-ALL guide (for another car) is marked *automatic transmission
  only*, so it is a poor fit for a manual. [VERIFIED for that guide]
- The Raspberry Pi software controls **none** of this. It only reads whether an
  authorised interface reports READY (`immobilizer_interface.py`). Without one,
  starting is impossible: a hardware blocker, by design.
- If the variant is a European GT86, Fortin coverage is **[UNRESOLVED]**. A
  European equivalent with documented manual-transmission support is needed.

## 4. Manual gearbox safety

- Fortin's Q&A says manual cars need *"ready/reservation mode every time you
  leave the vehicle"* and that *"no neutral safety switch connections [are]
  needed"* [VERIFIED as Fortin's statement]. Their safety rests on the reservation procedure:
  the engine keeps running with the key out, which shows the car was left out of gear.
- **[PROPOSED]** We do not rely on the procedure alone. The independent controller also requires:
  - two independent neutral indications that agree,
  - parking brake applied,
  - hood closed,
  - doors closed,
  - the vehicle stationary,
  - a continuous Pi heartbeat.
- A factory neutral position switch on the ZN6 manual is **[UNRESOLVED]**.
  Searches found Toyota neutral/clutch-start switch parts, but nothing confirming
  their fitment to the ZN6 manual. Plan for a **dedicated, added neutral
  sensor** (e.g. a sealed Hall sensor on the shift mechanism) as source B, plus the
  factory switch (if one exists) as source A.
- Clutch-start interlock: manual Toyotas use a clutch start switch (generic
  Toyota part listing) [ZN6 fitment UNRESOLVED]. A remote crank must operate
  the starter without the pedal pressed. That function is handled only by the
  certified commercial module under its reservation logic. **We never add a simple
  clutch-switch bypass.**
- The parking brake is a lever with a warning-light switch [type and polarity UNRESOLVED].
  The hood switch may not exist on non-alarm cars. Plan for an added hood-pin switch.

## 5. Engine monitoring

- OBD-II Mode 01 standard PIDs (RPM 0x0C, coolant 0x05, speed 0x0D, module voltage 0x42)
  are SAE J1979 standards [VERIFIED as standards]. ECU support for each on this
  car is [UNRESOLVED]. Implemented in `obd_pids.py`.
- Model-specific CAN signals: **not used**, [UNRESOLVED].
- **[PROPOSED]** The controller uses its own tachometer / engine-running input,
  independent of OBD-II. The Pi uses OBD-II RPM plus a second running signal.

## 6. Feasibility verdict

| Area | Feasible? | Blocker |
|---|---|---|
| Software, simulation, state machine | **Yes** (done in Phase 2) | |
| Immobilizer-compatible start | Probably, with a commercial data interface | B3: interface fitment for the exact variant |
| Manual-transmission safety | Yes, with independent controller + reservation | B4: second neutral source hardware |
| Detecting *original fob* triple-lock | **Uncertain** | **B1: no verified fob-specific lock signal; Fortin says OEM remote monitoring unavailable on key models** |
| Low standby drain | Yes, with design work | B6 |
| Legal use | Country-dependent | B7 |

### Blockers

- **B1. Fob-specific lock signal.** Get the service-manual keyless-entry circuit
  for the exact variant. Bench-probe methods A and C with an isolated logic
  analyser.
- **B2. Exact market and build.** Mostly resolved: VIN = 2014 North American Scion FR-S. Still needed:
  confirm the manual gearbox and the key form on the car, and get the 2014 FR-S service manual.
- **B3. Immobilizer interface.** No source confirms that the EVO-ONE supports a **2014** FR-S with a
  **regular key and a manual gearbox**. The TOY3 listing says "2013+ Toyota/Scion regular key" without
  mentioning manuals, and Fortin's FR-S manual-transmission guidance found so far is for push-to-start cars.
  Confirm with Fortin technical support (1-877-336-7797) on their compatibility page for the exact car,
  and get their installation guide for it.
- **B4. Neutral sensing.** Find out whether a factory neutral switch exists; design the added sensor.
- **B5. Start interface.** High-current switching and clutch-interlock handling stay with
  the certified module. A self-built driver needs professional design and review.
- **B6. Battery drain.** A Pi Zero 2 W draws roughly 0.5 W idle (≈ 1 Ah/day from the car battery). Power management is needed (see hardware_requirements.md).
- **B7. Law and insurance.** Many places forbid leaving a vehicle unattended with
  the engine running on a public road (for example, Great Britain's Road Vehicles
  (Construction and Use) Regulations 1986, reg. 107), and many limit idling. Check
  your local law, and your insurer, before any use.

## Sources

- Fortin Q&A, EVO-ONE and FR-S issues: https://www.wirecolor.pp.fortin.ca/en/qa/66860/evo-one-and-frs-issues
- Fortin Q&A, Scion manual transmission: https://fortin.ca/en/qa/Scion/tag/manual-transmission
- 2013 FR-S VIN example (transmission "Standard"): https://www.hagerty.com/valuation-tools/past-sales/2013-scion-fr~s/JF1ZNAA1XD1726234/8c15ef11-7312-4fe1-abad-3f5cb2d80559
- 2013 FR-S VIN decode (labelled 6AT): https://decodethis.com/web/vin/JF1ZNAA18D1710811
- Fortin Q&A, FR-S OEM remote: https://image-7.wirecolor.com/qa/73394/frs-oem-remote
- EVO-ONE TOY3 bundle, 2013 Toyota/Scion regular key: https://www.bestbuy.ca/en-ca/product/fortin-evo-one-toy3-remote-start-flashlink-updater-bundle-plug-and-play-t-harness-for-2013-toyota-scion-regular-key-all-in-one-security-programmer/19662246
- Fortin EVO-ALL guide (automatic-only note): https://device.report/manual/17112446
- Keyless entry receiver 88831CA030 listing: https://ebay.com/p/1922194265
- Toyota clutch start switch (generic): https://partsouq.com/de/shop/product/8452042010-toyota-switch-assy-clutch-start/1575832
- NHTSA bulletin, FR-S 6AT P/N switch (automatic only): https://static.nhtsa.gov/odi/tsbs/2014/MC-10109417-9999.pdf
