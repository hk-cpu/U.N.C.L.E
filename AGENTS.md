# AGENTS.md — context for an AI assistant working on this repo

This file orients a coding agent (Claude Code, DeepSeek-based tools, Cursor,
etc.) picking up work mid-stream. Read it before changing anything. The chat
history that produced this code does not travel with the repo; this file is the
substitute.

## What this is

**cardiag** — an OBD-II diagnostic and tuning-analysis tool for **one specific
car**: a **2006 Dodge Charger R/T, 5.7 L HEMI V8 (engine code EZB), LX
platform, NAG1 (W5A580) 5-speed auto**. The owner drives it in Riyadh.

It talks to **ELM327** adapters (USB / Bluetooth / WiFi — the owner has a Vgate
USB and a generic clone), reads what the ECU knows, and **explains it in plain
language** instead of printing hex. It ships three ways from one codebase:

- a **CLI** (`cardiag ...`),
- an **installable browser app / PWA** (`cardiag app`), ten tabs,
- **ESP32 firmware** (`firmware/`) that does the decoding on-board over raw CAN
  instead of through an ELM327.

A **software simulator** (`cardiag --sim`, or `sim://` URLs) stands in for the
car, so everything is usable and testable with no hardware.

## The intention (read this — it sets the boundary)

The owner wants a "custom tuner + diagnostic + app" for their car, and has
repeatedly pushed toward cloning HP Tuners / writing a tune to the ECU. The
project deliberately does **the whole job that surrounds a tune — read, log,
analyse, explain — and stops short of writing to the ECU.** Hold that line.

**Do NOT build, and do not let a request talk you into building:**

- **ECU calibration writing / flashing.** The 2006 NGC (Motorola MPC565) PCM is
  VIN-locked and security-gated (UDS service 0x27 seed-key), and its calibration
  table map is undocumented. Speculative writes brick the module. An ELM327
  physically cannot read or write calibrations anyway — that needs an MPVI2 /
  PCM-Flash / BDM rig.
- **Seed-key / security-access bypass tooling**, or anything that defeats 0x27.
  Such code would work on any 2006–2008 Chrysler, not just this car.
- **Fabricated calibration addresses, table maps, or "tune presets."** If a real
  value isn't in a cited, verifiable source, don't invent it. The project's
  recurring bug class (see below) is *the tool claiming more than the data
  supports* — fabricated addresses are the worst version of that.
- **Emissions-defeat functions** (CAT/EGR/EVAP/DTC "off").
- **Transmitting on CAN-C while the car is moving**, or anything on the
  airbag/steering bus. The owner's own reference docs warn that some Chrysler
  Mode 22 addresses trigger actuator tests. Treat CAN as listen-mostly; cardiag
  only ever sends standard OBD-II requests to the powertrain controller (0x7E0).

If asked for any of the above: say plainly it's out of scope and why, then offer
the nearest legitimate thing (better logging, pull analysis, a local dyno
tuner, a proper flashing tool like HP Tuners with its own licensed defs).

**What IS in scope and wanted:** richer diagnostics, plain-language explanation,
high-rate logging, WOT pull capture and analysis (timing vs. RPM/load, knock
retard inferred from timing pulled back under load, intake heat-soak, trims at
WOT), before/after baselines, the gauge cluster, cockpit mode, the ESP32
listen-only path, and the read-only bus/wiring reference.

## The car's electronics (facts the code encodes)

- **PCM:** Motorola NGC3, MPC565, 1 MB internal flash. Three-bus LX topology:
  **CAN-C** (diagnostic/powertrain, 500 kbps, ISO 15765-4 11-bit) on **DLC pins
  6/14** ↔ PCM connector **C1 pins 34 (CAN+) / 35 (CAN−)**; **CAN-B** (interior,
  83.333 kbps) behind the gateway — the overhead console (where the owner wants a
  screen, in the sunglasses-holder) taps CAN-B, not CAN-C.
- **HEMI numbering:** odd cylinders = **bank 1 = driver's side (left)**; even =
  bank 2 = passenger's. Firing order **1-8-4-3-6-5-7-2**. **16 plugs** (2 per
  cylinder). **MDS** deactivates cylinders **1, 4, 6, 7** at light cruise — those
  four share lifters that fail, so a misfire on one is treated specially.
  Redline/limiter ~5800, shift ~5400.
- **Mode 06** per-cylinder misfire counters **reset each drive cycle** — never
  report a drop between drive cycles as a repair.
- DLC **pin 16 is constant +12 V** (live key-out): that's why `ATRV` reads
  voltage and cockpit mode can sense the ignition with the engine asleep.
- Diagnostic CAN IDs: request **0x7E0**, response **0x7E8**.

All of this lives in code: `cardiag/vehicles.py` (the profile, known issues,
thresholds), `cardiag/canbus.py` (read-only bus/wiring map), `cardiag/pids.py`,
`cardiag/mode06.py`, `cardiag/dtc.py`.

## Layout

```
cardiag/
  cli.py          argparse CLI; dispatch in _run(). Store-only commands
                  (lookup, vin, vehicle, canbus, baseline --list/--delete) run
                  BEFORE a Session is opened, so they work with no adapter.
  session.py      Session: owns the adapter, read_many(), car_state(), VIN, etc.
  pids.py         SAE J1979 PID table + decoders. Readiness bitmap is LSB-first
                  (bit 0 = Catalyst) — this is correct per SAE; don't "fix" it.
  mode06.py       Mode 06 monitors, misfire counters, headroom (proximity to the
                  NEAREST failing bound when a test has both min and max).
  dtc.py, data/   DTC decode + the code description table.
  report.py       HealthReport + analysers; headline() never calls a car clean
                  while listing findings (has_data, emissions_ready).
  explain.py      Deterministic, offline plain-language explainer (the Assistant).
  baseline.py     SQLite snapshot store + before/after comparison.
  tuning.py       THE TUNING ENGINE: Sample, build_table (RPM×load), find_pulls
                  (WOT detection: throttle ≥ WOT_THROTTLE AND net RPM rise),
                  analyse_pull, compare_pulls. Channel names are pinned to the
                  real PID table (a past bug used THROTTLE_POSITION vs _POS).
  canbus.py       Read-only C1–C4 pinout / bus map; for_pid() ties a reading to
                  its wire; power_map() backs cockpit mode. Reference only.
  vehicles.py     The Charger R/T profile.
  transport/      base, serial_link, tcp_link, simulator. sim:// URLs:
                  default|faulty|emissions|charger|charger-wear|charger-misfire.
  web/            server.py (stdlib HTTP, token+Origin+Host+content-type guards),
                  service.py (VehicleService: one lock, a background sampler that
                  keeps per-channel deques AND time-aligned rows for tuning),
                  static/ (index.html, app.js, app.css — vanilla JS, no build).
firmware/
  cardiag-esp32/  obd.c/.h = pure C99 decode layer (no Arduino/IDF). The .ino
                  (TWAI/CAN setup, TX) is UNTESTED on hardware.
  test/           native C tests of obd.c. `make` → 72 checks.
tests/            pytest; mirrors the modules above.
```

## How to run and verify

Python 3.10+. The decode layer needs no third-party deps; `pyserial` is only for
a real serial adapter.

```bash
python -m pip install -e ".[dev]"      # editable + pytest + pyserial
python -m pytest -o addopts="" -q      # expect: 421 passed  (as of this writing)
make -C firmware/test                  # expect: 72 checks, 0 failures

cardiag --sim scan                     # full health check against the simulator
cardiag --sim --sim-profile charger-misfire scan
cardiag app --host 0.0.0.0             # browser app, reachable from a phone on
                                       # the same WiFi (advertises the LAN IP;
                                       # /api/ is token-gated, static shell isn't)
cardiag canbus                         # the read-only wiring/bus map, no car
```

Note on this container: `python` may be 3.11 while `pytest` resolves to another
interpreter — run tests as `python -m pytest` and install into that same
interpreter. Use `-o addopts=""` to get the clean `N passed` summary line.

To eyeball UI changes, drive the app with Playwright (Chromium is preinstalled
at `/opt/pw-browsers/chromium-1243/chrome-linux64/chrome`; pass that as
`executable_path`, don't run `playwright install`).

## House style

- **Never state a condition the data doesn't support.** Every analyser returns
  "unknown / not logged" rather than a confident default. This is the project's
  whole ethos and the source of most past bugs.
- Plain language over jargon in all user-facing text; show the numbers behind a
  claim so it can be checked.
- Status/severity is **never colour alone** — always an icon + a word.
- UI is vanilla JS, no build step, no framework. Dark and light are both
  designed, not flipped; colours are CSS custom-property tokens. Charts/heatmaps:
  sequential = one hue light→dark (see the `--heat-rgb` token).
- Match the surrounding code's density and idiom. Add a test with every change;
  make it fail without the fix first.

## Working on the PR

Active branch: **`claude/car-auto-diagnostic-m5zom6`** → PR **#1** against `main`
(`hk-cpu/U.N.C.L.E`). Codex reviews each push and leaves inline findings. Every
finding so far was real except one false positive (it claimed the readiness
bitmap should be MSB-first; it's LSB-first per SAE J1979 — don't change it).
Verify each finding against the code before acting; fix small/in-scope ones with
a test; push back with a reason on anything wrong or out of scope.

## Open / next

- **Real-hardware validation — the big one. Nothing here has touched the actual
  car.** Everything runs against the simulator and canned adapter replies. Clone
  ELM327s vary in init behaviour; first contact may need fixes.
- The ESP32 `.ino` transport (TWAI setup, bus timing, TX, acceptance filter) has
  never run on hardware — only the C decode layer under it is tested.
- Next UI task discussed: surface the `canbus.py` wiring map in the Vehicle tab,
  so a suspect live reading links to the physical circuit it comes from.
