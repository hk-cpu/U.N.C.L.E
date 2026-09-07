# U.N.C.L.E

A WhatsApp first AI assistant.

## cardiag

`cardiag` is the car diagnostics component: a command line OBD-II scan tool that
talks to a standard ELM327 adapter, reads what your car's ECU knows, and explains
what it means rather than just printing hex.

It works with the adapters you already have — a Vgate USB cable, a Bluetooth or
WiFi ELM327 clone — and ships with a simulated car so you can try every command
before you go near the driveway.

```
cardiag --sim --sim-profile charger-misfire scan
```

### The app

```bash
cardiag app
```

Opens in its own window — no address bar, no tabs, its own icon in the dock or
taskbar. `cardiag ui` does the same thing in an ordinary browser tab if you
prefer that.

To stop typing commands entirely:

```bash
cardiag install-launcher
```

That adds cardiag to your applications menu (Start menu on Windows,
`~/Applications` on macOS), so it starts with a click like anything else.

**Install it properly.** With the app open, use the *Install* button in the
toolbar — or your browser's install option. It then behaves as a real
installed application: its own icon, its own window, launched from wherever you
launch everything else. The shell is cached, so it opens instantly and still
opens if the server is not running (it will tell you it cannot reach the car
rather than showing stale readings — vehicle data is never cached).

The manifest also registers shortcuts, so a long-press or right-click on the
icon jumps straight to Health, Live or Codes.

Nine tabs:

- **Connect** — pick your adapter from a list, or click a simulated car to try
  it with no hardware.
- **Assistant** — the car explained in plain language: what it is doing, whether
  that is normal, and what is worth acting on.
- **Health** — the full diagnosis: findings ranked worst-first, each with what
  to check, plus readiness monitors, live data and the freeze frame.
- **Gauges** — a full-screen instrument cluster: tachometer, speedometer, a
  shift light, and coolant, oil, voltage and intake as plain numbers.
- **Live** — stat tiles with meters and sparklines, one per parameter. Values
  that leave their normal band turn amber or red, so a lean bank is visible at
  a glance instead of needing to be spotted in a column of numbers.
- **Codes** — trouble codes with likely causes and model-specific notes, and a
  Clear button that explains what clearing costs before it does it.
- **Monitors** — mode 06, with the per-cylinder misfire counters drawn as bars
  so a skewed group is obvious at a glance.
- **Baselines** — save a snapshot, then compare the car against it later.
- **Vehicle** — the decoded VIN and everything the profile knows about your
  engine.

It follows your system light/dark setting, with a toggle, and the layout works
on a phone.

#### Using it from your phone

The adapter plugs into a laptop, so the laptop runs the app and the phone is
the screen:

```bash
cardiag app --host 0.0.0.0
```

That prints a URL with a token in it — open that on your phone, on the same
WiFi. The token is required from anything that is not the laptop itself,
because the API can erase your trouble codes.

One honest limitation: browsers only allow an app to be *installed* from a
secure context. `localhost` counts as one, so installing on the laptop works.
A plain `http://192.168.x.x` address over your network does not, so Android
Chrome will not offer the install prompt there — the page works normally, it
just stays a page. On iOS, Safari's *Add to Home Screen* still gives you an
icon.

Nothing is sent anywhere. The server runs on your machine and talks to your
adapter; it binds to localhost unless you ask otherwise.

### The assistant

Connecting lands on the **Assistant**, which reads the whole car and says what
it found in sentences rather than numbers:

> Dodge Charger R/T (5.7 L HEMI V8) is running, but something needs looking at.
>
> **Fuelling — bank 2 lean.** Bank 2 (passenger's (right) side) is +23% off. The
> ECU is having to correct hard, which points at an air leak, a weak fuel supply
> or a lying oxygen sensor.

One card per aspect of the car — faults, engine, cooling, fuelling, electrical,
wear, readiness — each with the numbers it drew its conclusion from, so any claim
can be checked. *Watch live* keeps a running commentary going while you drive or
rev it, carrying the scan's findings forward rather than silently retracting them.

It works with no internet and no API key. That is deliberate: the moment you are
stood over an engine in a car park is the moment your phone has no signal, and an
explanation that needs the network is no explanation at all. The reasoning is
ordinary tested Python in `cardiag/explain.py`, not a model's guess.

Two rules it follows, both of which it has tests for:

- **It never describes a channel the car did not answer.** No coolant reading
  produces no cooling verdict — not a reassuring one.
- **It never calls a car healthy while listing repairs.** The summary weighs the
  mode 06 monitors as well as the live readings, so the flagship case — a car
  with no codes, no warning light, and a cylinder quietly misfiring — reads
  *"no stored codes, but the monitors show something developing"*.

#### How it is connected

The header and the assistant both name the link: **USB**, **Bluetooth**, **WiFi**
or **Simulated**. This is worth stating because a Bluetooth ELM327 reaches the
computer as an ordinary serial port — on Windows both a cable and a pairing are
just a COM port — so the app checks the driver's own description rather than
guessing from the device path.

| Link | What you connect to |
|---|---|
| USB | `/dev/ttyUSB0`, `/dev/ttyACM0`, `COM3` — the Vgate cable |
| Bluetooth | `/dev/rfcomm0` after pairing, or the COM port Windows assigns |
| WiFi | `tcp://192.168.0.10:35000` |

### The gauge cluster

The **Gauges** tab is the CAN bus build guide's *Gauge v1: OBD-polling fallback*
— the ★☆☆ starting point that needs no hardware beyond the ELM327 you already
have. Start it, hit *Full screen*, and prop the phone where you can see it.

- **Tachometer** with the red zone drawn where the 5.7's limiter is (5800 rpm),
  in its fixed place on the dial rather than appearing only when you reach it.
- **Shift light** across the top: amber from 5400 rpm, red at the limiter.
- **Speedometer**, in km/h, as the PID reports it.
- **Coolant, oil, voltage and intake** as numbers, turning amber and red on the
  same thresholds the rest of the app uses. Sustained coolant above 110 °C is
  the point to investigate the cooling system, and the readout says so by
  changing colour.
- **0–100 km/h timer**, armed whenever the car is stopped.
- The screen is kept awake while it runs.

It polls six channels instead of the Live tab's full set, at 20 Hz instead of
2 Hz, because a dial that lags is worse than no dial.

Two honest limits. The redline is the published figure, not a measured cutoff —
it is where the dial paints red, not a promise about your engine. And the 0–100
timer reads off the poll stream: an ELM327 answers a speed request every 50 ms
at best, so treat the number as indicative, not as a drag box result.

The guide's *Gauge v2* — decoding RPM and speed straight off CAN-C broadcasts at
ten times the rate — needs the ESP32 hardware, not this adapter. See *Raw CAN,
and where cardiag stops* below.

### Catching it before it breaks

A code reader tells you what already failed. Mode 06 tells you what is *about*
to. It is where the ECU keeps the measurements behind its pass/fail monitors —
including a misfire counter for every cylinder.

```bash
cardiag misfires
```

```
Misfire counts by cylinder ------------------------------------
  cylinder 1      22  ######################......  (deactivated at cruise)
  cylinder 2       3  ###.........................
  cylinder 3       2  ##..........................
  cylinder 4      28  ############################  (deactivated at cruise)
  cylinder 5       4  ####........................
  cylinder 6      19  ###################.........  (deactivated at cruise)
  cylinder 7      25  #########################...  (deactivated at cruise)
  cylinder 8       3  ###.........................
```

That car has **no stored codes and no warning light**. Every one of the four
tall bars is an MDS cylinder, and that skew is the pattern worn MDS lifters
produce. `cardiag scan` says so in words, and flags the bank 2 catalyst sitting
at 92 % of its own failure limit — still passing, so still no code.

```bash
cardiag tests          # every monitor: measured value, limits, margin left
cardiag tests --all    # including the ones with plenty of headroom
```

### Guided repair

```bash
cardiag fix                 # what procedures exist for your car
cardiag fix mds-lifter      # walk one
```

Each step says what to do, what a good result looks like, which live parameters
to watch while you do it, and where you can hurt yourself or the car.

### Before and after

The way to change something safely is to measure first.

```bash
cardiag baseline before-plugs     # snapshot the car as it is
#   ... fit the parts, drive it ...
cardiag compare before-plugs      # what actually moved
```

```
  ↓ Long term fuel trim, bank 2
     23.44 %  ->  4.69 %
     -18.75
```

`compare` exits non-zero if anything got worse, so it drops into a script.

This also covers tuning. `cardiag calibration` reads the ECU's calibration ID
and verification number — the identity of the software actually running in the
module. Snapshot it before a tune and `compare` will tell you plainly if the
PCM was reflashed, by you or by anyone else:

```
  · ECU calibration
     68RT0057AA  ->  68XX9999ZZ
     The calibration ID changed, so the module was reflashed between these two
     snapshots. If that was not deliberate, find out who did it.
```

Snapshots live in `~/.local/share/cardiag/baselines.db` (override with
`--store` or `$CARDIAG_HOME`).

### Raw CAN, and where cardiag stops

The OBD port on this car carries **diagnostic traffic only**. The powertrain
bus that modules actually broadcast on (CAN-C) sits behind the Front Control
Module gateway, and the body bus (CAN-B, on DLC pins 3 and 11) is a separate
network at a different bit rate. An ELM327 is a diagnostic translator, not a
sniffer — it cannot see either of those, whatever you plug it into.

So the **Gauges** tab polls — which is exactly the guide's Gauge v1, and works
today with what you own. Going faster than polling, or reading anything the
diagnostic layer does not expose (steering-wheel buttons, MDS state, gear
position), needs different hardware: a CAN interface tapped at the FCM or the
DLC's body pins, and a tool like SavvyCAN to reverse the frames into a DBC.

What cardiag *is* good for in that project is the correlation step. Log known
values from the diagnostic side while capturing raw frames on the other, and
the broadcast frames carrying RPM and speed fall out of the comparison:

```bash
cardiag live RPM SPEED --refresh 0.1 --log drive.csv
```

The CSV timestamps are Unix epoch seconds to the millisecond, which lines up
directly against a SavvyCAN or `candump` capture.

### What this does not do

**It does not write calibrations to the ECU.** That is a deliberate limit, not
an oversight. The 2006 LX powertrain module is security-locked, its calibration
tables are proprietary, and none of it is reachable over generic OBD-II.
Writing speculative bytes at it does not produce a tune — it bricks a VIN-locked
module and immobilises the car, and recovering that needs dealer-level tooling
to re-marry the module to the immobiliser.

Actual flash tuning needs a tool that has licensed or reverse-engineered those
tables: HP Tuners, DiabloSport, SCT and similar. What cardiag gives you is
everything around that — the logs a tuner asks for, proof of which calibration
is loaded, and a before/after measurement to show whether it helped.

### Vehicle profiles

Generic OBD-II tells you "cylinder 4 is misfiring". A profile turns that into
something you can act on. cardiag ships with one for the **2006 Dodge Charger
R/T (5.7 L HEMI V8)**, detected automatically from the car's VIN:

```
  ! P0304 - Cylinder 4 misfire detected
     On your engine, cylinder 4 is on bank 2, the passenger's (right) side,
     and has 2 spark plugs. It is also one of the cylinders MDS shuts down
     at cruise.

  ! Known issue on this model: MDS lifter or camshaft lobe failure
     Raised by P0300, P0304. The lifters for the four MDS cylinders
     (1, 4, 6, 7) can collapse or spall, taking the camshaft lobe with them...
     How to check: (1) Note which cylinder is missing - is it one of
     1, 4, 6, 7? (2) Swap the coil with a neighbouring cylinder and clear the
     code. If the misfire stays on the same cylinder, it is not ignition...
```

The profile knows the engine's bank layout and Chrysler's cylinder numbering,
that the HEMI has 16 plugs, which cylinders MDS deactivates, that the engine
runs hotter than a generic threshold would allow for, and the failures this
model is known for — MDS lifters, exhaust manifold bolts (which show up as a
*lean* code, not an exhaust one), the oil pressure sender, and the two separate
catalysts.

```bash
cardiag vehicle                    # what cardiag knows about your car
cardiag vin                        # decode the VIN, with check-digit validation
cardiag --vehicle charger lookup P0304   # model-specific code explanation
cardiag --vehicle none scan        # opt out, stay generic
```

Profiles are applied automatically when the VIN matches, so on your own car you
never need the flag. `--vehicle` is there for cars that do not report a VIN
(common before roughly 2005).

### What it does

- **Reads trouble codes** — stored (mode 03), pending (mode 07) and permanent
  (mode 0A), each with a plain-English description, a severity, and the likely
  causes ordered cheapest-first.
- **Explains, not just reports** — flags high fuel trims, low charging voltage,
  a thermostat stuck open, an overheating engine, and incomplete readiness
  monitors, with what to check for each.
- **Live dashboard** — a self-refreshing terminal view of every parameter your
  car supports, with meters and running min/max.
- **Data logging** — record a drive to CSV or SQLite while you watch.
- **Emissions readiness** — tells you whether the car would pass a pre-test
  check, and which monitors still need a drive cycle.
- **Freeze frame** — the snapshot of engine conditions the ECU took at the
  moment it confirmed a fault, which is usually the most useful clue you get.
- **Clears codes** — with a warning about what that actually costs you.

### Install

```bash
git clone https://github.com/hk-cpu/u.n.c.l.e
cd u.n.c.l.e
pip install -e ".[serial]"
```

`pyserial` is only needed for real hardware; the package itself is pure standard
library and the simulator runs without it.

### Connecting

Find your adapter:

```bash
cardiag ports
```

Then point at it. `cardiag` probes baud rates automatically, so usually you only
need the device:

```bash
cardiag --port /dev/ttyUSB0 scan          # Linux USB (Vgate, CH340, FTDI)
cardiag --port COM3 scan                  # Windows
cardiag --port /dev/cu.usbserial-1420 scan  # macOS
cardiag --port /dev/rfcomm0 scan          # Bluetooth, after rfcomm bind
cardiag --port tcp://192.168.0.10:35000 scan  # WiFi ELM327
```

With no `--port`, the adapter is auto-detected, or read from `$CARDIAG_PORT`:

```bash
export CARDIAG_PORT=/dev/ttyUSB0
cardiag scan
```

**Turn the ignition on.** The adapter powers up from the OBD port whether or not
the car is awake, so a connected adapter and a responding ECU are two different
things. For live data, have the engine running.

**If it connects to the adapter but not the car**, name the protocol. Cheap
ELM327 clones are known to fail auto-detection against Chrysler's CAN timing
while working perfectly once told what to speak:

```bash
cardiag --protocol 6 scan     # ISO 15765-4 CAN, 11 bit, 500 kbaud
```

cardiag already retries protocols 6 and 7 by itself when auto-detect fails, and
the error tells you what it tried — `--protocol` is there for when you want to
skip straight past the guessing.

### Commands

| Command | What it does |
| --- | --- |
| `cardiag app` | The app, in its own window |
| `cardiag ui` | The same thing in a browser tab |
| `cardiag install-launcher` | Add it to your applications menu |
| `cardiag scan` | Full health check with findings (the default) |
| `cardiag codes` | Trouble codes with likely causes |
| `cardiag misfires` | Per-cylinder misfire counters |
| `cardiag tests` | Mode 06: what the monitors measured, and their margin |
| `cardiag fix <issue>` | Guided repair procedure |
| `cardiag baseline <name>` | Snapshot the car for later comparison |
| `cardiag compare <name>` | What changed since a snapshot |
| `cardiag calibration` | ECU calibration ID and verification number |
| `cardiag lookup P0420` | Explain a code — works offline, no car needed |
| `cardiag vehicle` | What cardiag knows about your model |
| `cardiag vin` | Decode and validate the VIN |
| `cardiag live` | Live dashboard |
| `cardiag live --log drive.csv` | Dashboard plus recording |
| `cardiag monitors` | Emissions readiness monitors |
| `cardiag freeze` | Freeze frame from when the fault was stored |
| `cardiag read RPM COOLANT_TEMP` | Read specific parameters once |
| `cardiag pids` | What your car actually supports |
| `cardiag clear` | Erase codes (asks first) |
| `cardiag ports` | List serial ports |
| `cardiag info` | Adapter, protocol, VIN |

Every command takes `--json` for scripting. `scan` and `codes` exit non-zero when
something is wrong, so they drop straight into a cron job:

```bash
cardiag --json scan > "health-$(date +%F).json" || notify-send "Car needs attention"
```

### Trying it without a car

Six simulated vehicles are built in:

```bash
cardiag --sim scan                              # healthy car
cardiag --sim --sim-profile faulty scan         # check-engine light, three codes
cardiag --sim --sim-profile emissions monitors  # codes recently cleared
cardiag --sim --sim-profile charger scan        # healthy 2006 Charger R/T
cardiag --sim --sim-profile charger-wear misfires # wear with no codes set yet
cardiag --sim --sim-profile charger-misfire scan  # Charger, MDS-cylinder misfire
cardiag --sim live                              # dashboard on a simulated drive
```

The two `charger` profiles present a V8 with both banks reporting, a valid 2006
R/T VIN, and — on `charger-misfire` — a cylinder 4 misfire with bank 2 running
lean, which is the picture a worn MDS lifter and a leaking manifold actually
produce.

The simulator runs a 60-second drive cycle — idle, pull away, cruise, slow down —
so the live dashboard actually moves.

### Using it from Python

```python
from cardiag import Session

with Session("/dev/ttyUSB0") as car:
    print(car.read_vin())

    for code in car.read_dtcs():
        print(code.code, code.description, code.causes)

    rpm = car.read("RPM")
    print(rpm.value, rpm.pid.unit)
```

And for the analysis layer:

```python
from cardiag import report, Session

with Session("sim://?profile=faulty") as car:
    health = report.build(car)
    print(health.headline)
    for finding in health.findings:
        print(finding.severity, finding.title)
```

### How it fits together

```
cli.py            argparse front end, output formatting
  web/            the app: stdlib HTTP server, JSON API, installable page
  dashboard.py    live terminal view
  report.py       findings: turns readings into "here is what to check"
  logger.py       CSV and SQLite recording
    session.py    the high-level vehicle API
      baseline.py snapshots and before/after comparison
      vehicles.py model profiles: engine layout, known issues, procedures
      vin.py      VIN validation and decoding
      mode06.py   on-board monitor test results
      elm327.py   adapter driver: handshake, framing, multi-frame CAN
      pids.py     parameter table and SAE J1979 decoders
      dtc.py      trouble code decoding and the fault database
        transport/  serial (USB, Bluetooth), TCP (WiFi), simulator
```

Each layer only knows about the one below it, so adding a transport, a PID or a
diagnostic rule touches one file.

### Tests

```bash
pip install -e ".[dev]"
pytest
```

The suite runs entirely against the simulator and canned adapter replies — no
hardware, no car.

### Limitations, honestly

- **Generic OBD-II only.** Every car sold since roughly 2001 (petrol, EU) or 1996
  (US) answers these requests, but they only cover emissions-related systems on
  the engine and gearbox. ABS, airbag, body and comfort modules speak
  manufacturer-specific protocols this tool does not implement — if your warning
  light is on and `cardiag` finds no codes, that is usually why.
- **Manufacturer-specific codes** (P1xxx, P2xxx, P3xxx) are decoded structurally
  but their meaning varies by make, so those are reported as such rather than
  guessed at.
- **Vehicle profiles are advisory.** The known-issue notes describe what a model
  commonly suffers from; they are a place to start looking, not a diagnosis. The
  engine layout facts (bank sides, cylinder numbering, MDS cylinders, firing
  order) are specifications and can be relied on.
- **Clearing codes does not fix anything.** It erases the freeze frame and resets
  every readiness monitor, which means the car cannot pass an emissions test
  until you have driven a full cycle. If the fault is still present, the code
  comes back.
- **Don't drive while reading the dashboard.** Log to a file and read it after,
  or bring a passenger.
