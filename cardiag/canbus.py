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
actually reasons about - not the complete 152-pin harness. Alongside the PCM
harness it carries the chassis grounding points and the powertrain/diagnostic
fuses, because a dead PCM or a "no communication" fault far more often traces to
a blown feed or a bad ground than to the bus itself. Everything is drawn from
the vehicle's own wiring and is read-only.

A note on cross-referencing: the fuse and ground tables name PCM connector pins
in places, and those come from a different source document than the C1-C4 pinout
above. Where the two disagree on a cavity or circuit number they are left
un-cross-linked rather than reconciled by guesswork; a fuse or ground is tied to
a named circuit here only where both sources agree it is the same wire.

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


# ---------------------------------------------------------------------------
# Chassis grounding topology (G100-G304)
# ---------------------------------------------------------------------------
#
# The battery sits in the trunk while the starter, alternator and PCM are up
# front, so ground integrity across the whole unibody matters. The modules do
# not ground straight to the battery; they land on shared chassis points. A
# loose or corroded point drags several unrelated circuits down together - the
# classic "everything is wrong at once" fault - which is exactly when knowing
# what shares a ground earns its keep.

#: The factory spec for a healthy module ground on this platform.
GROUND_SPEC = "less than 1.0 ohm resistance and under 100 mV voltage drop"


@dataclass(frozen=True)
class Ground:
    """One chassis grounding point and what shares it."""

    name: str          # e.g. "G102"
    location: str
    serves: str
    note: str = ""


GROUNDS: tuple[Ground, ...] = (
    Ground("G100", "Left front of the engine compartment",
           "Front control module (FCM), left front lamps, instrument cluster "
           "reference"),
    Ground("G101", "Right front of the engine compartment, by the shock tower",
           "ABS/ESP module, integrated power module (IPM), right headlamps"),
    Ground("G102", "Right side of the engine compartment, near the PCM",
           "PCM chassis grounds, A/C compressor, cooling-fan relays",
           note="The PCM's own case grounds (circuits Z816 and Z13) land here. "
                "A bad G102 is a prime suspect when several engine sensors read "
                "wrong at the same time."),
    Ground("G200", "Behind the left side of the instrument panel, near the "
           "ignition-switch breakout",
           "Instrument cluster (CCN), HVAC control, ABS/ESP interior sense, "
           "premium radio/antenna"),
    Ground("G201", "Behind the right side of the instrument panel",
           "Front HVAC blower motor / power module, passenger-side dash"),
    Ground("G202", "Behind the left instrument panel, near the cluster breakout",
           "DLC pins 4 and 5, NAG1 TCM, SKREEM/immobiliser, audio amplifier, "
           "cruise",
           note="The diagnostic connector grounds here. A bad G202 can imitate "
                "an adapter or 'no communication' fault that is really the DLC "
                "ground."),
    Ground("G300", "Below the left B-pillar",
           "Left body harness, driver door module, anti-theft, ABS rear sensor "
           "shield, cluster"),
    Ground("G301", "Below the right B-pillar",
           "Right body harness, passenger door module, shifter lever assembly, "
           "exterior lamps"),
    Ground("G302", "Right front of the cargo pan (trunk)",
           "Rear power distribution centre, fuel pump module, rear tail/stop "
           "lamps, TCM secondary ground"),
    Ground("G303", "Right rear of the cargo pan, by the decklid latch",
           "Decklid latch, rear fascia and licence-plate lamps, battery "
           "negative (B-) trunk ground stud",
           note="Covers the G303/G304 pair. This is where the trunk-mounted "
                "battery's negative returns to the body."),
)

BY_GROUND: dict[str, Ground] = {g.name: g for g in GROUNDS}


def grounds() -> list[Ground]:
    """The chassis grounding points this reference records."""
    return list(GROUNDS)


# ---------------------------------------------------------------------------
# Key fuses and relays (powertrain / diagnostic feeds)
# ---------------------------------------------------------------------------
#
# Not the whole box - the front Integrated Power Module (IPM) and rear Power
# Distribution Centre (PDC) carry dozens of circuits each. This is the subset
# cardiag reasons about: the PCM's power, the automatic-shutdown (ASD) feed that
# carries spark and injection, the fuel pump, the transmission module, and the
# diagnostic connector's own feeds - the ones whose loss looks like a failure
# cardiag would otherwise try to diagnose on the bus.

#: Fuse panels on this car.
IPM = "Front IPM (under-hood)"
PDC = "Rear PDC (trunk)"


@dataclass(frozen=True)
class Fuse:
    """One fuse (or cartridge) and the circuit it protects."""

    panel: str
    position: str      # e.g. "Fuse 11"
    rating: str        # e.g. "20 A"
    feeds: str
    #: A PCM harness circuit in :data:`BY_CIRCUIT` this fuse is the same wire
    #: as, where both source documents agree; empty where they do not line up.
    circuit: str = ""
    note: str = ""


FUSES: tuple[Fuse, ...] = (
    # --- Front IPM ---------------------------------------------------------
    Fuse(IPM, "Fuse 10", "5 A",
         "PCM processor feed and starter-relay coil"),
    Fuse(IPM, "Fuse 11", "20 A",
         "PCM constant battery (B+), keep-alive memory", circuit="A209",
         note="Pull this and the PCM forgets its learned fuel trims and "
              "adaptations - they have to relearn on the next drive."),
    Fuse(IPM, "Fuse 14", "25 A",
         "Automatic-shutdown (ASD) relay output to the PCM"),
    Fuse(IPM, "Fuse 15", "25 A",
         "ASD relay output to fuel injectors 1-8 and ignition coils 1-8",
         note="Shares the ASD relay (R13) with fuse 14. When the engine stops "
              "and the ASD relay drops, spark and fuel die together - which is "
              "what circuit F42 reports as the ASD feed going away."),
    Fuse(IPM, "Fuse 17", "30 A", "ABS/ESP module valve feed"),
    Fuse(IPM, "Fuse 19", "50 A",
         "Radiator cooling fan (high) relay and fan control"),
    Fuse(IPM, "Fuse 20", "20 A",
         "Starter motor solenoid relay (R9)",
         note="The PCM commands this relay on circuit T752 (C3 pin 38)."),
    Fuse(IPM, "Fuse 21", "50 A", "ABS/ESP module pump-motor feed"),
    # --- Rear PDC ----------------------------------------------------------
    Fuse(PDC, "Fuse 6", "20 A",
         "Fuel pump module, fed via the fuel pump relay (R9)",
         note="The PCM commands the fuel pump relay on circuit K31 (C3 pin "
              "37)."),
    Fuse(PDC, "Fuse 8", "15 A",
         "DLC pin 16 constant B+, and the ignition-switch feed (PCM, fuel pump "
         "relay, OCM)",
         note="Feeds the diagnostic connector's always-hot pin 16, so a dead "
              "fuse 8 can look like a dead adapter."),
    Fuse(PDC, "Fuse 29", "5 A",
         "Ignition RUN-START feed (PCM, ABS/ESP, FCM, cluster, SKREEM, stop "
         "lamp switch)", circuit="F18",
         note="Circuit F18 reaches the PCM at C1 pin 11 - the wake-up "
              "transition cockpit mode watches for."),
    Fuse(PDC, "Fuse 37", "15 A",
         "NAG1 transmission control (TCM fused relay output and shifter)"),
)


def fuses(panel: str | None = None) -> list[Fuse]:
    """The key fuses this reference records, or just those in one panel."""
    if panel is None:
        return list(FUSES)
    return [f for f in FUSES if f.panel == panel]


def fuses_for_circuit(circuit_name: str) -> list[Fuse]:
    """Fuses tied to a named PCM harness circuit, where the two line up."""
    name = circuit_name.strip().upper()
    return [f for f in FUSES if f.circuit.upper() == name]


# ---------------------------------------------------------------------------
# Serialisation (shared by the CLI's --json and the web Vehicle tab)
# ---------------------------------------------------------------------------

def circuit_ref(pid_name: str) -> dict | None:
    """A compact reference to the wire a live PID is read from, or None.

    Small on purpose: just enough for a reading in the UI to point at the
    physical circuit it comes from, so a value that looks wrong can be chased
    to a wire and connector pin.
    """
    circuit = for_pid(pid_name)
    if circuit is None:
        return None
    return {
        "circuit": circuit.circuit,
        "location": circuit.location,
        "color": circuit.color,
        "function": circuit.function,
    }


def as_dict(category: str | None = None) -> dict:
    """The whole reference as plain data. ``category`` filters the circuits.

    Grounds and fuses belong to the full map, so they are included only when no
    category filter is applied - the same rule the printed view follows.
    """
    data: dict = {
        "diag_request_id": DIAG_REQUEST_ID,
        "diag_response_id": DIAG_RESPONSE_ID,
        "buses": [
            {"name": b.name, "speed_kbps": b.speed_kbps, "standard": b.standard,
             "where": b.where, "access": b.access}
            for b in BUSES
        ],
        "dlc_pins": [
            {"pin": p.pin, "function": p.function, "note": p.note}
            for p in DLC_PINS
        ],
        "circuits": [
            {"circuit": c.circuit, "connector": c.connector, "pin": c.pin,
             "location": c.location, "color": c.color, "function": c.function,
             "category": c.category, "pid": c.pid, "note": c.note}
            for c in circuits(category)
        ],
    }
    if category is None:
        data["grounds"] = [
            {"name": g.name, "location": g.location, "serves": g.serves,
             "note": g.note}
            for g in GROUNDS
        ]
        data["ground_spec"] = GROUND_SPEC
        data["fuses"] = [
            {"panel": f.panel, "position": f.position, "rating": f.rating,
             "feeds": f.feeds, "circuit": f.circuit, "note": f.note}
            for f in FUSES
        ]
    return data
