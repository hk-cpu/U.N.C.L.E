"""Diagnostic trouble codes: decoding raw bytes and explaining what they mean."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .data import dtc_codes

CODE_PATTERN = re.compile(r"^[PCBU][0-3][0-9A-F]{3}$")

#: The letter encoded in the top two bits of the first DTC byte.
_LETTERS = ("P", "C", "B", "U")

#: How urgently a code should be dealt with.
SEVERITY_ORDER = {"critical": 0, "serious": 1, "moderate": 2, "advisory": 3}


@dataclass
class Dtc:
    """A single trouble code, with everything we know about it."""

    code: str
    description: str
    #: "stored", "pending" or "permanent"
    status: str = "stored"
    system: str = ""
    origin: str = ""
    severity: str = "moderate"
    causes: list[str] = field(default_factory=list)
    #: True when only the code range is known, not the specific fault.
    generic_fallback: bool = False

    def __str__(self) -> str:
        return f"{self.code}: {self.description}"

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "description": self.description,
            "status": self.status,
            "system": self.system,
            "origin": self.origin,
            "severity": self.severity,
            "causes": list(self.causes),
            "known_code": not self.generic_fallback,
        }


def decode_bytes(data: bytes) -> list[str]:
    """Turn a run of two-byte DTC fields into code strings.

    ``0x0143`` becomes ``"P0143"``. All-zero pairs are padding and are dropped.
    """
    codes: list[str] = []
    for index in range(0, len(data) - 1, 2):
        first, second = data[index], data[index + 1]
        if first == 0 and second == 0:
            continue
        codes.append(decode_pair(first, second))
    return codes


def decode_pair(first: int, second: int) -> str:
    letter = _LETTERS[(first & 0xC0) >> 6]
    return f"{letter}{(first & 0x30) >> 4}{first & 0x0F:X}{(second & 0xF0) >> 4:X}{second & 0x0F:X}"


def encode(code: str) -> bytes:
    """Inverse of :func:`decode_pair`, used by the simulator and the tests."""
    code = code.strip().upper()
    if not CODE_PATTERN.match(code):
        raise ValueError(f"not a valid trouble code: {code!r}")
    first = (_LETTERS.index(code[0]) << 6) | (int(code[1]) << 4) | int(code[2], 16)
    second = (int(code[3], 16) << 4) | int(code[4], 16)
    return bytes((first, second))


def describe(code: str, status: str = "stored") -> Dtc:
    """Build a :class:`Dtc` for a code string, falling back to its code range."""
    code = code.strip().upper()
    system = dtc_codes.SYSTEM_BY_LETTER.get(code[0], "unknown system")
    origin = dtc_codes.ORIGIN_BY_DIGIT.get(code[1], "unknown origin")

    description = dtc_codes.DESCRIPTIONS.get(code)
    fallback = description is None
    if fallback:
        description = _fallback_description(code, system, origin)

    return Dtc(
        code=code,
        description=description,
        status=status,
        system=system,
        origin=origin,
        severity=severity_of(code),
        causes=list(dtc_codes.CAUSES.get(code, ())),
        generic_fallback=fallback,
    )


def _fallback_description(code: str, system: str, origin: str) -> str:
    """The best description we can give for a code that is not in the table."""
    if code[1] in ("1", "2", "3") and code[0] == "P":
        return (
            f"Manufacturer specific powertrain code - look this one up against "
            f"your car's make, since {code} means different things on different vehicles"
        )
    if code[0] == "P":
        subsystem = dtc_codes.P_SUBSYSTEM.get(code[2], "an unclassified subsystem")
        return f"Generic powertrain fault in {subsystem}"
    return f"{system} fault, {origin}"


def severity_of(code: str) -> str:
    """A rough triage bucket, used to sort and colour output."""
    if code in dtc_codes.CRITICAL_CODES:
        return "critical"
    if code.startswith("P03"):          # misfires damage the catalyst
        return "serious"
    if code.startswith("U0"):           # a module has dropped off the bus
        return "serious"
    if code[0] == "C":                  # brakes, traction, steering
        return "serious"
    if code.startswith(("P044", "P045", "P042", "P043")):
        return "advisory"               # emissions - fails a test, drives fine
    if code[0] == "B":
        return "advisory"
    return "moderate"


def sort_key(item: Dtc) -> tuple[int, str]:
    return (SEVERITY_ORDER.get(item.severity, 9), item.code)


def summarise(codes: list[Dtc]) -> str:
    """One line describing a set of codes, for the top of a report."""
    if not codes:
        return "No trouble codes stored."

    stored = [item for item in codes if item.status == "stored"]
    pending = [item for item in codes if item.status == "pending"]
    permanent = [item for item in codes if item.status == "permanent"]

    parts = []
    if stored:
        parts.append(f"{len(stored)} stored")
    if pending:
        parts.append(f"{len(pending)} pending")
    if permanent:
        parts.append(f"{len(permanent)} permanent")

    worst = min(codes, key=sort_key).severity
    return f"{', '.join(parts)} trouble code(s); most severe is {worst}."
