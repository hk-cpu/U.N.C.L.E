"""The physical bus and connector map for the 2006 Charger R/T PCM.

This is reference knowledge, not a capability: it records which wire carries
what, how the diagnostic connector maps onto the powertrain CAN bus, and which
pins are constant-battery as opposed to ignition-switched. Nothing here sends a
frame or writes anything to the car - it exists so the rest of cardiag can
*label* what it already reads, and so the listen-only CAN work has a signal map
to name frames against.

Two things this map is deliberately good for:

* Telling you which physical circuit a live PID comes from, so a reading that
  looks wrong can be chased back to a specific wire and connector pin.
* Knowing which pin is constant 12 V and which only wakes with the ignition,
  which is exactly what cockpit mode leans on to decide the car is "awake".

The data is a curated subset of the factory NGC3 four-connector pinout - the
power, ground, ignition, CAN and primary-sensor circuits that this software
actually reasons about - not the complete 152-pin harness. Everything is drawn
from the vehicle's own wiring and is read-only.

Safety note carried from the vehicle reference, and enforced in spirit by the
rest of cardiag: CAN-C is a live powertrain bus. cardiag only ever transmits
standard OBD-II/ISO 15765-4 requests addressed to the powertrain controller
(0x7E0); it does not write calibrations, issue actuator overrides, or probe
manufacturer memory. Treat the bus as listen-mostly, and never transmit on the
airbag/steering branch.
"""

from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Buses
# ---------------------------------------------------------------------------

#: cardiag addresses the powertrain controller here, and reads its reply here.
DIAG_REQUEST_ID = 0x7E0
DIAG_RESPONSE_ID = 0x7E8


@dataclass(frozen=True)
class Bus:
    """One data bus on the car, and what cardiag is allowed to do on it."""

    name: str
    speed_kbps: float
    standard: str
    where: str
    #: cardiag's own access policy for this bus, stated plainly.
    access: str


BUSES: tuple[Bus, ...] = (
    Bus(
        name="CAN-C (diagnostic / powertrain)",
        speed_kbps=500.0,
        standard="ISO 15765-4, 11-bit identifiers",
        where="DLC pins 6 (CAN high) and 14 (CAN low); PCM connector C1 "
              "pins 34 (CAN+) and 35 (CAN-)",
        access="Read. cardiag sends only standard OBD-II requests to the "
               "powertrain controller (0x7E0) and reads replies (0x7E8). It "
               "does not write calibrations or issue actuator commands.",
    ),
    Bus(
        name="CAN-B (interior / body)",
        speed_kbps=83.333,
        standard="Chrysler low-speed CAN",
        where="Behind the CAN-C gateway; tapped at the overhead console and "
              "DLC pins 3 and 11 on this platform",
        access="Not used by cardiag. Listed for context only - the overhead "
               "console (the sunglasses-holder display location) sits on this "
               "bus, not on CAN-C.",
    ),
)


# ---------------------------------------------------------------------------
# The J1962 diagnostic connector
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DlcPin:
    pin: int
    function: str
    note: str = ""


#: Only the pins that are populated and that matter to how cardiag connects.
DLC_PINS: tuple[DlcPin, ...] = (
    DlcPin(16, "Constant +12 V battery",
           "Live with the key out. This is why the adapter can read port "
           "voltage (ATRV) and detect the ignition without the engine awake."),
    DlcPin(4, "Chassis ground"),
    DlcPin(5, "Signal ground"),
    DlcPin(6, "CAN-C high (500 kbps)", "Twisted pair with pin 14."),
    DlcPin(14, "CAN-C low (500 kbps)", "Twisted pair with pin 6."),
)


# ---------------------------------------------------------------------------
# PCM harness circuits (curated subset)
# ---------------------------------------------------------------------------

# Categories, so the CLI and callers can group or filter.
POWER = "power"
GROUND = "ground"
IGNITION = "ignition"
CAN = "can"
SENSOR = "sensor"
ACTUATOR = "actuator"


@dataclass(frozen=True)
class Circuit:
    """One wire in the PCM harness.

    ``pid`` names the live PID this circuit feeds, where cardiag reads one, so a
    suspect reading can be traced to a physical wire. It is a PID name from
    :mod:`cardiag.pids` (for example ``"INTAKE_PRESSURE"``), or empty.
    """

    circuit: str
    connector: str
    pin: int
    color: str
    function: str
    category: str
    pid: str = ""
    note: str = ""

    @property
    def location(self) -> str:
        return f"{self.connector} pin {self.pin}"


# C1 (Black): power, grounds, ignition wake-up and CAN-C.
# C2 (Orange/Gray): coils, injectors, primary engine sensors, knock, MDS, ETC.
# C3 (Natural/White): driver demand, EVAP, EOT, fuel pump and starter.
# Transmission (C4) is handled by a separate TCM over CAN-C on this car, so only
# its ground cavities are populated; nothing there is useful to cardiag.
_CIRCUITS: tuple[Circuit, ...] = (
    # --- C1: power / ground / ignition / CAN -------------------------------
    Circuit("A209", "C1", 10, "RD", "Constant +12 V battery (fused B+)",
            POWER, note="Fed from the TIPM. Powers the PCM's keep-alive memory "
                        "with the key off."),
    Circuit("F18", "C1", 11, "LG/BK", "Ignition switch output (RUN-START)",
            IGNITION, note="Hot only in RUN and START. This transition is what "
                           "wakes the PCM, and what cockpit mode treats as the "
                           "car coming alive."),
    Circuit("F11", "C1", 12, "RD/WT", "Ignition switch output (OFF-RUN-START)",
            IGNITION),
    Circuit("Z816", "C1", 9, "BK", "Primary PCM power ground", GROUND),
    Circuit("Z13", "C1", 14, "BK/RD", "Powertrain chassis ground", GROUND),
    Circuit("D65", "C1", 34, "WT/LG", "CAN-C bus high (500 kbps)", CAN,
            note="Maps to DLC pin 6."),
    Circuit("D64", "C1", 35, "WT/LB", "CAN-C bus low (500 kbps)", CAN,
            note="Maps to DLC pin 14."),
    Circuit("F42", "C1", 38, "DG/LG", "Fused ASD relay output (+12 V when "
            "energised)", POWER, note="The automatic-shutdown relay feeds the "
            "coils, injectors and O2 heaters; it drops when the engine stops."),
    # --- C2: primary engine sensors ----------------------------------------
    Circuit("K1", "C2", 23, "VT/BR", "Manifold absolute pressure (MAP) signal",
            SENSOR, pid="INTAKE_PRESSURE",
            note="Primary speed-density load input."),
    Circuit("K21", "C2", 30, "DB/LB", "Intake air temperature (IAT) signal",
            SENSOR, pid="INTAKE_TEMP"),
    Circuit("K2", "C2", 20, "VT/OR", "Engine coolant temperature (ECT) signal",
            SENSOR, pid="COOLANT_TEMP"),
    Circuit("K22", "C2", 21, "BR/OR", "Throttle position sensor 1 signal",
            SENSOR, pid="THROTTLE_POS"),
    Circuit("K122", "C2", 28, "BR/DG", "Throttle position sensor 2 signal",
            SENSOR, note="Redundant TP channel for the electronic throttle."),
    Circuit("K42", "C2", 25, "DB/OR", "Knock sensor 1 (bank 1) signal", SENSOR,
            note="No generic PID exposes knock; the ECU acts on it internally "
                 "by pulling timing. cardiag infers that from timing advance "
                 "falling under load, never from this wire."),
    Circuit("K242", "C2", 36, "BR/WT", "Knock sensor 2 (bank 2) signal", SENSOR),
    Circuit("K44", "C2", 34, "DB/GY", "Camshaft position (CMP) signal", SENSOR),
    Circuit("K24", "C2", 35, "DB/WT", "Crankshaft position (CKP) signal", SENSOR),
    Circuit("K41", "C2", 31, "DB/LB", "Upstream O2 1/1 signal (bank 1 sensor 1)",
            SENSOR, pid="O2_B1S1"),
    Circuit("K43", "C2", 33, "DB/PK", "Upstream O2 2/1 signal (bank 2 sensor 1)",
            SENSOR, pid="O2_B2S1"),
    Circuit("F855", "C2", 29, "PK/YL", "Regulated 5 V sensor supply", POWER,
            note="Feeds MAP, TPS, CMP and CKP. One failed 5 V rail takes "
                 "several sensors down at once."),
    # --- C2: primary actuators (named so a reading has context; not driven) -
    Circuit("K453", "C2", 16, "BR/DG", "MDS solenoid 6 (cylinder deactivation)",
            ACTUATOR, note="One of the four MDS solenoids (cylinders 1, 4, 6, "
                           "7). cardiag reports MDS state, it does not command "
                           "it."),
    # --- C3: driver demand and the MDS gate --------------------------------
    Circuit("K23", "C3", 25, "BR/WT", "Accelerator pedal position (APPS) 1 "
            "signal", SENSOR),
    Circuit("K29", "C3", 36, "WT/BR", "Accelerator pedal position (APPS) 2 "
            "signal", SENSOR),
    Circuit("G224", "C3", 33, "VT/YL", "Engine oil temperature (EOT) signal",
            SENSOR, pid="OIL_TEMP",
            note="Critical input to the MDS activation gate - MDS stays off "
                 "until the oil is warm enough."),
    Circuit("K31", "C3", 37, "BR", "Fuel pump relay control", ACTUATOR),
    Circuit("T752", "C3", 38, "DG/OR", "Engine starter motor relay control",
            ACTUATOR),
)

BY_CIRCUIT: dict[str, Circuit] = {c.circuit: c for c in _CIRCUITS}


def circuits(category: str | None = None) -> list[Circuit]:
    """All curated circuits, or just those in one category."""
    if category is None:
        return list(_CIRCUITS)
    return [c for c in _CIRCUITS if c.category == category]


def for_pid(pid_name: str) -> Circuit | None:
    """The physical circuit a given live PID is read from, if cardiag knows it."""
    name = pid_name.strip().upper()
    for circuit in _CIRCUITS:
        if circuit.pid.upper() == name:
            return circuit
    return None


def power_map() -> dict[str, list[Circuit]]:
    """The circuits that decide whether the car is powered, grouped by role.

    This is the wiring behind cockpit mode: constant battery is always live,
    the ignition circuits are what actually come up when the key is turned.
    """
    return {
        "constant": [c for c in _CIRCUITS if c.category == POWER
                     and "Constant" in c.function],
        "ignition": circuits(IGNITION),
        "ground": circuits(GROUND),
    }
