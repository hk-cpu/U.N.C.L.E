"""Reference for the 2006 Charger's CAN-C broadcast frames, for a sniffer.

Unlike the diagnostic traffic cardiag reads over the OBD port (request 0x7E0,
reply 0x7E8, decoded by :mod:`cardiag.pids`), the live CAN-C bus broadcasts the
car's state continuously and fast - engine RPM at ~75 Hz, wheel speeds and brake
pressure at ~37 Hz. Capturing that passively (a transceiver on the D65/D64 pair,
`candump`, SavvyCAN) is how you log at a rate standard OBD polling can't touch.

This module is the map for *reading* that capture: which arbitration ID is which
frame, how often it comes, and which bytes carry which signal. Two honesty rules
run through it, because the data is reverse-engineered, not from a factory DBC:

* It carries byte *locations* and the signal each is observed to track, not
  verified scale factors. So the annotator shows the raw integer at a signal's
  bytes and names what it tracks - it does not invent an engineering value
  (an RPM, a km/h) it cannot stand behind. You find the scale by correlating
  against a known diagnostic reading, which is exactly what the capture is for.
* Every frame carries a confidence, straight from how well the source
  characterised it, so a strong match and a bare observation are told apart.

It transmits nothing. It labels frames the car already broadcasts.

Sources: the reverse-engineering captures the vehicle reference cites (Colorado
State University's 500 kbps CAN-C logs, the latonita / BiggRanger Chrysler CAN
repositories), cross-checked against the FSM.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Confidence in a frame's reverse-engineered definition.
MATCHED = "matched"       # signal matched against a reference trace (e.g. VBox)
OBSERVED = "observed"     # byte activity seen and attributed, scale unconfirmed


@dataclass(frozen=True)
class Signal:
    """A signal observed within a frame: where it sits, and what it tracks.

    ``length`` is in bytes, big-endian (MSB first), which is how CAN-C carries
    these. ``signed`` marks a two's-complement value. No scale factor is stored,
    deliberately - see the module docstring.
    """

    name: str
    start_byte: int
    length: int
    signed: bool = False
    note: str = ""


#: The two 11-bit buses this module maps. They share an ID number space, so the
#: same arbitration ID means different things on each - 0x210 is brake pressure
#: on CAN-C but the instrument-panel dimmer on CAN-B. Frames are therefore kept
#: in separate tables and never merged into one lookup.
CAN_C = "CAN-C (500k)"
CAN_B = "CAN-B (83.3k)"


@dataclass(frozen=True)
class BroadcastFrame:
    can_id: int
    name: str
    hz: float
    dlc: int
    confidence: str
    signals: tuple[Signal, ...] = ()
    note: str = ""
    bus: str = CAN_C

    @property
    def code(self) -> str:
        return f"0x{self.can_id:03X}"


# ---------------------------------------------------------------------------
# The frames (CAN-C, 500 kbps, 11-bit). Curated to the ones the source
# characterised; the many cyclic status frames it could only time are left out
# rather than listed as if their contents were known.
# ---------------------------------------------------------------------------

_FRAMES: tuple[BroadcastFrame, ...] = (
    BroadcastFrame(0x0A0, "Status / control", 10.0, 1, OBSERVED),
    BroadcastFrame(
        0x200, "Wheel / vehicle speeds", 37.4, 8, MATCHED,
        (Signal("speed", 2, 2, note="matched to a VBox speed trace"),
         Signal("speed", 4, 2), Signal("speed", 6, 2)),
        "Three 16-bit speed signals; individual wheels not separated."),
    BroadcastFrame(
        0x208, "Wheel / vehicle speeds", 37.4, 8, MATCHED,
        (Signal("speed", 4, 2), Signal("speed", 6, 2))),
    BroadcastFrame(
        0x210, "Brake pressure", 37.5, 8, MATCHED,
        (Signal("brake_pressure", 2, 1,
                note="master-cylinder / brake-line pressure"),)),
    BroadcastFrame(0x212, "ABS / brake dynamics", 37.5, 8, OBSERVED),
    BroadcastFrame(
        0x215, "Speed & distance", 37.4, 7, MATCHED,
        (Signal("speed", 0, 2),
         Signal("distance", 2, 2, note="cumulative counter"),
         Signal("distance", 4, 2, note="cumulative counter"))),
    BroadcastFrame(
        0x236, "Steering input", 74.7, 8, MATCHED,
        (Signal("steering_angle", 0, 2, signed=True,
                note="wheel angle / rate"),)),
    BroadcastFrame(0x248, "Powertrain / TCM broadcast", 50.0, 8, OBSERVED),
    BroadcastFrame(
        0x300, "Signed dynamic", 37.4, 8, OBSERVED,
        (Signal("dynamic", 6, 2, signed=True,
                note="two's-complement dynamic value"),)),
    BroadcastFrame(0x308, "Powertrain (high rate)", 75.0, 8, OBSERVED),
    BroadcastFrame(
        0x312, "Engine data (RPM / torque / throttle)", 75.0, 8, OBSERVED,
        note="The fastest engine-state frame - the one worth capturing for a "
             "pull. The source attributes RPM, torque and throttle to it but "
             "does not split the bytes, so no signal is decoded here: capture "
             "the raw frame and correlate it against a diagnostic RPM read to "
             "find the layout."),
    BroadcastFrame(0x315, "Powertrain / chassis", 50.0, 6, OBSERVED),
    BroadcastFrame(0x325, "Powertrain / chassis", 50.0, 7, OBSERVED),
    BroadcastFrame(
        0x328, "Chassis / dynamic", 37.4, 8, OBSERVED,
        (Signal("dynamic", 2, 1, note="byte 2 seen changing; scale unknown"),)),
    BroadcastFrame(
        0x338, "Speed & steering / yaw", 37.5, 8, MATCHED,
        (Signal("speed", 0, 2),
         Signal("steering_or_yaw", 6, 2, signed=True))),
    BroadcastFrame(
        0x3D0, "Dynamic status (independent)", 7.5, 4, OBSERVED,
        (Signal("dynamic", 0, 1), Signal("dynamic", 1, 1)),
        "Two independent single-byte signals; not characterised further."),
    BroadcastFrame(
        0x3E4, "Distance / odometer", 1.0, 3, MATCHED,
        (Signal("distance", 1, 2, note="odometer-like counter"),)),
    BroadcastFrame(
        0x3E6, "Status flag", 2.0, 1, OBSERVED,
        (Signal("status_flag", 0, 1),)),
    BroadcastFrame(
        0x3FF, "Dynamic status", 20.0, 6, OBSERVED,
        (Signal("dynamic", 0, 1),)),
    BroadcastFrame(
        0x408, "Dynamic status (low-res)", 10.0, 8, OBSERVED,
        (Signal("dynamic", 1, 1, note="low-resolution"),)),
    BroadcastFrame(
        0x410, "Dynamic status (low-res)", 50.0, 8, OBSERVED,
        (Signal("dynamic", 1, 1, note="low-resolution"),)),
    BroadcastFrame(
        0x415, "Dynamic status", 7.5, 7, OBSERVED,
        (Signal("dynamic", 1, 1),)),
    BroadcastFrame(
        0x445, "Level sensor (fuel / suspension)", 16.7, 6, OBSERVED,
        (Signal("level", 0, 1,
                note="named by the source as a level-sensor-like signal; "
                     "which level and the scale are unconfirmed"),)),
    BroadcastFrame(
        0x608, "Dynamic status (multi-byte)", 7.5, 8, OBSERVED,
        (Signal("dynamic", 0, 1), Signal("dynamic", 5, 1),
         Signal("dynamic", 6, 1))),
)

BY_ID: dict[int, BroadcastFrame] = {frame.can_id: frame for frame in _FRAMES}


# ---------------------------------------------------------------------------
# CAN-B (interior / body, 83.3 kbps). This is the bus the overhead console and
# radio sit on - the one cardiag's canbus.py names but does not yet decode.
#
# HONESTY, loudly: every entry below is REVERSE-ENGINEERED FROM A DIFFERENT CAR.
# The signal meanings come from latonita's Jeep Grand Cherokee WH capture and
# BiggRanger's Grand Cherokee / Durango radio work - not from a 2006 LX Charger.
# CAN-B layouts differ by platform, so each ID here is a STARTING HYPOTHESIS to
# confirm against an 83.3k capture from the actual car, never a decoded fact.
# They are kept note-only (no byte offsets, no scale) because none is verified
# on this platform; hz/dlc are 0 where the source did not record them.
# ---------------------------------------------------------------------------

#: One line to print above the CAN-B table so the caveat is never lost.
CANB_CAVEAT = ("Reverse-engineered from a Jeep (Grand Cherokee WH / Durango), "
               "not a 2006 LX Charger - treat every ID as a hypothesis to "
               "confirm against an 83.3k capture from the car.")

_CANB_FRAMES: tuple[BroadcastFrame, ...] = (
    BroadcastFrame(0x000, "Ignition state", 0.0, 0, OBSERVED, bus=CAN_B,
                   note="Off / ACC / running / cranking. The radio needs it to "
                        "wake (BiggRanger)."),
    BroadcastFrame(0x002, "Engine speed / road speed", 0.0, 0, OBSERVED,
                   bus=CAN_B, note="RPM, vehicle speed and wheel-pulse counters "
                                   "(latonita)."),
    BroadcastFrame(0x003, "Gear selector / brake", 0.0, 0, OBSERVED, bus=CAN_B,
                   note="PRND position, brake switch, ESP-off bit (latonita)."),
    BroadcastFrame(0x006, "Turn signals / hazards", 0.0, 0, OBSERVED, bus=CAN_B),
    BroadcastFrame(0x013, "Doors / seatbelt", 0.0, 0, OBSERVED, bus=CAN_B,
                   note="Door-ajar bits and driver belt-unbuckled (latonita)."),
    BroadcastFrame(0x014, "Odometer / outside temp", 0.0, 0, OBSERVED, bus=CAN_B,
                   note="24-bit odometer and outside air temperature "
                        "(latonita)."),
    BroadcastFrame(0x015, "Charging voltage", 0.0, 0, OBSERVED, bus=CAN_B,
                   note="Battery / charging voltage, ~0.1 V per count "
                        "(latonita)."),
    BroadcastFrame(0x01B, "VIN", 0.0, 0, OBSERVED, bus=CAN_B,
                   note="VIN across three multiplexed frames (latonita)."),
    BroadcastFrame(0x02C, "Coolant temperature", 0.0, 0, OBSERVED, bus=CAN_B,
                   note="Coolant temp, ~raw minus 40 degC (latonita)."),
    BroadcastFrame(0x0D0, "Parking brake", 0.0, 0, OBSERVED, bus=CAN_B),
    BroadcastFrame(0x0F0, "Time set", 0.0, 0, OBSERVED, bus=CAN_B,
                   note="Radio -> SKREEM when the clock is changed "
                        "(BiggRanger)."),
    BroadcastFrame(0x09F, "Radio source / mode", 0.0, 0, OBSERVED, bus=CAN_B,
                   note="RAR radio source/mode; button bits unconfirmed "
                        "(latonita)."),
    BroadcastFrame(0x159, "Fuel level", 0.0, 0, OBSERVED, bus=CAN_B,
                   note="Fuel level, ~0.5 L per count, uncalibrated; a validity "
                        "flag rides a separate byte (latonita)."),
    BroadcastFrame(0x1A7, "Fuel flow", 0.0, 0, OBSERVED, bus=CAN_B,
                   note="Instantaneous fuel flow (latonita)."),
    BroadcastFrame(0x1AC, "Dimmer wheel", 0.0, 0, OBSERVED, bus=CAN_B,
                   note="Dash dimmer-wheel position (latonita)."),
    BroadcastFrame(0x1B2, "4WD lever", 0.0, 0, OBSERVED, bus=CAN_B),
    BroadcastFrame(0x1B4, "Compass heading", 2.0, 0, OBSERVED, bus=CAN_B,
                   note="Compass heading, ~2 deg per count, 500 ms cadence "
                        "(latonita)."),
    BroadcastFrame(0x1C8, "Exterior lights", 0.0, 0, OBSERVED, bus=CAN_B),
    BroadcastFrame(0x210, "Instrument-panel dimmer", 0.0, 0, OBSERVED, bus=CAN_B,
                   note="Panel dimmer level (latonita). NOTE: 0x210 on CAN-C is "
                        "brake pressure - different bus, different meaning."),
    BroadcastFrame(0x2C0, "Accelerator pedal", 0.0, 0, OBSERVED, bus=CAN_B,
                   note="Accelerator pedal position (latonita)."),
    BroadcastFrame(0x370, "Wiper sweep", 0.0, 0, OBSERVED, bus=CAN_B),
    BroadcastFrame(0x394, "Radio buttons", 0.0, 0, OBSERVED, bus=CAN_B,
                   note="RAR radio mode / button bits; unconfirmed (latonita)."),
    BroadcastFrame(0x3D0, "Audio settings", 0.0, 0, OBSERVED, bus=CAN_B,
                   note="Volume, bass, mid, treble, balance, fade (latonita). "
                        "NOTE: 0x3D0 on CAN-C is an unrelated dynamic frame."),
    BroadcastFrame(0x3DD, "VES-present (inject)", 0.0, 0, OBSERVED, bus=CAN_B,
                   note="latonita's VES enabler: injecting 03 00 00 00 00 00 00 "
                        "00 tells the RAR radio a rear-ent unit is present, "
                        "which switches its AUX input on."),
    BroadcastFrame(0x3EC, "Time broadcast", 1.0, 0, OBSERVED, bus=CAN_B,
                   note="Current time from the SKREEM module, ~1 Hz "
                        "(BiggRanger)."),
    BroadcastFrame(0x400, "Network management (OSEK)", 0.0, 0, OBSERVED,
                   bus=CAN_B, note="0x400-0x41E is the OSEK NM ring - bus "
                                   "housekeeping, not sensor data (latonita)."),
)

CANB_BY_ID: dict[int, BroadcastFrame] = {f.can_id: f for f in _CANB_FRAMES}

#: Diagnostic IDs, named so a capture annotator does not treat them as unknown.
DIAGNOSTIC_IDS = {
    0x7DF: "OBD functional request (all ECUs)",
    0x7E0: "OBD request -> PCM",
    0x7E8: "OBD reply <- PCM",
    0x7E1: "OBD request -> TCM",
    0x7E9: "OBD reply <- TCM",
    0x7E2: "OBD request -> ABS/ESP",
    0x7EA: "OBD reply <- ABS/ESP",
}


def frames(bus: str | None = None) -> list[BroadcastFrame]:
    """CAN-C frames by default; pass ``CAN_B`` for the interior-bus table."""
    if bus == CAN_B:
        return list(_CANB_FRAMES)
    return list(_FRAMES)


def canb_frames() -> list[BroadcastFrame]:
    """The interior-bus (CAN-B) reference table. See CANB_CAVEAT first."""
    return list(_CANB_FRAMES)


def get(can_id: int) -> BroadcastFrame | None:
    """A CAN-C frame by ID. CAN-B lives in CANB_BY_ID; the two are never merged,
    because an ID means different things on each bus."""
    return BY_ID.get(can_id)


@dataclass
class Reading:
    """One signal read raw out of a frame's bytes - no scale applied."""

    name: str
    raw: int
    start_byte: int
    length: int
    signed: bool
    note: str = ""

    def to_dict(self) -> dict:
        return {"name": self.name, "raw": self.raw, "start_byte": self.start_byte,
                "length": self.length, "signed": self.signed, "note": self.note}


@dataclass
class Annotation:
    can_id: int
    name: str
    confidence: str
    known: bool
    diagnostic: bool
    readings: list[Reading] = field(default_factory=list)
    note: str = ""

    @property
    def code(self) -> str:
        return f"0x{self.can_id:03X}"

    def to_dict(self) -> dict:
        return {"id": self.code, "name": self.name, "confidence": self.confidence,
                "known": self.known, "diagnostic": self.diagnostic,
                "readings": [r.to_dict() for r in self.readings], "note": self.note}


def annotate(can_id: int, data: bytes) -> Annotation:
    """Name a frame and read the raw values at its documented signal bytes.

    Raw only: no scale is applied, because none is verified. A value whose bytes
    run past the data present is skipped rather than guessed at.
    """
    if can_id in DIAGNOSTIC_IDS:
        return Annotation(can_id, DIAGNOSTIC_IDS[can_id], MATCHED,
                          known=True, diagnostic=True,
                          note="Diagnostic traffic - decode with the OBD tools.")

    frame = BY_ID.get(can_id)
    if frame is None:
        return Annotation(can_id, "unknown", "", known=False, diagnostic=False)

    readings: list[Reading] = []
    for signal in frame.signals:
        end = signal.start_byte + signal.length
        if end > len(data):
            continue
        raw = int.from_bytes(data[signal.start_byte:end], "big", signed=signal.signed)
        readings.append(Reading(signal.name, raw, signal.start_byte,
                                signal.length, signal.signed, signal.note))

    return Annotation(can_id, frame.name, frame.confidence, known=True,
                      diagnostic=False, readings=readings, note=frame.note)
