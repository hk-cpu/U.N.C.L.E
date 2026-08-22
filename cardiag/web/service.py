"""Thread-safe vehicle service behind the web UI.

There is exactly one adapter and one serial port, so every request has to take
turns. This module owns the :class:`~cardiag.session.Session` and serialises
access to it, and runs the live sampler on its own thread so the browser polling
rate is decoupled from how fast the adapter can actually answer.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any

from .. import pids, report as report_module, vehicles, vin as vin_module
from ..elm327 import ObdError
from ..session import Reading, Session
from ..transport import TransportError, describe_url

#: How many samples of each channel to keep for the sparklines.
HISTORY = 60

#: Seconds between sampler passes. The adapter is the real limit; this just
#: stops a fast link from spinning the CPU.
SAMPLE_INTERVAL = 0.25

#: Channels the live view starts with, when the car supports them.
DEFAULT_LIVE = [
    "RPM", "SPEED", "COOLANT_TEMP", "THROTTLE_POS",
    "ENGINE_LOAD", "CONTROL_MODULE_VOLTAGE",
    "LONG_FUEL_TRIM_1", "LONG_FUEL_TRIM_2",
]


class ServiceError(Exception):
    """Something the user needs to be told about, in plain words."""


class VehicleService:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._session: Session | None = None
        self._url: str | None = None
        self._profile: vehicles.VehicleProfile | None = None
        self._error: str | None = None

        self._sampler: threading.Thread | None = None
        self._sampling = threading.Event()
        self._channels: list[str] = []
        self._latest: dict[str, dict[str, Any]] = {}
        self._history: dict[str, deque] = {}
        self._sample_count = 0
        self._last_sample_at: float | None = None

    # -- connection --------------------------------------------------------
    def connect(self, url: str, timeout: float = 5.0,
                vehicle: str | None = None) -> dict:
        self.disconnect()

        profile = None
        if vehicle and vehicle.lower() not in ("", "auto"):
            if vehicle.lower() in ("none", "generic"):
                profile = None
            else:
                profile = vehicles.get(vehicle)
                if profile is None:
                    raise ServiceError(f"unknown vehicle profile: {vehicle}")

        with self._lock:
            try:
                session = Session(url, timeout=timeout)
                session.connect()
            except (TransportError, ObdError, ValueError) as exc:
                self._error = str(exc)
                raise ServiceError(str(exc)) from exc

            self._session = session
            self._url = url
            self._error = None

            if profile is None and (vehicle or "").lower() != "none":
                vin = session.read_vin()
                if vin:
                    profile = vehicles.match_vin(vin)
            self._profile = profile

        return self.status()

    def disconnect(self) -> dict:
        self.stop_live()
        with self._lock:
            if self._session is not None:
                self._session.close()
            self._session = None
            self._url = None
            self._profile = None
        return self.status()

    @property
    def connected(self) -> bool:
        return self._session is not None

    def _require(self) -> Session:
        if self._session is None:
            raise ServiceError("not connected to a vehicle yet")
        return self._session

    def status(self) -> dict:
        with self._lock:
            session = self._session
            if session is None:
                return {
                    "connected": False,
                    "error": self._error,
                    "profiles": sorted(vehicles.PROFILES),
                }

            adapter = session.adapter
            return {
                "connected": True,
                "url": self._url,
                "link": describe_url(self._url or ""),
                "adapter": adapter.identifier if adapter else None,
                "protocol": adapter.protocol if adapter else None,
                "battery_voltage": adapter.voltage if adapter else None,
                "profile": self._profile.name if self._profile else None,
                "profile_key": self._profile.key if self._profile else None,
                "supported_pids": len(session.supported_pids),
                "sampling": self._sampling.is_set(),
                "error": None,
                "profiles": sorted(vehicles.PROFILES),
            }

    # -- one-shot operations ----------------------------------------------
    def scan(self) -> dict:
        with self._lock:
            session = self._require()
            result = report_module.build(session, profile=self._profile)
            return result.to_dict()

    def codes(self) -> dict:
        with self._lock:
            session = self._require()
            found = session.read_dtcs()
            return {
                "codes": [item.to_dict() for item in found],
                "profile_notes": self._code_notes(found),
            }

    def _code_notes(self, found: list) -> dict[str, dict]:
        """Model-specific context for each code, when a profile is active."""
        if self._profile is None:
            return {}

        notes = {}
        for item in found:
            located = report_module.locate_code(item.code, self._profile)
            note = self._profile.code_notes.get(item.code)
            issues = [issue.title for issue in self._profile.issues_for([item.code])]
            if located or note or issues:
                notes[item.code] = {
                    "located": located,
                    "note": note,
                    "issues": issues,
                }
        return notes

    def clear_codes(self) -> dict:
        with self._lock:
            session = self._require()
            session.clear_dtcs()
        return {"cleared": True}

    def freeze(self) -> dict:
        with self._lock:
            session = self._require()
            frame = session.freeze_frame()

        out: dict[str, Any] = {}
        for key, value in frame.items():
            if isinstance(value, Reading):
                out[key] = {
                    "description": value.pid.description,
                    "formatted": value.pid.format(value.value),
                }
            else:
                out[key] = {"description": key.replace("_", " "), "formatted": str(value)}
        return {"frame": out}

    def monitors(self) -> dict:
        with self._lock:
            session = self._require()
            status = session.monitor_status()
        return {"status": report_module.status_dict(status)}

    def vehicle(self) -> dict:
        with self._lock:
            vin = self._session.read_vin() if self._session else None
        decoded = vin_module.decode(vin).to_dict() if vin else None
        return {
            "profile": self._profile.to_dict() if self._profile else None,
            "vin": decoded,
        }

    def available_channels(self) -> list[dict]:
        with self._lock:
            session = self._require()
            return [
                {
                    "name": entry.name,
                    "description": entry.description,
                    "unit": entry.unit,
                }
                for entry in session.live_pids()
            ]

    # -- live sampling -----------------------------------------------------
    def start_live(self, names: list[str] | None = None) -> dict:
        with self._lock:
            session = self._require()
            supported = {entry.name for entry in session.live_pids()}

            if names:
                chosen = [name for name in names if name in supported]
                if not chosen:
                    raise ServiceError(
                        "none of the requested channels are supported by this vehicle"
                    )
            else:
                chosen = [name for name in DEFAULT_LIVE if name in supported]
                if not chosen:
                    chosen = sorted(supported)[:8]

            self._channels = chosen
            self._latest = {}
            self._history = {name: deque(maxlen=HISTORY) for name in chosen}
            self._sample_count = 0

        if not self._sampling.is_set():
            self._sampling.set()
            self._sampler = threading.Thread(
                target=self._sample_loop, name="cardiag-sampler", daemon=True
            )
            self._sampler.start()

        return {"channels": self._channels}

    def stop_live(self) -> dict:
        self._sampling.clear()
        sampler, self._sampler = self._sampler, None
        if sampler is not None and sampler is not threading.current_thread():
            sampler.join(timeout=2.0)
        return {"sampling": False}

    def _sample_loop(self) -> None:
        while self._sampling.is_set():
            started = time.monotonic()
            try:
                self._sample_once()
            except ServiceError:
                break
            except (TransportError, ObdError):
                # A dropped frame is normal on a marginal link; keep going and
                # let the reading go stale rather than killing the view.
                pass

            elapsed = time.monotonic() - started
            if elapsed < SAMPLE_INTERVAL:
                time.sleep(SAMPLE_INTERVAL - elapsed)

    def _sample_once(self) -> None:
        with self._lock:
            session = self._require()
            channels = list(self._channels)
            readings = session.read_many(channels)

            now = time.time()
            for name in channels:
                reading = readings.get(name)
                if reading is None:
                    continue
                numeric = _numeric(reading.value)
                self._latest[name] = {
                    "name": name,
                    "description": reading.pid.description,
                    "unit": reading.pid.unit,
                    "formatted": reading.pid.format(reading.value),
                    "value": numeric,
                    "at": now,
                }
                if numeric is not None:
                    self._history[name].append(numeric)

            self._sample_count += 1
            self._last_sample_at = now

    def live_snapshot(self) -> dict:
        with self._lock:
            channels = []
            for name in self._channels:
                entry = pids.get(name)
                latest = self._latest.get(name)
                history = list(self._history.get(name, ()))
                low, high = _display_range(name, entry)
                channels.append({
                    "name": name,
                    "description": entry.description if entry else name,
                    "unit": entry.unit if entry else "",
                    "formatted": latest["formatted"] if latest else None,
                    "value": latest["value"] if latest else None,
                    "history": history,
                    "min": min(history) if history else None,
                    "max": max(history) if history else None,
                    "range_low": low,
                    "range_high": high,
                    "severity": _channel_severity(name, latest["value"] if latest else None),
                })
            return {
                "sampling": self._sampling.is_set(),
                "samples": self._sample_count,
                "at": self._last_sample_at,
                "channels": channels,
            }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _numeric(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        for item in value.values():
            if isinstance(item, (int, float)) and not isinstance(item, bool):
                return float(item)
    return None


def _display_range(name: str, entry) -> tuple[float | None, float | None]:
    """Meter bounds - the range a driver cares about, not the protocol's."""
    from ..dashboard import DISPLAY_RANGE

    override = DISPLAY_RANGE.get(name)
    if override:
        return override
    if entry is None:
        return None, None
    return entry.minimum, entry.maximum


#: Live values worth colouring when they leave their normal band. Everything
#: else stays neutral - a meter that is always amber teaches nothing.
_WARN_RULES = {
    "COOLANT_TEMP": lambda v: "critical" if v > 113 else ("warning" if v > 105 else "good"),
    "RPM": lambda v: "warning" if v > 5500 else "good",
    "CONTROL_MODULE_VOLTAGE": lambda v: "warning" if v < 13.0 or v > 15.0 else "good",
    "LONG_FUEL_TRIM_1": lambda v: "warning" if abs(v) > 15 else "good",
    "LONG_FUEL_TRIM_2": lambda v: "warning" if abs(v) > 15 else "good",
    "OIL_TEMP": lambda v: "warning" if v > 130 else "good",
}


def _channel_severity(name: str, value: float | None) -> str:
    if value is None:
        return "neutral"
    rule = _WARN_RULES.get(name)
    if rule is None:
        return "neutral"
    return rule(value)
