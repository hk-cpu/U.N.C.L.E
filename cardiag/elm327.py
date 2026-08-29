"""Driver for ELM327-compatible adapters (Vgate USB, generic clones, WiFi)."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

from .transport import Transport, TransportError

#: Adapter replies that mean "your request did not produce vehicle data".
ERROR_REPLIES = {
    "NO DATA": "the ECU had nothing to say for that request",
    "UNABLE TO CONNECT": "no reply from the vehicle - is the ignition on?",
    "BUS INIT: ERROR": "could not initialise the OBD bus",
    "BUS INIT:ERROR": "could not initialise the OBD bus",
    "BUS ERROR": "electrical fault on the OBD bus",
    "CAN ERROR": "CAN bus error - wrong protocol or a wiring fault",
    "DATA ERROR": "the adapter received a corrupted frame",
    "BUFFER FULL": "the adapter's buffer overflowed",
    "FB ERROR": "feedback error - check the adapter's power supply",
    "LV RESET": "the adapter browned out - check the OBD port power",
    "STOPPED": "the request was interrupted",
    "ERROR": "the adapter reported a generic error",
    "?": "the adapter did not understand the command",
}

#: Noise the adapter emits while it works, safe to discard.
_NOISE = re.compile(r"^(SEARCHING\.*|BUS INIT\.*|OK)$", re.IGNORECASE)

_MULTILINE = re.compile(r"^([0-9A-F]):([0-9A-F\s]*)$", re.IGNORECASE)

#: Tried in order after auto-detection fails. Protocol 6 (ISO 15765-4 CAN,
#: 11 bit, 500 kbaud) covers everything from the mid-2000s onward and is the
#: one clone adapters most often need stating outright; 7 is its 29-bit twin.
FALLBACK_PROTOCOLS = ("6", "7")

PROTOCOLS = {
    "0": "automatic",
    "1": "SAE J1850 PWM (41.6 kbaud)",
    "2": "SAE J1850 VPW (10.4 kbaud)",
    "3": "ISO 9141-2 (5 baud init)",
    "4": "ISO 14230-4 KWP (5 baud init)",
    "5": "ISO 14230-4 KWP (fast init)",
    "6": "ISO 15765-4 CAN (11 bit, 500 kbaud)",
    "7": "ISO 15765-4 CAN (29 bit, 500 kbaud)",
    "8": "ISO 15765-4 CAN (11 bit, 250 kbaud)",
    "9": "ISO 15765-4 CAN (29 bit, 250 kbaud)",
    "A": "SAE J1939 CAN (29 bit, 250 kbaud)",
    "B": "user defined CAN 1",
    "C": "user defined CAN 2",
}


class ObdError(Exception):
    """The adapter answered, but not with vehicle data."""


class NoDataError(ObdError):
    """The ECU does not support that request, or had nothing to report."""


@dataclass
class AdapterInfo:
    identifier: str
    protocol: str
    protocol_id: str
    voltage: float | None

    def __str__(self) -> str:
        volts = f"{self.voltage:.1f} V" if self.voltage is not None else "unknown"
        return f"{self.identifier} | {self.protocol} | battery {volts}"


class ELM327:
    """Turns OBD-II requests into adapter commands and back into bytes."""

    def __init__(self, transport: Transport, timeout: float = 5.0) -> None:
        self.transport = transport
        self.timeout = timeout
        self.info: AdapterInfo | None = None
        #: Remembered "expected reply count" per request, which lets the adapter
        #: return as soon as it has the frames instead of waiting out its timer.
        self._reply_counts: dict[str, int] = {}

    # -- session -----------------------------------------------------------
    def connect(self, protocol: str | None = None) -> AdapterInfo:
        """Reset the adapter, configure it, and negotiate a vehicle protocol.

        ``protocol`` forces an ELM327 protocol number (``"6"`` is ISO 15765-4
        CAN, 11 bit, 500 kbaud). Left unset, auto-detection is tried first and
        the protocols in :data:`FALLBACK_PROTOCOLS` after it: cheap ELM327
        clones are known to fail auto-detection against some makes' CAN timing
        while working perfectly once the protocol is stated outright.
        """
        self.transport.open()
        self.transport.flush_input()

        identifier = self._reset()

        # Quiet, machine-friendly output: no echo, no linefeeds, no spaces,
        # no headers. Everything below assumes this configuration.
        for command in ("ATE0", "ATL0", "ATS0", "ATH0", "ATST64"):
            self._at(command)

        attempts = [protocol] if protocol else [None, *FALLBACK_PROTOCOLS]
        failures: list[str] = []

        for attempt in attempts:
            self._at(f"ATSP{attempt}" if attempt else "ATSP0")
            self._reply_counts.clear()      # frame counts differ per protocol
            try:
                protocol_id, described = self._negotiate_protocol()
            except ObdError as exc:
                failures.append(
                    f"{'auto-detect' if attempt is None else f'protocol {attempt}'}: {exc}"
                )
                continue

            self.info = AdapterInfo(
                identifier=identifier,
                protocol=described,
                protocol_id=protocol_id,
                voltage=self.read_voltage(),
            )
            return self.info

        raise ObdError(
            "connected to the adapter but not to the car.\n"
            + "\n".join(f"  {failure}" for failure in failures)
            + "\nTurn the ignition to ON (engine running is best). If it still "
            "fails, name the protocol explicitly - a 2006 Chrysler LX car is "
            "protocol 6."
        )

    def close(self) -> None:
        try:
            self.transport.close()
        except TransportError:
            pass

    def __enter__(self) -> "ELM327":
        self.connect()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _reset(self) -> str:
        """``ATZ`` reboots the adapter; the banner it prints identifies it."""
        self.transport.write_line("ATZ")
        try:
            reply = self.transport.read_until_prompt(timeout=max(self.timeout, 6.0))
        except TransportError:
            # Some clones ignore ATZ but answer the softer ATWS.
            self.transport.flush_input()
            reply = self.transport.command("ATWS", timeout=max(self.timeout, 6.0))

        banner = [line.strip() for line in _split_lines(reply) if line.strip()]
        # Drop the echoed command; echo is still on at this point.
        banner = [line for line in banner if line.upper() not in ("ATZ", "ATWS")]
        return banner[-1] if banner else "unknown adapter"

    def _negotiate_protocol(self) -> tuple[str, str]:
        """Force protocol detection with a real request, then ask what it chose."""
        # A supported-PID request is the cheapest thing every ECU answers, so
        # it doubles as the proof that this protocol actually works.
        self.request(0x01, 0x00)

        raw = self._at("ATDPN").strip().upper()
        # ATDPN answers e.g. "A6" - the leading A means it was auto-detected.
        identifier = raw[-1] if raw else "0"
        return identifier, PROTOCOLS.get(identifier, f"unknown protocol ({raw})")

    def read_voltage(self) -> float | None:
        """Battery voltage measured at the OBD port by the adapter itself."""
        try:
            reply = self._at("ATRV").strip().upper().rstrip("V")
            return float(reply)
        except (ObdError, TransportError, ValueError):
            return None

    # -- commands ----------------------------------------------------------
    def _at(self, command: str) -> str:
        """Send an adapter (AT) command and return its raw reply."""
        raw = self.transport.command(command, timeout=self.timeout)
        lines = [line.strip() for line in _split_lines(raw) if line.strip()]
        lines = [line for line in lines if line.upper() != command.upper()]
        return lines[-1] if lines else ""

    def request(self, mode: int, pid: int | None = None,
                expected_frames: int | None = None) -> bytes:
        """Send an OBD request and return the data bytes of the first reply.

        The mode and PID echo that every ECU prepends is verified and stripped,
        so callers get just the payload.
        """
        frames = self.request_all(mode, pid, expected_frames)
        if not frames:
            raise NoDataError(_no_data_message(mode, pid))
        return frames[0]

    def request_all(self, mode: int, pid: int | None = None,
                    expected_frames: int | None = None) -> list[bytes]:
        """Like :meth:`request` but keeps every responding ECU's payload."""
        command = f"{mode:02X}" + (f"{pid:02X}" if pid is not None else "")

        # Telling the adapter how many frames to expect stops it waiting out
        # its full timeout on every single request; it roughly doubles the
        # sample rate on the live dashboard.
        hint = expected_frames if expected_frames is not None else self._reply_counts.get(command)
        wire = command + (str(hint) if hint else "")

        raw = self.transport.command(wire, timeout=self.timeout)
        frames = parse_response(raw, echo=wire)

        payloads = []
        for frame in frames:
            payload = _strip_echo(frame, mode, pid)
            if payload is not None:
                payloads.append(payload)

        if payloads and hint is None:
            self._reply_counts[command] = len(payloads)
        return payloads

    def request_records(self, mode: int, pid: int) -> list[bytes]:
        """Send a request and return each reply with only the mode byte stripped.

        Mode 06 repeats its monitor ID inside every record, so the usual
        "strip mode and PID" handling would eat the first record's ID and leave
        the rest misaligned. Callers here parse the records themselves.
        """
        command = f"{mode:02X}{pid:02X}"
        raw = self.transport.command(command, timeout=self.timeout)

        payloads = []
        for frame in parse_response(raw, echo=command):
            if not frame:
                continue
            if frame[0] == 0x7F:
                reason = frame[2] if len(frame) > 2 else 0
                raise ObdError(
                    f"the ECU rejected mode {mode:02X} "
                    f"(negative response code 0x{reason:02X})"
                )
            if frame[0] == mode + 0x40:
                payloads.append(frame[1:])
        return payloads

    def clear_codes(self) -> None:
        """Mode 04: erase stored codes, freeze frame and readiness monitors."""
        raw = self.transport.command("04", timeout=max(self.timeout, 8.0))
        frames = parse_response(raw, echo="04")
        for frame in frames:
            if frame and frame[0] == 0x44:
                return
        raise ObdError(
            "the ECU refused to clear the codes. Most cars only accept this "
            "with the ignition on and the engine off."
        )


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

def parse_response(raw: str, echo: str | None = None) -> list[bytes]:
    """Turn an adapter reply into a list of complete response frames.

    Handles the ELM327's two output shapes: one hex line per frame, and the
    indexed ``0:``/``1:``/``2:`` form it uses for multi-frame CAN replies.
    """
    lines = []
    for line in _split_lines(raw):
        line = line.strip()
        if not line or _NOISE.match(line):
            continue
        if echo and line.replace(" ", "").upper() == echo.upper():
            continue
        lines.append(line)

    for line in lines:
        upper = line.upper().strip()
        if upper in ERROR_REPLIES:
            raise _error_for(upper)
        # Some firmwares append the reason to a BUS INIT line.
        if upper.startswith("BUS INIT") and "ERROR" in upper:
            raise ObdError(ERROR_REPLIES["BUS INIT: ERROR"])

    if any(_MULTILINE.match(line) for line in lines):
        return _assemble_multiline(lines)

    frames = []
    for line in lines:
        frame = _hex_to_bytes(line)
        if frame:
            frames.append(frame)
    return frames


def _assemble_multiline(lines: list[str]) -> list[bytes]:
    """Reassemble an indexed CAN reply into one frame.

    The adapter prints the total payload length on its own line, then the
    segments prefixed with their index::

        014
        0:490201314434
        1:47500052 3535
        2:42313233343536
    """
    total_length: int | None = None
    segments: dict[int, bytes] = {}

    for line in lines:
        match = _MULTILINE.match(line)
        if match:
            index = int(match.group(1), 16)
            segments[index] = _hex_to_bytes(match.group(2))
            continue
        compact = line.replace(" ", "")
        if total_length is None and re.fullmatch(r"[0-9A-F]{1,3}", compact, re.IGNORECASE):
            total_length = int(compact, 16)

    if not segments:
        return []

    payload = b"".join(segments[index] for index in sorted(segments))
    if total_length is not None and 0 < total_length <= len(payload):
        payload = payload[:total_length]
    return [payload]


def _strip_echo(frame: bytes, mode: int, pid: int | None) -> bytes | None:
    """Verify a frame answers our request and return just its data bytes."""
    if len(frame) < 1:
        return None

    if frame[0] == 0x7F:
        # Negative response: 7F <mode> <reason>
        reason = frame[2] if len(frame) > 2 else 0
        raise ObdError(
            f"the ECU rejected mode {mode:02X} "
            f"(negative response code 0x{reason:02X})"
        )

    if frame[0] != mode + 0x40:
        return None

    if pid is None:
        return frame[1:]

    if len(frame) < 2 or frame[1] != pid:
        return None
    return frame[2:]


def _hex_to_bytes(text: str) -> bytes:
    compact = re.sub(r"[^0-9A-Fa-f]", "", text)
    if len(compact) % 2:
        # A truncated final nibble means a corrupted frame; drop it rather
        # than silently shifting every byte after it.
        compact = compact[:-1]
    try:
        return bytes.fromhex(compact)
    except ValueError:
        return b""


def _split_lines(raw: str) -> list[str]:
    return raw.replace("\n", "\r").split("\r")


def _error_for(reply: str) -> ObdError:
    detail = ERROR_REPLIES[reply]
    if reply == "NO DATA":
        return NoDataError(detail)
    return ObdError(f"{reply}: {detail}")


def _no_data_message(mode: int, pid: int | None) -> str:
    target = f"mode {mode:02X}" + (f" PID {pid:02X}" if pid is not None else "")
    return f"no response to {target} - the vehicle probably does not support it"
