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

### Commands

| Command | What it does |
| --- | --- |
| `cardiag scan` | Full health check with findings (the default) |
| `cardiag codes` | Trouble codes with likely causes |
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

Five simulated vehicles are built in:

```bash
cardiag --sim scan                              # healthy car
cardiag --sim --sim-profile faulty scan         # check-engine light, three codes
cardiag --sim --sim-profile emissions monitors  # codes recently cleared
cardiag --sim --sim-profile charger scan        # healthy 2006 Charger R/T
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
  dashboard.py    live terminal view
  report.py       findings: turns readings into "here is what to check"
  logger.py       CSV and SQLite recording
    session.py    the high-level vehicle API
      vehicles.py model profiles: engine layout and known issues
      vin.py      VIN validation and decoding
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
