"""A simulated vehicle that speaks ELM327, for development and demos.

``sim://`` gives you a car on the bench: it answers AT commands, advertises a
realistic set of PIDs, produces live values that move the way a real engine's
do, and (on the ``faulty`` profile) stores trouble codes and a freeze frame.
"""

from __future__ import annotations

import math
import random
import time

from .base import Transport

#: PIDs the simulated ECU supports, mirroring a mid-2010s petrol car.
SUPPORTED_PIDS = {
    0x00, 0x01, 0x03, 0x04, 0x05, 0x06, 0x07, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F,
    0x10, 0x11, 0x13, 0x14, 0x15, 0x1F,
    0x20, 0x21, 0x2F, 0x30, 0x31, 0x33,
    0x40, 0x42, 0x45, 0x46, 0x49, 0x4C, 0x4D, 0x4E, 0x51, 0x5C, 0x5E,
}

VIN = "1HGCM82633A004352"
ECU_NAME = "ECM-EngineControl"

#: A valid 2006 Dodge Charger R/T VIN: 2B3 (Canadian-built Dodge car),
#: position 8 'H' for the 5.7 HEMI, position 10 '6' for 2006, built at
#: Brampton. The check digit is correct, so profile matching accepts it.
CHARGER_VIN = "2B3KA53H66H123456"

#: Bank 2 sensors exist on a V8 and not on the inline four the default
#: simulated car models, so V8 profiles advertise more PIDs.
V8_EXTRA_PIDS = {0x08, 0x09, 0x18, 0x19, 0x3D, 0x3F}

#: Mode 06 monitors a four-cylinder car reports: two oxygen sensors, one
#: catalyst, and a misfire counter per cylinder ($A2 is cylinder 1).
#: The bitmap MIDs ($20, $40, $60, $80, $A0) have to be present too - each one
#: advertises the next, and discovery stops at the first gap in that chain.
SUPPORTED_MONITORS = {0x00, 0x01, 0x02,
                      0x20, 0x40, 0x41, 0x60, 0x80, 0xA0, 0xA1,
                      0xA2, 0xA3, 0xA4, 0xA5}

#: A V8 adds bank 2's sensors and catalyst, and four more cylinders.
V8_EXTRA_MONITORS = {0x05, 0x06, 0x42, 0xA6, 0xA7, 0xA8, 0xA9}

#: What the ECU says is loaded. Recording these is how a reflash is detected.
CALIBRATION_ID = "68RT0057AA"
CALIBRATION_VERIFICATION = "4A1B7C2D"

PROFILES = {
    "default": {
        "codes": [],
        "pending": [],
        "mil": False,
        "description": "healthy car, all monitors complete",
    },
    "faulty": {
        "codes": ["P0420", "P0171", "P0301"],
        "pending": ["P0128"],
        "mil": True,
        "description": "check-engine light on with three stored codes",
    },
    "emissions": {
        "codes": [],
        "pending": [],
        "mil": False,
        "not_ready": True,
        "description": "codes cleared recently, monitors not yet complete",
    },
    "charger": {
        "codes": [],
        "pending": [],
        "mil": False,
        "vin": CHARGER_VIN,
        "v8": True,
        "ecu_name": "ECM-HEMI-5.7",
        # An even scatter of counts across all eight cylinders: normal.
        "misfire_counts": {1: 3, 2: 2, 3: 4, 4: 2, 5: 3, 6: 3, 7: 2, 8: 4},
        "catalyst_ratio": {1: 0.21, 2: 0.24},
        "description": "healthy 2006 Dodge Charger R/T, 5.7 HEMI V8",
    },
    "charger-wear": {
        # The case worth catching: no codes, no warning light, nothing a code
        # reader would show. But the MDS cylinders are already counting misfires
        # several times the others, and bank 2's catalyst monitor is nearly at
        # its limit. This is what "before it breaks" looks like.
        "codes": [],
        "pending": [],
        "mil": False,
        "vin": CHARGER_VIN,
        "v8": True,
        "ecu_name": "ECM-HEMI-5.7",
        "misfire_counts": {1: 22, 2: 3, 3: 2, 4: 28, 5: 4, 6: 19, 7: 25, 8: 3},
        "catalyst_ratio": {1: 0.22, 2: 0.69},
        "description": "2006 Charger R/T, no codes but wear showing in mode 06",
    },
    "charger-misfire": {
        # A misfire on cylinder 4 - one of the four MDS cylinders - alongside a
        # lean bank 2, which together is the classic worn-lifter picture.
        "codes": ["P0304", "P0174", "P0300"],
        "pending": ["P0430"],
        "mil": True,
        "vin": CHARGER_VIN,
        "v8": True,
        "ecu_name": "ECM-HEMI-5.7",
        "lean_bank": 2,
        # Cylinder 4 has failed outright, and 1, 6 and 7 - the other three MDS
        # cylinders - are already counting well above the non-MDS four. That
        # skew is the pattern a worn MDS lifter set produces.
        "misfire_counts": {1: 41, 2: 3, 3: 2, 4: 168, 5: 4, 6: 37, 7: 45, 8: 3},
        "catalyst_ratio": {1: 0.23, 2: 0.68},
        "description": "2006 Charger R/T with an MDS-cylinder misfire",
    },
}


class SimulatorTransport(Transport):
    """Implements just enough of an ELM327 plus an ECU to exercise the tool."""

    def __init__(self, profile: str = "default", timeout: float = 5.0) -> None:
        super().__init__(timeout=timeout)
        if profile not in PROFILES:
            known = ", ".join(sorted(PROFILES))
            raise ValueError(f"unknown simulator profile {profile!r} (try: {known})")
        self.profile = PROFILES[profile]
        self.profile_name = profile
        self.description = f"simulated vehicle ({profile})"

        self._out = bytearray()
        self._line = bytearray()
        self._echo = True
        self._headers = False
        self._start = time.monotonic()
        self._random = random.Random(0xB0BCAF)
        self._codes = list(self.profile["codes"])
        self._pending = list(self.profile["pending"])
        self._mil = bool(self.profile["mil"])
        self._cleared_at: float | None = None

        self.vin = self.profile.get("vin", VIN)
        self.ecu_name = self.profile.get("ecu_name", ECU_NAME)
        self.supported = set(SUPPORTED_PIDS)
        self.monitors = set(SUPPORTED_MONITORS)
        if self.profile.get("v8"):
            self.supported |= V8_EXTRA_PIDS
            self.monitors |= V8_EXTRA_MONITORS

    # -- transport plumbing ------------------------------------------------
    def open(self) -> None:
        self._out.clear()
        self._line.clear()
        self._start = time.monotonic()

    def close(self) -> None:
        self._out.clear()

    def _write_raw(self, data: bytes) -> None:
        for byte in data:
            if byte in (0x0D, 0x0A):
                line = self._line.decode("ascii", "ignore").strip()
                self._line.clear()
                if line:
                    self._handle(line)
                else:
                    self._emit("")           # bare CR repeats the prompt
            else:
                self._line.append(byte)

    def _read_raw(self, timeout: float) -> bytes:
        if not self._out:
            return b""
        chunk = bytes(self._out)
        self._out.clear()
        return chunk

    def _emit(self, body: str) -> None:
        if body:
            self._out.extend(body.encode("ascii"))
            self._out.extend(b"\r")
        self._out.extend(b"\r>")

    # -- command dispatch --------------------------------------------------
    def _handle(self, line: str) -> None:
        if self._echo:
            self._out.extend(line.encode("ascii") + b"\r")

        command = line.replace(" ", "").upper()

        if command.startswith("AT"):
            self._emit(self._at(command[2:]))
            return

        response = self._obd(command)
        self._emit(response)

    def _at(self, command: str) -> str:
        if command in ("Z", "WS"):
            self._echo = True
            self._headers = False
            return "ELM327 v1.5"
        if command == "I":
            return "ELM327 v1.5"
        if command.startswith("E"):
            self._echo = command.endswith("1")
            return "OK"
        if command.startswith("H"):
            self._headers = command.endswith("1")
            return "OK"
        if command == "RV":
            # Alternator charging voltage, wobbling slightly like the real thing.
            return f"{14.1 + math.sin(self._elapsed() / 3) * 0.2:.1f}V"
        if command == "DPN":
            return "A6"
        if command.startswith(("L", "S", "SP", "ST", "AT", "CAF", "M")):
            return "OK"
        return "OK"

    def _obd(self, command: str) -> str:
        # A trailing digit is the "expected frames" hint; it is not part of the
        # request, so strip it when the rest already forms whole bytes.
        if len(command) % 2 and len(command) > 2:
            command = command[:-1]

        try:
            request = bytes.fromhex(command)
        except ValueError:
            return "?"

        if not request:
            return "?"

        mode = request[0]

        if mode == 0x01 or mode == 0x02:
            return self._mode_01_02(request, mode)
        if mode == 0x03:
            return self._dtc_response(0x43, self._codes)
        if mode == 0x07:
            return self._dtc_response(0x47, self._pending)
        if mode == 0x0A:
            return self._dtc_response(0x4A, [])
        if mode == 0x04:
            self._codes.clear()
            self._pending.clear()
            self._mil = False
            self._cleared_at = time.monotonic()
            return "44"
        if mode == 0x06:
            return self._mode_06(request)
        if mode == 0x09:
            return self._mode_09(request)
        return "NO DATA"

    # -- mode 06 -----------------------------------------------------------
    def _mode_06(self, request: bytes) -> str:
        if len(request) < 2:
            return "NO DATA"
        mid = request[1]

        if mid == 0x00:
            return _multiline(bytes([0x46]) + _mode06_bitmap(0x00, self.monitors))
        if mid == 0x20:
            return _multiline(bytes([0x46]) + _mode06_bitmap(0x20, self.monitors))
        if mid in (0x40, 0x60, 0x80):
            return _multiline(bytes([0x46]) + _mode06_bitmap(mid, self.monitors))
        if mid == 0xA0:
            return _multiline(bytes([0x46]) + _mode06_bitmap(0xA0, self.monitors))

        if mid not in self.monitors:
            return "NO DATA"

        records = self._monitor_records(mid)
        if not records:
            return "NO DATA"

        payload = bytes([0x46]) + b"".join(records)
        return _multiline(payload) if len(payload) > 7 else _hex(payload)

    def _monitor_records(self, mid: int) -> list[bytes]:
        """Build the nine-byte records this monitor reports."""
        from ..mode06 import misfire_cylinder

        cylinder = misfire_cylinder(mid)
        if cylinder is not None:
            counts = self.profile.get("misfire_counts", {})
            value = counts.get(cylinder, 0)
            # TID $0B, scaling $24 (counts). The ECU's failure threshold on a
            # misfire counter is the count that would set a code.
            return [_record(mid, 0x0B, 0x24, value, 0, 200)]

        if mid in (0x41, 0x42):
            # Catalyst efficiency: a switch ratio, lower is healthier. Bank 2 is
            # the one degrading on the misfire profile.
            bank = 1 if mid == 0x41 else 2
            ratio = self.profile.get("catalyst_ratio", {}).get(bank, 0.24)
            return [_record(mid, 0x80, 0x05, int(ratio / 0.0000305), 0,
                            int(0.75 / 0.0000305))]

        if mid in (0x01, 0x05):
            # Upstream oxygen sensor switch time.
            return [_record(mid, 0x07, 0x03, 42, 0, 200)]

        if mid in (0x02, 0x06):
            return [_record(mid, 0x08, 0x03, 31, 0, 200)]

        return []

    def _mode_01_02(self, request: bytes, mode: int) -> str:
        if len(request) < 2:
            return "NO DATA"
        pid = request[1]
        frame_number = request[2] if mode == 0x02 and len(request) > 2 else 0x00

        if mode == 0x02:
            if not self._codes or frame_number != 0x00:
                return "NO DATA"
            data = self._freeze_frame(pid)
            if data is None:
                return "NO DATA"
            return _hex(bytes([0x42, pid, frame_number]) + data)

        if pid not in self.supported:
            return "NO DATA"

        data = self._live_value(pid)
        if data is None:
            return "NO DATA"
        return _hex(bytes([0x41, pid]) + data)

    def _mode_09(self, request: bytes) -> str:
        if len(request) < 2:
            return "NO DATA"
        pid = request[1]
        if pid == 0x00:
            return _hex(bytes([0x49, 0x00])
                        + _bitmap(0x00, {0x02, 0x04, 0x06, 0x0A}))
        if pid == 0x04:
            payload = bytes([0x49, 0x04, 0x01]) + \
                CALIBRATION_ID.encode("ascii").ljust(16, b"\x00")
            return _multiline(payload)
        if pid == 0x06:
            payload = bytes([0x49, 0x06, 0x01]) + \
                bytes.fromhex(CALIBRATION_VERIFICATION)
            return _hex(payload)
        if pid == 0x02:
            payload = bytes([0x49, 0x02, 0x01]) + self.vin.encode("ascii")
            return _multiline(payload)
        if pid == 0x0A:
            payload = bytes([0x49, 0x0A, 0x01]) + self.ecu_name.encode("ascii").ljust(20, b"\x00")
            return _multiline(payload)
        return "NO DATA"

    def _dtc_response(self, reply_mode: int, codes: list[str]) -> str:
        from ..dtc import encode

        payload = bytes([reply_mode, len(codes)])
        for code in codes:
            payload += encode(code)
        if len(payload) > 7:
            return _multiline(payload)
        return _hex(payload)

    # -- the simulated engine ---------------------------------------------
    def _elapsed(self) -> float:
        return time.monotonic() - self._start

    def _engine_state(self) -> dict[str, float]:
        """A short drive cycle: idle, pull away, cruise, slow down, repeat."""
        t = self._elapsed()
        phase = (t % 60.0) / 60.0
        jitter = self._random.uniform(-0.015, 0.015)

        if phase < 0.20:
            throttle, speed_target = 0.06, 0.0
        elif phase < 0.40:
            ramp = (phase - 0.20) / 0.20
            throttle, speed_target = 0.35 + ramp * 0.25, ramp * 70
        elif phase < 0.70:
            throttle, speed_target = 0.22, 70 + math.sin(t / 4) * 6
        else:
            ramp = 1 - (phase - 0.70) / 0.30
            throttle, speed_target = 0.05, max(0.0, ramp * 70)

        throttle = max(0.0, min(1.0, throttle + jitter))
        speed = max(0.0, speed_target)
        rpm = 780 + throttle * 4200 + (speed * 8 if speed > 5 else 0)
        rpm = min(rpm, 6200)

        # Coolant climbs to the thermostat's set point and holds there.
        warmup = min(1.0, t / 180.0)
        coolant = 18 + warmup * 71
        load = min(1.0, 0.12 + throttle * 0.85)

        return {
            "throttle": throttle,
            "speed": speed,
            "rpm": rpm,
            "coolant": coolant,
            "load": load,
            "t": t,
        }

    #: The "system too lean" code for each bank.
    _LEAN_CODE = {1: "P0171", 2: "P0174"}

    def _lean_bank(self) -> int | None:
        """Which bank, if any, is running lean enough to skew its fuel trims.

        Tied to the stored code so that clearing the codes also clears the
        symptom, the way it would on a car whose fault has been fixed.
        """
        configured = self.profile.get("lean_bank")
        candidates = [configured] if configured else [1, 2]
        for bank in candidates:
            if self._LEAN_CODE[bank] in self._codes:
                return bank
        return None

    def _live_value(self, pid: int) -> bytes | None:
        state = self._engine_state()
        # A lean bank shows up as a large positive long-term trim on that bank.
        lean_bank = self._lean_bank()
        v8 = bool(self.profile.get("v8"))

        if pid == 0x00:
            return _bitmap(0x00, self.supported)
        if pid == 0x20:
            return _bitmap(0x20, self.supported)
        if pid == 0x40:
            return _bitmap(0x40, self.supported)
        if pid == 0x01:
            return self._monitor_status()
        if pid == 0x03:
            return bytes([0x02 if state["coolant"] > 60 else 0x01, 0x00])
        if pid == 0x04:
            return bytes([_scale(state["load"])])
        if pid == 0x05:
            return bytes([int(state["coolant"]) + 40])
        if pid == 0x06:
            return bytes([_trim(3.9 if lean_bank == 1 else 0.8)])
        if pid == 0x07:
            return bytes([_trim(21.1 if lean_bank == 1 else 2.3)])
        if pid == 0x08 and v8:
            return bytes([_trim(3.9 if lean_bank == 2 else 1.6)])
        if pid == 0x09 and v8:
            return bytes([_trim(23.4 if lean_bank == 2 else 3.1)])
        if pid == 0x0B:
            return bytes([int(28 + state["throttle"] * 70)])
        if pid == 0x0C:
            return _u16(int(state["rpm"] * 4))
        if pid == 0x0D:
            return bytes([int(state["speed"]) & 0xFF])
        if pid == 0x0E:
            return bytes([int((12 + state["throttle"] * 18 + 64) * 2) & 0xFF])
        if pid == 0x0F:
            return bytes([int(24 + state["throttle"] * 8) + 40])
        if pid == 0x10:
            return _u16(int((1.9 + state["load"] * 42) * 100))
        if pid == 0x11:
            return bytes([_scale(state["throttle"])])
        if pid == 0x13:
            # Bits 0-3 are bank 1 sensors 1-4, bits 4-7 bank 2. A V8 has an
            # upstream and a downstream sensor on each bank.
            return bytes([0x33 if v8 else 0x03])
        if pid in (0x14, 0x15, 0x18, 0x19):
            upstream = pid in (0x14, 0x18)
            # Upstream sensors swing quickly as the ECU trims the mixture;
            # downstream ones sit fairly still behind a working catalyst.
            offset = 0.0 if pid in (0x14, 0x15) else 1.1
            swing = math.sin(state["t"] * (2.4 if upstream else 0.5) + offset)
            voltage = 0.45 + swing * (0.35 if upstream else 0.08)
            return bytes([int(voltage * 200), 0xFF])
        if pid in (0x3D, 0x3F) and v8:
            # Bank 2 catalyst temperatures, rising with load.
            celsius = 320 + state["load"] * 400 + (30 if pid == 0x3F else 0)
            return _u16(int((celsius + 40) * 10))
        if pid == 0x1F:
            return _u16(int(state["t"]) & 0xFFFF)
        if pid == 0x21:
            return _u16(412 if self._mil else 0)
        if pid == 0x2F:
            return bytes([_scale(0.62)])
        if pid == 0x30:
            return bytes([0 if self._cleared_at else 14])
        if pid == 0x31:
            return _u16(0 if self._cleared_at else 1180)
        if pid == 0x33:
            return bytes([101])
        if pid == 0x42:
            return _u16(int((14.1 + math.sin(state["t"] / 3) * 0.2) * 1000))
        if pid == 0x45:
            return bytes([_scale(state["throttle"] * 0.9)])
        if pid == 0x46:
            return bytes([22 + 40])
        if pid == 0x49:
            return bytes([_scale(state["throttle"])])
        if pid == 0x4C:
            return bytes([_scale(state["throttle"] * 0.95)])
        if pid == 0x4D:
            return _u16(37 if self._mil else 0)
        if pid == 0x4E:
            return _u16(0 if self._cleared_at else 2450)
        if pid == 0x51:
            return bytes([0x01])          # petrol
        if pid == 0x5C:
            return bytes([int(min(98, state["coolant"] + 9)) + 40])
        if pid == 0x5E:
            return _u16(int((0.9 + state["load"] * 11) * 20))
        return None

    def _monitor_status(self) -> bytes:
        count = len(self._codes)
        first = (0x80 if self._mil else 0x00) | (count & 0x7F)

        # B: three continuous monitors, all supported and complete.
        second = 0x07
        # C: which non-continuous monitors this engine has.
        third = 0xE1
        # D: which of those are still incomplete.
        if self.profile.get("not_ready") or self._cleared_at:
            fourth = 0xE1                 # nothing has run since the clear
        elif "P0420" in self._codes:
            fourth = 0x01                 # catalyst monitor did not complete
        else:
            fourth = 0x00
        return bytes([first, second, third, fourth])


    def _freeze_frame(self, pid: int) -> bytes | None:
        """The snapshot the ECU stored when the fault was confirmed.

        The trigger code and the skewed fuel trims are taken from whatever this
        profile actually stored, so the freeze frame agrees with mode 03 instead
        of contradicting it.
        """
        from .. import dtc as dtc_module

        lean_bank = self._lean_bank()
        v8 = bool(self.profile.get("v8"))

        # A real ECU stores the frame for the fault it considers most
        # significant, so pick the worst stored code rather than the first.
        trigger = min(
            self._codes,
            key=lambda code: (
                dtc_module.SEVERITY_ORDER[dtc_module.severity_of(code)], code
            ),
            default=None,
        )

        frozen = {
            # PID 02 in mode 02 is the code that caused the frame to be stored.
            0x02: dtc_module.encode(trigger) if trigger else None,
            0x04: bytes([_scale(0.47)]),
            0x05: bytes([89 + 40]),
            0x0B: bytes([44]),
            0x0C: _u16(int(2310 * 4)),
            0x0D: bytes([63]),
            0x0E: bytes([int((18 + 64) * 2)]),
            0x0F: bytes([31 + 40]),
            0x10: _u16(int(14.7 * 100)),
            0x11: bytes([_scale(0.31)]),
            0x06: bytes([_trim(6.2 if lean_bank == 1 else 1.6)]),
            0x07: bytes([_trim(19.5 if lean_bank == 1 else 2.3)]),
        }
        if v8:
            frozen[0x08] = bytes([_trim(6.2 if lean_bank == 2 else 1.6)])
            frozen[0x09] = bytes([_trim(21.9 if lean_bank == 2 else 3.1)])

        return frozen.get(pid)


# ---------------------------------------------------------------------------
# Encoding helpers
# ---------------------------------------------------------------------------

def _scale(fraction: float) -> int:
    return max(0, min(255, int(round(fraction * 255))))


def _trim(percent: float) -> int:
    return max(0, min(255, int(round(percent * 128 / 100 + 128))))


def _u16(value: int) -> bytes:
    value = max(0, min(0xFFFF, value))
    return bytes([value >> 8, value & 0xFF])


def _bitmap(base: int, supported: set[int]) -> bytes:
    bits = 0
    for offset in range(1, 33):
        if base + offset in supported:
            bits |= 1 << (32 - offset)
    return bits.to_bytes(4, "big")


def _record(mid: int, tid: int, scaling: int, value: int,
            minimum: int, maximum: int) -> bytes:
    """One nine-byte mode 06 record."""
    return bytes([mid, tid, scaling]) + _u16(value) + _u16(minimum) + _u16(maximum)


def _mode06_bitmap(base: int, monitors: set[int]) -> bytes:
    """A supported-monitors record: the bitmap sits in the value+min fields."""
    bits = 0
    for offset in range(1, 33):
        if base + offset in monitors:
            bits |= 1 << (32 - offset)
    raw = bits.to_bytes(4, "big")
    return bytes([base, 0x00, 0x01]) + raw + b"\x00\x00"


def _hex(payload: bytes) -> str:
    return payload.hex().upper()


def _multiline(payload: bytes) -> str:
    """Format a payload the way an ELM327 renders a multi-frame CAN reply.

    ISO-TP puts 6 data bytes in the first frame (the other two carry the length)
    and 7 in each consecutive frame, so the segments are not evenly sized.
    """
    lines = [f"{len(payload):03X}", f"0:{payload[:6].hex().upper()}"]
    index = 1
    for start in range(6, len(payload), 7):
        lines.append(f"{index:X}:{payload[start:start + 7].hex().upper()}")
        index += 1
    return "\r".join(lines)
