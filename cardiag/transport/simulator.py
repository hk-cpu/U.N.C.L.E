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
        if mode == 0x09:
            return self._mode_09(request)
        return "NO DATA"

    def _mode_01_02(self, request: bytes, mode: int) -> str:
        if len(request) < 2:
            return "NO DATA"
        pid = request[1]
        frame_number = request[2] if mode == 0x02 and len(request) > 2 else 0x00

        if mode == 0x02:
            if not self._codes or frame_number != 0x00:
                return "NO DATA"
            data = _freeze_frame(pid)
            if data is None:
                return "NO DATA"
            return _hex(bytes([0x42, pid, frame_number]) + data)

        if pid not in SUPPORTED_PIDS:
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
            return _hex(bytes([0x49, 0x00]) + _bitmap(0x00, {0x02, 0x0A}))
        if pid == 0x02:
            payload = bytes([0x49, 0x02, 0x01]) + VIN.encode("ascii")
            return _multiline(payload)
        if pid == 0x0A:
            payload = bytes([0x49, 0x0A, 0x01]) + ECU_NAME.encode("ascii").ljust(20, b"\x00")
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

    def _live_value(self, pid: int) -> bytes | None:
        state = self._engine_state()
        # A lean bank-1 fault shows up as a large positive long-term trim.
        lean = "P0171" in self._codes

        if pid == 0x00:
            return _bitmap(0x00, SUPPORTED_PIDS)
        if pid == 0x20:
            return _bitmap(0x20, SUPPORTED_PIDS)
        if pid == 0x40:
            return _bitmap(0x40, SUPPORTED_PIDS)
        if pid == 0x01:
            return self._monitor_status()
        if pid == 0x03:
            return bytes([0x02 if state["coolant"] > 60 else 0x01, 0x00])
        if pid == 0x04:
            return bytes([_scale(state["load"])])
        if pid == 0x05:
            return bytes([int(state["coolant"]) + 40])
        if pid == 0x06:
            return bytes([_trim(3.9 if lean else 0.8)])
        if pid == 0x07:
            return bytes([_trim(21.1 if lean else 2.3)])
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
            return bytes([0x03])          # B1S1 and B1S2 fitted
        if pid in (0x14, 0x15):
            swing = math.sin(state["t"] * (2.4 if pid == 0x14 else 0.5))
            voltage = 0.45 + swing * (0.35 if pid == 0x14 else 0.08)
            return bytes([int(voltage * 200), 0xFF])
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


def _freeze_frame(pid: int) -> bytes | None:
    """The snapshot the ECU stored when the fault was confirmed."""
    frozen = {
        0x02: bytes.fromhex("0301"),      # the code that set the frame: P0301
        0x04: bytes([_scale(0.47)]),
        0x05: bytes([89 + 40]),
        0x0B: bytes([44]),
        0x0C: _u16(int(2310 * 4)),
        0x0D: bytes([63]),
        0x0E: bytes([int((18 + 64) * 2)]),
        0x0F: bytes([31 + 40]),
        0x10: _u16(int(14.7 * 100)),
        0x11: bytes([_scale(0.31)]),
        0x06: bytes([_trim(6.2)]),
        0x07: bytes([_trim(19.5)]),
    }
    return frozen.get(pid)
