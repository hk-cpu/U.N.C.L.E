# cardiag on an ESP32

This talks OBD-II to the car over CAN directly. It is not a companion to the
ELM327 — it **replaces** it. The ESP32's TWAI peripheral is a CAN controller;
give it a transceiver on DLC pins 6 and 14 and it can send the same diagnostic
requests an ELM327 sends and decode the replies itself.

## Do this first — no parts required

1. Install the ESP32 boards in the Arduino IDE: **File → Preferences →
   Additional Board Manager URLs**, add
   `https://espressif.github.io/arduino-esp32/package_esp32_index.json`, then
   **Tools → Board → Boards Manager** and install *esp32 by Espressif*.
2. Open `cardiag-esp32/cardiag-esp32.ino`.
3. Pick your board under **Tools → Board** (for the LOLIN S2 Mini, choose
   *LOLIN S2 Mini*) and the right port.
4. Upload, then open **Tools → Serial Monitor** at **115200 baud**.

`SELF_TEST` is `1` out of the box, so it runs with no transceiver, no wiring and
no car. You should see:

```
-- decoding canned replies (no hardware needed) --
  coolant = 82.00 degC
  rpm = 3047.00 rpm
  speed = 100.00 km/h
  throttle = 25.10 %
  volts = 14.44 V
-- reassembling the VIN from three frames --
  VIN = 2B3KA53H46H315720
```

That proves the board, the toolchain and the decoder before you spend anything.
It is also the whole point of the next section.

## What you still need to buy

| Part | Why | ~Cost |
|---|---|---|
| **SN65HVD230** breakout (or MCP2562/TJA1051) | The ESP32 has a CAN *controller* but no transceiver. This is the only mandatory part. | $3 |
| OBD-II male connector or breakout | So you are not jamming wires into the car's port | $5 |
| 12 V→5 V buck converter, automotive-rated | Power, once it lives in the car | $4 |

A display is optional and deliberately not in version one — get real numbers in
the serial monitor first.

## Wiring

| ESP32 | SN65HVD230 | | Car |
|---|---|---|---|
| GPIO5 (`PIN_CAN_TX`) | `D` / `TXD` | | |
| GPIO4 (`PIN_CAN_RX`) | `R` / `RXD` | | |
| 3V3 | `VCC` | ⚠ **3.3 V, not 5 V** | |
| GND | `GND` | | DLC pin 4 or 5 |
| | `CANH` | → | **DLC pin 6** |
| | `CANL` | → | **DLC pin 14** |

Both GPIOs are changeable at the top of the sketch — TWAI routes through the pin
matrix, so any free pins work. Avoid the strapping pins (0, 45, 46 on the S2;
0, 2, 12, 15 on the classic ESP32).

**No 120 Ω termination resistor.** The bus is already terminated at both ends;
you are tapping the middle. Some SN65HVD230 breakouts have a termination
resistor fitted — check the board and remove or disable it if so.

Then set `SELF_TEST` to `0`, upload, turn the ignition on, and watch the serial
monitor.

## Safety

Pins 6 and 14 are the **diagnostic** bus. It exists for a scan tool to transmit
requests on, which is exactly what this firmware does — no different from what
your Vgate already does. That is *not* the same wire as the powertrain **CAN-C**
behind the FCM gateway, which carries engine, transmission, ABS and airbag
traffic and must only ever be listened to. Do not move these two wires onto it.

Power the board from an **ignition-switched** feed once it lives in the car. DLC
pin 16 is always live, so a board wired there will sit awake and flatten the
battery.

## How this is tested, and what that does and does not mean

The decoding — the arithmetic that turns two hex bytes into a number you would
act on — is in `obd.c`, deliberately written as plain C99 with no Arduino or
ESP-IDF dependency. That means it compiles and runs on an ordinary computer:

```bash
make -C firmware/test        # 72 checks
python -m pytest tests/test_firmware.py
```

The cases are taken from *2006 Dodge Charger RT HEMI — Raw Hex OBD Data
Reference*: its worked examples (`41 05 7A` → 82 °C, `41 0C 2F 9C` → 3047 rpm,
`41 42 38 68` → 14.44 V), its raw-hex DTC table, and its captured VIN response.
So a pass means the firmware agrees with what that car was actually observed to
reply.

**What is not tested:** everything in the `.ino` — TWAI setup, bus timing,
transmitting, the acceptance filter, the poll loop. None of that can run without
the hardware, and none of it has ever seen a car. Expect to debug it on the
bench. The split exists so that when something does go wrong, you can be
confident it is the transport and not the maths.
