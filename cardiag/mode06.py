"""Mode 06 - on-board monitoring test results.

Mode 06 is where the ECU keeps the actual measurements behind its pass/fail
monitors: the catalyst efficiency it computed, the oxygen sensor switch times,
and - most usefully on this engine - a misfire counter for every cylinder.

Those counters move long before a P030x code is set, so watching them is how a
developing fault gets caught while it is still cheap. A cylinder whose count is
climbing while its neighbours stay flat is a real signal even when nothing has
tripped the warning light yet.

On CAN the reply is a run of nine-byte records::

    46 | MID TID UASID TEST_H TEST_L MIN_H MIN_L MAX_H MAX_L | MID TID ...

Each record carries its own monitor ID, which is why the mode byte is all that
gets stripped before parsing.

Scaling comes from the "unit and scaling ID" in each record. Only the scalings
that are unambiguous are decoded here; anything else is reported as a raw value
with its scaling ID, rather than being multiplied by a guess.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Bytes per record in the CAN (ISO 15765-4) framing.
RECORD_LENGTH = 9

#: Monitor IDs that advertise which other MIDs exist.
BITMAP_MIDS = (0x00, 0x20, 0x40, 0x60, 0x80, 0xA0, 0xC0, 0xE0)


@dataclass(frozen=True)
class Scaling:
    """How to turn a raw 16-bit test value into something readable."""

    unit: str
    multiplier: float
    signed: bool = False

    def convert(self, raw: int) -> float:
        value = _as_signed(raw) if self.signed else raw
        return value * self.multiplier


#: Unit-and-scaling IDs from SAE J1979. Deliberately partial: an entry is here
#: only when its meaning is unambiguous, because a wrong multiplier turns a
#: healthy reading into a scary one. Unknown IDs fall back to the raw count.
SCALINGS: dict[int, Scaling] = {
    0x01: Scaling("", 1.0),                 # raw count
    0x02: Scaling("", 0.1),
    0x03: Scaling("", 0.01),
    0x04: Scaling("", 0.001),
    0x05: Scaling("", 0.0000305),           # ratio
    0x06: Scaling("", 0.000305),            # ratio
    0x07: Scaling("rpm", 0.25),
    0x09: Scaling("km/h", 1.0),
    0x0A: Scaling("mV", 0.122),
    0x0B: Scaling("V", 0.001),
    0x0C: Scaling("V", 0.01),
    0x24: Scaling("counts", 1.0),
    0x30: Scaling("counts", 1.0),
    0x31: Scaling("km", 1.0),
}

#: Monitor IDs whose meaning is standardised. Cylinder misfire counters are the
#: $A2-$AB block; $A1 is the engine-wide summary.
MONITOR_NAMES: dict[int, str] = {
    0x01: "Oxygen sensor bank 1 sensor 1",
    0x02: "Oxygen sensor bank 1 sensor 2",
    0x03: "Oxygen sensor bank 1 sensor 3",
    0x04: "Oxygen sensor bank 1 sensor 4",
    0x05: "Oxygen sensor bank 2 sensor 1",
    0x06: "Oxygen sensor bank 2 sensor 2",
    0x07: "Oxygen sensor bank 2 sensor 3",
    0x08: "Oxygen sensor bank 2 sensor 4",
    0x21: "Catalyst monitor bank 1",
    0x22: "Catalyst monitor bank 2",
    0x31: "EGR monitor bank 1",
    0x32: "EGR monitor bank 2",
    0x39: "Exhaust gas sensor monitor",
    0x41: "Catalyst efficiency bank 1",
    0x42: "Catalyst efficiency bank 2",
    0x61: "Heated catalyst monitor bank 1",
    0x62: "Heated catalyst monitor bank 2",
    0x71: "Evaporative system leak (cap off)",
    0x72: "Evaporative system leak (0.090 in)",
    0x73: "Evaporative system leak (0.040 in)",
    0x74: "Evaporative system leak (0.020 in)",
    0x75: "Purge flow monitor",
    0x81: "Oxygen sensor heater bank 1 sensor 1",
    0x82: "Oxygen sensor heater bank 1 sensor 2",
    0x85: "Oxygen sensor heater bank 2 sensor 1",
    0x86: "Oxygen sensor heater bank 2 sensor 2",
    0xA1: "Misfire monitor, all cylinders",
    0xA2: "Misfire cylinder 1",
    0xA3: "Misfire cylinder 2",
    0xA4: "Misfire cylinder 3",
    0xA5: "Misfire cylinder 4",
    0xA6: "Misfire cylinder 5",
    0xA7: "Misfire cylinder 6",
    0xA8: "Misfire cylinder 7",
    0xA9: "Misfire cylinder 8",
    0xAA: "Misfire cylinder 9",
    0xAB: "Misfire cylinder 10",
}

#: $A2 is cylinder 1, so cylinder = MID - 0xA1.
MISFIRE_BASE = 0xA1
MISFIRE_LAST = 0xAB


def monitor_name(mid: int) -> str:
    if mid in MONITOR_NAMES:
        return MONITOR_NAMES[mid]
    if mid in BITMAP_MIDS:
        return f"Supported monitors {mid + 1:02X}-{mid + 0x20:02X}"
    return f"Monitor {mid:02X} (not in the standard table)"


def misfire_cylinder(mid: int) -> int | None:
    """The cylinder number a misfire MID refers to, if it is one."""
    if MISFIRE_BASE < mid <= MISFIRE_LAST:
        return mid - MISFIRE_BASE
    return None


@dataclass
class TestResult:
    """One monitor test: what was measured, and the limits it is judged against."""

    mid: int
    tid: int
    scaling_id: int
    raw_value: int
    raw_min: int
    raw_max: int

    @property
    def monitor(self) -> str:
        return monitor_name(self.mid)

    @property
    def scaling(self) -> Scaling | None:
        return SCALINGS.get(self.scaling_id)

    @property
    def known_scaling(self) -> bool:
        return self.scaling_id in SCALINGS

    @property
    def unit(self) -> str:
        scaling = self.scaling
        return scaling.unit if scaling else ""

    def _convert(self, raw: int) -> float:
        scaling = self.scaling
        return scaling.convert(raw) if scaling else float(raw)

    @property
    def value(self) -> float:
        return self._convert(self.raw_value)

    @property
    def minimum(self) -> float:
        return self._convert(self.raw_min)

    @property
    def maximum(self) -> float:
        return self._convert(self.raw_max)

    @property
    def has_min(self) -> bool:
        """A min limit of zero usually means "not applicable", not "must exceed 0"."""
        return self.raw_min != 0

    @property
    def has_max(self) -> bool:
        return self.raw_max not in (0, 0xFFFF)

    @property
    def passed(self) -> bool | None:
        """True/False against the ECU's own limits, or None when it sets none."""
        if not self.has_min and not self.has_max:
            return None
        if self.has_min and self.raw_value < self.raw_min:
            return False
        if self.has_max and self.raw_value > self.raw_max:
            return False
        return True

    @property
    def headroom(self) -> float | None:
        """How much of the allowed band is used, 0..1, or None without limits.

        Near 1.0 means the monitor is close to failing even though it passes -
        which is the whole point of reading mode 06 rather than waiting for a code.
        """
        if not self.has_max:
            return None
        low = self.raw_min if self.has_min else 0
        span = self.raw_max - low
        if span <= 0:
            return None
        return max(0.0, min(1.0, (self.raw_value - low) / span))

    def format_value(self) -> str:
        if not self.known_scaling:
            return f"{self.raw_value} raw (scaling ID {self.scaling_id:02X})"
        text = f"{self.value:.4g}"
        return f"{text} {self.unit}".strip()

    def format_limits(self) -> str:
        parts = []
        if self.has_min:
            parts.append(f"min {self._convert(self.raw_min):.4g}")
        if self.has_max:
            parts.append(f"max {self._convert(self.raw_max):.4g}")
        return ", ".join(parts) if parts else "no limits set"

    def to_dict(self) -> dict:
        return {
            "mid": f"{self.mid:02X}",
            "tid": f"{self.tid:02X}",
            "monitor": self.monitor,
            "cylinder": misfire_cylinder(self.mid),
            "value": self.value if self.known_scaling else self.raw_value,
            "raw_value": self.raw_value,
            "unit": self.unit,
            "known_scaling": self.known_scaling,
            "formatted": self.format_value(),
            "limits": self.format_limits(),
            "minimum": self.minimum if self.has_min else None,
            "maximum": self.maximum if self.has_max else None,
            "passed": self.passed,
            "headroom": self.headroom,
        }


def parse_records(payload: bytes) -> list[TestResult]:
    """Split a mode 06 payload into its nine-byte records."""
    results = []
    for start in range(0, len(payload) - RECORD_LENGTH + 1, RECORD_LENGTH):
        chunk = payload[start:start + RECORD_LENGTH]
        results.append(TestResult(
            mid=chunk[0],
            tid=chunk[1],
            scaling_id=chunk[2],
            raw_value=(chunk[3] << 8) | chunk[4],
            raw_min=(chunk[5] << 8) | chunk[6],
            raw_max=(chunk[7] << 8) | chunk[8],
        ))
    return results


def decode_supported(mid: int, payload: bytes) -> set[int]:
    """Decode a supported-monitors bitmap record into the MIDs it advertises."""
    for record in parse_records(payload):
        if record.mid != mid:
            continue
        # The bitmap occupies the four bytes that would otherwise be value+min.
        bits = (record.raw_value << 16) | record.raw_min
        return {
            mid + offset
            for offset in range(1, 33)
            if bits & (1 << (32 - offset))
        }
    return set()


def _as_signed(raw: int) -> int:
    return raw - 0x10000 if raw & 0x8000 else raw
