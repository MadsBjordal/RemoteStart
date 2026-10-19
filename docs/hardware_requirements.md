# Hardware requirements and integration

Labels: **[PROPOSED]** = our engineering design; **[UNRESOLVED]** = needs vehicle-specific
confirmation. No wire colours, pin numbers or connector locations are given:
none are verified for this car. Part families named are examples to evaluate, not verified selections.

## 1. Components

| Component | Why | Interfaces | Status |
|---|---|---|---|
| Raspberry Pi Zero 2 W, Raspberry Pi OS Lite (64-bit) | Application controller: fob sequencing, state machine, diagnostics, logging | UART to the safety controller (through an isolator); SPI to a CAN controller for OBD-II; isolated digital input for the lock-event tap | [PROPOSED]. A Pi 3A+/4 also works but draws more standby power. The Zero 2 W suffices: the load is tiny |
| High-endurance microSD (industrial/"endurance" grade) + read-mostly filesystem | Survives power loss and write wear | — | [PROPOSED] |
| Automotive power supply for the Pi: wide-input buck converter rated for ISO 16750-2 / ISO 7637-2 transients (load dump, cranking dip to ~6 V), 5 V 2.5 A | Do not feed the Pi from raw 12 V | Battery-fed fuse → reverse-polarity protection (ideal-diode controller, e.g. LM74700-Q1 class) → TVS (automotive SMC-class) → buck → hold-up (supercapacitor) | [PROPOSED] |
| Hold-up + shutdown signal | Ride through cranking dips; orderly shutdown on long power loss | GPIO "power-fail" input to the Pi | [PROPOSED] |
| **Independent safety controller**: AEC-Q100 automotive MCU with independent window watchdog (e.g. NXP S32K1 family or similar), external window watchdog (TPS3430 class), EEPROM/flash for latched faults | Sole authority over the start interface; reads its own sensors | Own sensor inputs; UART to the Pi; outputs to the start interface; service input (physical switch inside the case) | [PROPOSED]. Firmware = Phase 4 |
| Output stage: automotive high-side smart switches with current sense / diagnostics (e.g. Infineon PROFET class) and read-back of every output | Drive the start interface's trigger, forced-stop and power-kill inputs | Controller GPIO → smart switch → module input; read-back → controller | [PROPOSED] |
| Final power-kill relay (automotive-rated, normally-open, controller-held) | Lets the controller cut the start interface's power without software help | Controller output | [PROPOSED] |
| Certified remote-start + immobilizer data interface (e.g. Fortin EVO-ONE + T-harness for the exact car) | Ignition/starter switching, immobilizer authorisation, clutch-interlock handling under its own reservation logic | Its documented trigger/status inputs and outputs | [UNRESOLVED]: fitment for the exact variant (vehicle_research B3) |
| Neutral source B: added sensor on the shift mechanism (sealed Hall sensor, two-wire current-loop output) | A single neutral switch is not proof | Controller input (and Pi input via isolator) | [PROPOSED]; source A (factory switch) [UNRESOLVED] |
| Hood pin switch (if no factory one) | Hood-open interlock | Controller + Pi inputs | [PROPOSED] |
| Vehicle-speed or wheel-movement input | Movement detection | VSS pulse input or a 3-axis accelerometer on the controller board as an independent extra | [UNRESOLVED] signal source |
| Tachometer / engine-running input to the controller | Controller's own running confirmation | Pulse input | [UNRESOLVED] signal source |
| CAN controller + **isolated** transceiver (MCP2515 + ISO1050 class), listen-only except standard OBD-II requests | Pi engine monitoring via standard PIDs | SPI to the Pi; OBD-II port | [PROPOSED] |
| Lock-event tap (isolated, high-impedance) | Validated fob command to the Pi and controller | Depends on method A/B/C | [UNRESOLVED] (vehicle_research B1) |
| Enclosure, automotive connectors (sealed, keyed), inline fuses (ATO/ATM) at every tap | Mechanical and fire safety | — | [PROPOSED] |

## 2. Power and standby consumption

- The Pi Zero 2 W idles at roughly 0.5 W, about 1 Ah per day. A parked car would lose a meaningful part of its battery in two to three weeks.
- **[PROPOSED]** The safety controller (µA-class sleep) switches the Pi's supply. It powers the Pi down after a configurable number of days parked, or when resting battery voltage falls below a threshold (e.g. 12.2 V). Remote start is then unavailable until the car is next used. A remote start is never attempted below the start-voltage limit.
- On power loss: the controller's outputs drop at once (hardware default). The reservation is lost (RAM only). Latched faults are kept (NVM). The Pi flushes its log on the power-fail signal.

## 3. Interface specification

| Signal | Direction | Electrical (proposed) | Default at power-up | Fail-safe reading | Protection |
|---|---|---|---|---|---|
| 12 V vehicle inputs (brake, hood, doors, neutral A, key-on) | vehicle → controller | 0–16 V tolerant (survive −16…+40 V): low < 3 V, high > 8 V, Schmitt input, resistor-coded switch for open/short diagnosis | — | broken wire = unsafe state (e.g. "hood open") or "invalid" | series resistor, TVS, RC filter, fused tap |
| Same inputs to the Pi | vehicle → Pi | Optocoupler or isolated digital input, 3.3 V logic on the Pi side | Pi pins as inputs with pull to the unsafe level | — | isolation; Pi GPIO **never** touches 12 V |
| Neutral B sensor | sensor → controller | 2-wire current loop (e.g. 7 mA / 14 mA), out-of-band = invalid | — | invalid | as above |
| Tach / VSS pulses | vehicle → controller | 0–16 V pulse, comparator input | — | no pulses = unknown | as above |
| UART Pi ↔ controller | both | 3.3 V UART 115200 8N1 through a digital isolator (ISO77xx class) | — | silence = link lost; controller stops a run after 1 s | isolator, ESD |
| Start trigger to the interface | controller → module | High-side switch per the module's spec | **OFF** | OFF | fuse; read-back |
| Forced stop / kill | controller → module | Per the module's spec; power-kill relay normally open | **stop asserted / kill relay open** until the controller is healthy | stop | read-back |
| Power-fail warning | PSU → Pi | Open-drain 3.3 V | — | — | — |

Connector requirements: sealed and keyed automotive connectors, strain relief, no
piggy-back crimps; every vehicle tap individually fused as close to the source as
possible; dedicated ground stud.

## 4. Who is responsible for safety, in hardware

- **Independent safety:** the safety controller + its output read-back + the power-kill relay + the certified interface's own interlocks.
- **Pi:** second opinion, user interface (fob sequence), logging. It cannot start the engine.

## 5. Needs vehicle-specific verification before any wiring

Market/build (B2); fob lock signal (B1); immobilizer interface fitment (B3); neutral switch existence and type (B4);
parking-brake switch polarity; door and hood switch presence and polarity; tach and VSS signal sources;
the interface module's trigger/stop input specifications; fuse ratings at each tap.
