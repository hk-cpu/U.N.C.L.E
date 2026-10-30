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

from .. import baseline as baseline_module, canbus, explain as explain_module
from .. import mode06, pids, report as report_module, tuning
from .. import vehicles, vin as vin_module
from ..elm327 import ObdError
from ..session import Reading, Session
from ..transport import TransportError, describe_url, link_kind, link_label

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

#: What the gauge cluster shows. Kept short on purpose: every extra channel is
#: another request per pass, and a tachometer that lags is worse than no
#: tachometer. The two dials come first so they refresh fastest.
GAUGE_CHANNELS = [
    "RPM", "SPEED",
    "COOLANT_TEMP", "OIL_TEMP", "CONTROL_MODULE_VOLTAGE", "INTAKE_TEMP",
]

#: The gauge view polls as hard as the adapter allows.
GAUGE_INTERVAL = 0.05

#: What the tuning view samples - the channels a tuner reads off a pull. MAP is
#: the load axis, timing is the headline value, the trims and intake temp are
#: what a pull is really being watched for, and the commanded equivalence ratio
#: is what the AFR analysis reads (so a browser-captured pull can show it, the
#: same as a CLI log does), where the car answers it.
TUNING_CHANNELS = [
    "RPM", "INTAKE_PRESSURE", "THROTTLE_POS", "TIMING_ADVANCE", "INTAKE_TEMP",
    "ENGINE_LOAD", "SHORT_FUEL_TRIM_1", "LONG_FUEL_TRIM_1",
    "SHORT_FUEL_TRIM_2", "LONG_FUEL_TRIM_2", "COMMANDED_EQUIV_RATIO",
]

#: Time-aligned sample rows kept for tuning analysis. Larger than the sparkline
#: history because a pull plus its approach, and room for a few pulls to compare,
#: needs more than the 15 seconds HISTORY holds at the default rate.
ROW_HISTORY = 600


class ServiceError(Exception):
    """Something the user needs to be told about, in plain words."""


class VehicleService:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._session: Session | None = None
        self._url: str | None = None
        #: The driver's own name for the port, which is how a Bluetooth link is
        #: told apart from a cable - both arrive as a serial device.
        self._port_description: str = ""
        self._profile: vehicles.VehicleProfile | None = None
        self._error: str | None = None

        self._sampler: threading.Thread | None = None
        self._sampling = threading.Event()
        self._channels: list[str] = []
        self._latest: dict[str, dict[str, Any]] = {}
        self._history: dict[str, deque] = {}
        #: Time-aligned rows ({timestamp, channel: value, ...}) for tuning, kept
        #: alongside the per-channel sparkline deques because the tuning engine
        #: reads each moment whole, not one channel's history at a time.
        self._rows: deque = deque(maxlen=ROW_HISTORY)
        self._sample_count = 0
        self._last_sample_at: float | None = None
        self._interval = SAMPLE_INTERVAL
        #: Where baselines are stored; tests point this at a temporary file.
        self.baseline_path = None

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
            self._port_description = _describe_port(url)
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
            self._port_description = ""
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
                # How the adapter is attached, so the interface can say whether
                # it is speaking over a cable, a pairing or the network.
                "link_kind": link_kind(self._url or "", self._port_description),
                "link_label": link_label(self._url or "", self._port_description),
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

    # -- following the ignition --------------------------------------------
    def car_state(self) -> dict:
        """Whether the car is running, merely awake, or asleep.

        A dashboard left on the windscreen polls this while parked, so it must
        stay cheap and must never raise just because the car went to sleep -
        that is the normal case it exists to report.
        """
        with self._lock:
            session = self._require()
            state = session.car_state()

        payload = state.to_dict()
        payload["url"] = self._url
        return payload

    def reconnect(self) -> dict:
        """Reopen the last connection, for when the adapter lost power.

        Turning the key can power-cycle the adapter on some sockets, which
        drops the serial port underneath us. The dashboard should recover from
        that on its own rather than showing an error until someone taps it.
        """
        url = self._url
        if not url:
            raise ServiceError("no previous connection to reopen")

        vehicle = self._profile.key if self._profile else None
        return self.connect(url, vehicle=vehicle)

    # -- the assistant -----------------------------------------------------
    def explain(self) -> dict:
        """A full scan, said in plain language.

        This is the slow, thorough one: it reads codes, monitors and mode 06,
        so it can talk about wear that has not set a code yet.
        """
        with self._lock:
            session = self._require()
            result = report_module.build(session, profile=self._profile)

        payload = explain_module.from_report(result).to_dict()
        payload["scanned_at"] = result.generated_at
        payload["link"] = self._link_summary()
        return payload

    def explain_live(self) -> dict:
        """What the car is doing right now, from the sampler's last readings.

        Cheap enough to call while the live view is running - it reads nothing
        from the adapter, only what the sampler already collected.
        """
        with self._lock:
            self._require()
            readings = {
                name: {"value": entry["value"]}
                for name, entry in self._latest.items()
            }
            profile = self._profile

        payload = explain_module.from_live(readings, profile).to_dict()
        payload["sampling"] = self._sampling.is_set()
        payload["link"] = self._link_summary()
        return payload

    def _link_summary(self) -> dict:
        url = self._url or ""
        return {
            "kind": link_kind(url, self._port_description),
            "label": link_label(url, self._port_description),
            "detail": describe_url(url),
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

    def monitor_tests(self) -> dict:
        """Mode 06 results, plus the per-cylinder misfire view."""
        with self._lock:
            session = self._require()
            tests = session.monitor_tests()
            # Keep the worst per cylinder, as report.build and
            # Session.misfire_counts do. Last-write-wins here would let the
            # Monitors tab show 3 where another test for the same cylinder
            # reported 168.
            counts: dict[int, int] = {}
            for test in tests:
                cylinder = mode06.misfire_cylinder(test.mid)
                if cylinder is None:
                    continue
                counts[cylinder] = max(counts.get(cylinder, 0), test.raw_value)
            if not counts:
                counts = session.misfire_counts()
            deactivated = (
                list(self._profile.engine.deactivated_cylinders)
                if self._profile else []
            )

        return {
            "tests": [test.to_dict() for test in tests],
            "misfire_counts": {str(k): v for k, v in sorted(counts.items())},
            "deactivated_cylinders": deactivated,
        }

    def start_gauges(self) -> dict:
        """Live sampling tuned for the gauge cluster."""
        with self._lock:
            session = self._require()
            supported = {entry.name for entry in session.live_pids()}
            wanted = [name for name in GAUGE_CHANNELS if name in supported]

        started = self.start_live(wanted or None, interval=GAUGE_INTERVAL)
        started["engine"] = self.engine_limits()
        return started

    def engine_limits(self) -> dict:
        """What the dials need to draw themselves for this particular engine."""
        engine = self._profile.engine if self._profile else None
        return {
            "redline_rpm": engine.redline_rpm if engine else None,
            "shift_rpm": engine.shift_rpm if engine else None,
            "coolant_warning": self._thresholds().coolant_warning,
            "coolant_critical": self._thresholds().coolant_critical,
            "charging_low": self._thresholds().charging_low,
            "charging_high": self._thresholds().charging_high,
        }

    def _thresholds(self):
        return self._profile.thresholds if self._profile else vehicles.Thresholds()

    # -- tuning ------------------------------------------------------------
    def start_tuning(self) -> dict:
        """Live sampling of the channels a pull is read from."""
        with self._lock:
            session = self._require()
            # Build from every advertised PID, not live_pids() (dashboard_order,
            # which drops priority-0 PIDs): COMMANDED_EQUIV_RATIO is priority 0,
            # and the AFR analysis needs it, so the dashboard set would filter it
            # back out even though it is in TUNING_CHANNELS.
            supported = {pids.BY_PID[p].name
                         for p in session.supported_pids if p in pids.BY_PID}
            wanted = [name for name in TUNING_CHANNELS if name in supported]
        return self.start_live(wanted or None)

    def _tuning_samples(self) -> list:
        with self._lock:
            rows = list(self._rows)
        return tuning.samples_from_rows(rows)

    def tuning_table(self, channel: str = "TIMING_ADVANCE") -> dict:
        """An RPM x MAP table of one channel, binned from the sampled rows.

        ``table`` is None - not an empty grid - when the rows do not carry that
        channel, so the UI can say "not logged" rather than "never went there".
        """
        samples = self._tuning_samples()
        table = tuning.build_table(samples, channel)
        return {
            "channel": channel,
            "samples_seen": len(samples),
            "table": table.to_dict() if table else None,
        }

    def tuning_pulls(self) -> dict:
        """Wide-open-throttle runs found in the sampled rows, each read off."""
        samples = self._tuning_samples()
        pulls = tuning.find_pulls(samples)
        return {
            "samples_seen": len(samples),
            "pulls": [
                {
                    "index": index,
                    "seconds": round(pull.seconds, 1),
                    "rpm_range": list(pull.rpm_range) if pull.rpm_range else None,
                    "observations": [o.to_dict()
                                     for o in tuning.analyse_pull(pull)],
                }
                for index, pull in enumerate(pulls)
            ],
        }

    def calibration(self) -> dict:
        with self._lock:
            return self._require().calibration()

    def procedures(self) -> dict:
        """Guided fix procedures, when a profile supplies any."""
        if self._profile is None:
            return {"profile": None, "issues": []}
        return {
            "profile": self._profile.name,
            "issues": [
                {
                    "key": issue.key,
                    "title": issue.title,
                    "detail": issue.detail,
                    "severity": issue.severity,
                    "procedure": [step.to_dict() for step in issue.procedure],
                }
                for issue in self._profile.known_issues if issue.procedure
            ],
        }

    # -- baselines ---------------------------------------------------------
    def _store(self) -> baseline_module.BaselineStore:
        return baseline_module.BaselineStore(self.baseline_path)

    def list_baselines(self) -> dict:
        with self._store() as store:
            return {"snapshots": [s.to_dict() for s in store.list()]}

    def save_baseline(self, label: str) -> dict:
        report = self.scan()
        with self._store() as store:
            snapshot = store.save(report, label)
        return snapshot.to_dict()

    def delete_baseline(self, snapshot_id: int) -> dict:
        with self._store() as store:
            if not store.delete(snapshot_id):
                raise ServiceError(f"no snapshot with id {snapshot_id}")
        return {"deleted": snapshot_id}

    def compare_baseline(self, snapshot_id: int) -> dict:
        """Compare a saved snapshot against the car as it is right now."""
        with self._store() as store:
            try:
                before = store.get(snapshot_id)
            except KeyError as exc:
                raise ServiceError(str(exc.args[0])) from exc

        report = self.scan()
        after = baseline_module.Snapshot(
            id=0, taken_at=report.get("generated_at", 0.0), label="now",
            vin=(report.get("vehicle") or {}).get("vin"),
            profile=(report.get("profile") or {}).get("name"),
            headline=report.get("headline"),
            worst_severity=report.get("worst_severity"),
            payload=report,
        )
        # One store holds snapshots for several cars, and the Baselines tab
        # lists all of them. Comparing another vehicle's snapshot against this
        # scan would present its codes and readings as changes on this car.
        # The CLI refuses this; so must the UI, or the guard only covers one
        # of the two ways in.
        if before.vin and after.vin and before.vin != after.vin:
            raise ServiceError(
                f"snapshot #{before.id} was taken on VIN {before.vin}, but the "
                f"car connected now is {after.vin}"
            )

        changes = baseline_module.compare(before, after)
        return {
            "before": before.to_dict(),
            "after": after.to_dict(),
            "summary": baseline_module.summarise(changes),
            "changes": [change.to_dict() for change in changes],
        }

    def vehicle(self) -> dict:
        with self._lock:
            vin = self._session.read_vin() if self._session else None
        decoded = vin_module.decode(vin).to_dict() if vin else None
        return {
            "profile": self._profile.to_dict() if self._profile else None,
            "vin": decoded,
            # The read-only wiring/bus map, so the Vehicle tab can show which
            # physical circuit a reading comes from. It is reference data, not
            # tied to the connected car, so it is always available.
            "canbus": canbus.as_dict(),
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
    def start_live(self, names: list[str] | None = None,
                   interval: float | None = None) -> dict:
        with self._lock:
            session = self._require()
            supported = {entry.name for entry in session.live_pids()}

            self._interval = SAMPLE_INTERVAL if interval is None else max(0.0, interval)

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
            self._rows = deque(maxlen=ROW_HISTORY)
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
            if elapsed < self._interval:
                time.sleep(self._interval - elapsed)

    def _sample_once(self) -> None:
        with self._lock:
            session = self._require()
            channels = list(self._channels)
            readings = session.read_many(channels)

            now = time.time()
            row: dict[str, float] = {"timestamp": now}
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
                    row[name] = numeric

            # One time-aligned row for the tuning engine, which needs RPM, load
            # and timing from the same instant rather than three separate deques.
            self._rows.append(row)
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
                    "severity": _channel_severity(
                        name, latest["value"] if latest else None,
                        self._profile.thresholds if self._profile else None),
                    # The wire this reading comes off, where cardiag knows it,
                    # so a value that looks wrong can be traced to a circuit.
                    "circuit": canbus.circuit_ref(name),
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


def _describe_port(url: str) -> str:
    """The driver's description of a serial port, if it is one we can see.

    Only the description tells a paired Bluetooth adapter from a cabled one on
    Windows, where both are a COM port.
    """
    if not url or "://" in url and not url.startswith("serial://"):
        return ""
    try:
        from ..transport.serial_link import list_ports

        target = url.replace("serial://", "")
        for device, description in list_ports():
            if device == target:
                return description
    except Exception:                                  # pyserial missing, etc.
        pass
    return ""


#: Live values worth colouring when they leave their normal band. Everything
#: else stays neutral - a meter that is always amber teaches nothing. The
#: temperature and charging lines come from the vehicle profile rather than
#: being repeated here, so a tile and the assistant cannot disagree about the
#: same number.
_WARN_RULES = {
    "RPM": lambda v, t: "warning" if v > 5500 else "good",
    "LONG_FUEL_TRIM_1": lambda v, t: "warning" if abs(v) > 15 else "good",
    "LONG_FUEL_TRIM_2": lambda v, t: "warning" if abs(v) > 15 else "good",
    "OIL_TEMP": lambda v, t: "warning" if v > 130 else "good",
    "COOLANT_TEMP": lambda v, t: (
        "critical" if v >= t.coolant_critical
        else "warning" if v >= t.coolant_warning
        else "good"
    ),
    "CONTROL_MODULE_VOLTAGE": lambda v, t: (
        "warning" if v < t.charging_low or v > t.charging_high else "good"
    ),
}


def _channel_severity(name: str, value: float | None,
                      thresholds: vehicles.Thresholds | None = None) -> str:
    if value is None:
        return "neutral"
    rule = _WARN_RULES.get(name)
    if rule is None:
        return "neutral"
    return rule(value, thresholds or vehicles.Thresholds())
