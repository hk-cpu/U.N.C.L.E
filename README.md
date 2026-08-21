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
cardiag --sim --profile faulty scan
```

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

Three simulated vehicles are built in:

```bash
cardiag --sim scan                        # healthy car
cardiag --sim --profile faulty scan       # check-engine light, three codes
cardiag --sim --profile emissions monitors  # codes recently cleared
cardiag --sim live                        # dashboard on a simulated drive cycle
```

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
- **Clearing codes does not fix anything.** It erases the freeze frame and resets
  every readiness monitor, which means the car cannot pass an emissions test
  until you have driven a full cycle. If the fault is still present, the code
  comes back.
- **Don't drive while reading the dashboard.** Log to a file and read it after,
  or bring a passenger.
