"""The high-level API: a connected vehicle you can ask questions of.

Everything above this layer (the CLI, the dashboard, the logger) works in terms
of :class:`Session` rather than raw modes and PIDs.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Iterable

from . import dtc, mode06, pids
from .elm327 import ELM327, AdapterInfo, NoDataError, ObdError
from .pids import PID, MonitorStatus
from .transport import TransportError, open_transport

#: The chained bitmap PIDs that tell us which other PIDs exist.
_BITMAP_CHAIN = (0x00, 0x20, 0x40, 0x60, 0x80, 0xA0, 0xC0)


@dataclass
class Reading:
    """One decoded value, with the timestamp it was sampled at."""

    pid: PID
    value: Any
    timestamp: float = field(default_factory=time.time)

    @property
    def name(self) -> str:
        return self.pid.name

    def __str__(self) -> str:
        return f"{self.pid.description}: {self.pid.format(self.value)}"


@dataclass
class VehicleInfo:
    vin: str | None
    ecu_name: str | None
    fuel_type: str | None
    protocol: str
    adapter: str
    battery_voltage: float | None


class Session:
    """A connection to one vehicle."""

    def __init__(self, url: str = "sim://", timeout: float = 5.0,
                 protocol: str | None = None) -> None:
        self.url = url
        self.timeout = timeout
        #: ELM327 protocol number to force, or None to auto-detect first.
        self.protocol = protocol
        self.transport = open_transport(url, timeout=timeout)
        self.elm = ELM327(self.transport, timeout=timeout)
        self.adapter: AdapterInfo | None = None
        self._supported: set[int] | None = None

    # -- lifecycle ---------------------------------------------------------
    def connect(self) -> AdapterInfo:
        self.adapter = self.elm.connect(protocol=self.protocol)
        return self.adapter

    def close(self) -> None:
        self.elm.close()

    def __enter__(self) -> "Session":
        self.connect()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- capability discovery ---------------------------------------------
    @property
    def supported_pids(self) -> set[int]:
        """Every Mode 01 PID this vehicle answers, discovered once and cached."""
        if self._supported is None:
            self._supported = self._discover_supported()
        return self._supported

    def _discover_supported(self) -> set[int]:
        found: set[int] = set()
        for base in _BITMAP_CHAIN:
            try:
                data = self.elm.request(0x01, base)
            except (ObdError, TransportError):
                break
            if len(data) < 4:
                break
            found |= pids.BY_PID[base].decode(data) if base in pids.BY_PID \
                else _decode_bitmap(base, data)
            found.add(base)
            # Only keep walking the chain while the ECU says the next block exists.
            next_base = base + 0x20
            if next_base not in found:
                break
        return found

    def supports(self, identifier: int | str) -> bool:
        entry = pids.get(identifier)
        return entry is not None and entry.pid in self.supported_pids

    # -- live data ---------------------------------------------------------
    def read(self, identifier: int | str) -> Reading | None:
        """Read one PID. Returns ``None`` when the vehicle has no answer."""
        entry = pids.get(identifier)
        if entry is None:
            raise KeyError(f"unknown PID: {identifier!r}")

        try:
            data = self.elm.request(0x01, entry.pid)
        except NoDataError:
            return None
        except ObdError:
            return None

        if len(data) < entry.num_bytes:
            return None
        return Reading(pid=entry, value=entry.decode(data[: entry.num_bytes]))

    def read_many(self, identifiers: Iterable[int | str]) -> dict[str, Reading]:
        readings: dict[str, Reading] = {}
        for identifier in identifiers:
            reading = self.read(identifier)
            if reading is not None:
                readings[reading.name] = reading
        return readings

    def live_pids(self) -> list[PID]:
        """The supported PIDs worth putting on a dashboard, best first."""
        return pids.dashboard_order(self.supported_pids)

    # -- status and codes --------------------------------------------------
    def monitor_status(self) -> MonitorStatus | None:
        reading = self.read(0x01)
        return reading.value if reading else None

    def read_dtcs(self, include_pending: bool = True,
                  include_permanent: bool = True) -> list[dtc.Dtc]:
        """Stored (mode 03), pending (mode 07) and permanent (mode 0A) codes."""
        results: list[dtc.Dtc] = []
        seen: set[tuple[str, str]] = set()

        requests = [(0x03, "stored")]
        if include_pending:
            requests.append((0x07, "pending"))
        if include_permanent:
            requests.append((0x0A, "permanent"))

        for mode, status in requests:
            for code in self._codes_for_mode(mode):
                if (code, status) in seen:
                    continue
                seen.add((code, status))
                results.append(dtc.describe(code, status=status))

        results.sort(key=dtc.sort_key)
        return results

    def _codes_for_mode(self, mode: int) -> list[str]:
        try:
            frames = self.elm.request_all(mode)
        except (NoDataError, ObdError, TransportError):
            return []

        codes: list[str] = []
        for payload in frames:
            if not payload:
                continue
            # The first byte is the DTC count; the rest are two-byte codes.
            # Some ECUs omit the count, in which case the payload length is even.
            body = payload[1:] if len(payload) % 2 else payload
            codes.extend(dtc.decode_bytes(body))
        return codes

    def clear_dtcs(self) -> None:
        """Erase codes, freeze frames and readiness monitors (mode 04)."""
        self.elm.clear_codes()

    def freeze_frame(self) -> dict[str, Any]:
        """Mode 02: the snapshot the ECU took when a fault was confirmed."""
        frame: dict[str, Any] = {}

        trigger = self._freeze_value(0x02)
        if trigger and len(trigger) >= 2:
            frame["trigger_code"] = dtc.decode_pair(trigger[0], trigger[1])

        for entry in pids.dashboard_order(self.supported_pids):
            data = self._freeze_value(entry.pid)
            if data and len(data) >= entry.num_bytes:
                frame[entry.name] = Reading(
                    pid=entry, value=entry.decode(data[: entry.num_bytes])
                )
        return frame

    def _freeze_value(self, pid: int) -> bytes | None:
        try:
            # Mode 02 takes a frame number; 0 is the only one most cars store.
            frames = self.elm.request_all(0x02, pid)
        except (NoDataError, ObdError, TransportError):
            return None
        if not frames:
            return None
        payload = frames[0]
        # The reply echoes the frame number after the PID; drop it.
        return payload[1:] if payload else None

    # -- mode 06: on-board monitor test results ---------------------------
    def supported_monitors(self) -> set[int]:
        """Every mode 06 monitor ID this ECU advertises."""
        found: set[int] = set()
        for base in mode06.BITMAP_MIDS:
            try:
                payloads = self.elm.request_records(0x06, base)
            except (ObdError, TransportError):
                break
            if not payloads:
                break

            advertised: set[int] = set()
            for payload in payloads:
                advertised |= mode06.decode_supported(base, payload)
            if not advertised:
                break

            found |= advertised
            if base + 0x20 not in advertised:
                break
        return found

    def monitor_tests(self, mids: Iterable[int] | None = None) -> list[mode06.TestResult]:
        """Read mode 06 test results, defaulting to every monitor on offer.

        Bitmap MIDs are skipped: they advertise other monitors rather than
        measuring anything.
        """
        if mids is None:
            mids = sorted(self.supported_monitors())

        results: list[mode06.TestResult] = []
        for mid in mids:
            if mid in mode06.BITMAP_MIDS:
                continue
            try:
                payloads = self.elm.request_records(0x06, mid)
            except (NoDataError, ObdError, TransportError):
                continue
            for payload in payloads:
                # Some ECUs answer a request with records for other monitors
                # too; keep only what was asked for.
                results.extend(
                    record for record in mode06.parse_records(payload)
                    if record.mid == mid
                )
        return results

    def misfire_counts(self) -> dict[int, int]:
        """Per-cylinder misfire counters, keyed by cylinder number.

        Where a cylinder reports several tests, the largest count is kept -
        the counters are typically "this drive cycle" and a longer-run average,
        and the worst of them is what matters.
        """
        counts: dict[int, int] = {}
        first = mode06.MISFIRE_BASE + 1
        for record in self.monitor_tests(range(first, mode06.MISFIRE_LAST + 1)):
            cylinder = mode06.misfire_cylinder(record.mid)
            if cylinder is None:
                continue
            counts[cylinder] = max(counts.get(cylinder, 0), record.raw_value)
        return counts

    # -- identity ----------------------------------------------------------
    def calibration(self) -> dict[str, list[str]]:
        """Mode 09 calibration ID and verification number.

        Together these identify the software actually running in the ECU. Record
        them before changing anything: if they differ later, the module was
        reflashed, which is the only reliable way to tell a tune was applied or
        lost without taking anyone's word for it.
        """
        return {
            "calibration_ids": self._read_mode_09_list(0x04),
            "verification_numbers": self._read_mode_09_hex(0x06),
        }

    def _read_mode_09_list(self, pid: int) -> list[str]:
        """Mode 09 items are fixed 4-byte-aligned ASCII blocks, count-prefixed."""
        try:
            data = self.elm.request(0x09, pid)
        except (NoDataError, ObdError, TransportError):
            return []
        if len(data) < 2:
            return []

        count = data[0]
        body = data[1:]
        if not 1 <= count <= 8 or len(body) % count:
            # The count byte looked wrong; treat the whole payload as one item.
            text = _printable(body)
            return [text] if text else []

        width = len(body) // count
        items = [_printable(body[i * width:(i + 1) * width]) for i in range(count)]
        return [item for item in items if item]

    def _read_mode_09_hex(self, pid: int) -> list[str]:
        """CVNs are binary, so they are reported as hex rather than text."""
        try:
            data = self.elm.request(0x09, pid)
        except (NoDataError, ObdError, TransportError):
            return []
        if len(data) < 2:
            return []

        count = data[0]
        body = data[1:]
        if not 1 <= count <= 8 or len(body) % count:
            return [body.hex().upper()] if body else []

        width = len(body) // count
        return [body[i * width:(i + 1) * width].hex().upper() for i in range(count)]

    def vehicle_info(self) -> VehicleInfo:
        fuel = self.read(0x51)
        return VehicleInfo(
            vin=self.read_vin(),
            ecu_name=self._read_mode_09_string(0x0A),
            fuel_type=fuel.value if fuel else None,
            protocol=self.adapter.protocol if self.adapter else "unknown",
            adapter=self.adapter.identifier if self.adapter else "unknown",
            battery_voltage=self.adapter.voltage if self.adapter else None,
        )

    def read_vin(self) -> str | None:
        """Mode 09 PID 02. Cars built before roughly 2005 will not answer."""
        text = self._read_mode_09_string(0x02)
        if text and len(text) >= 17:
            return text[-17:]
        return text

    def _read_mode_09_string(self, pid: int) -> str | None:
        try:
            data = self.elm.request(0x09, pid)
        except (NoDataError, ObdError, TransportError):
            return None
        if not data:
            return None
        # A leading count byte says how many data items follow; it is not text.
        if data[0] in (0x01, 0x02, 0x03, 0x04) and len(data) > 1:
            data = data[1:]
        text = data.decode("ascii", "ignore")
        return "".join(ch for ch in text if ch.isprintable()).strip() or None


def _printable(data: bytes) -> str:
    """ASCII from a mode 09 block, with the NUL padding removed."""
    text = data.decode("ascii", "ignore")
    return "".join(ch for ch in text if ch.isprintable()).strip()


def _decode_bitmap(base: int, data: bytes) -> set[int]:
    bits = int.from_bytes(data[:4], "big")
    return {base + offset for offset in range(1, 33) if bits & (1 << (32 - offset))}
