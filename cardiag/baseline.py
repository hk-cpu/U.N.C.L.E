"""Baselines: keep a record of how the car was, so you can tell what changed.

The single most useful thing you can do before touching anything - a part, a
tune, a service - is write down how the car currently behaves. Afterwards the
question "did that help?" stops being a matter of opinion.

A snapshot is a whole health report stored as JSON, so the comparison can grow
without a schema migration. Snapshots live in one SQLite file, by default under
the user's config directory, keyed by VIN so more than one car can share it.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    taken_at       REAL NOT NULL,
    label          TEXT NOT NULL,
    vin            TEXT,
    profile        TEXT,
    headline       TEXT,
    worst_severity TEXT,
    payload        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS snapshots_vin ON snapshots (vin, taken_at);
"""


def default_path() -> Path:
    """Where snapshots live unless the caller says otherwise."""
    override = os.environ.get("CARDIAG_HOME")
    if override:
        return Path(override) / "baselines.db"

    base = os.environ.get("XDG_DATA_HOME")
    root = Path(base) if base else Path.home() / ".local" / "share"
    return root / "cardiag" / "baselines.db"


@dataclass
class Snapshot:
    id: int
    taken_at: float
    label: str
    vin: str | None
    profile: str | None
    headline: str | None
    worst_severity: str | None
    payload: dict[str, Any] = field(default_factory=dict)

    @property
    def when(self) -> str:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(self.taken_at))

    def summary(self) -> str:
        return f"#{self.id}  {self.when}  {self.label}"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "taken_at": self.taken_at,
            "when": self.when,
            "label": self.label,
            "vin": self.vin,
            "profile": self.profile,
            "headline": self.headline,
            "worst_severity": self.worst_severity,
        }


@dataclass
class Change:
    """One difference between two snapshots."""

    category: str
    label: str
    before: str
    after: str
    #: "better", "worse" or "neutral" - which way the car moved.
    direction: str = "neutral"
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "category": self.category,
            "label": self.label,
            "before": self.before,
            "after": self.after,
            "direction": self.direction,
            "note": self.note,
        }


class BaselineStore:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else default_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(str(self.path))
        self._connection.row_factory = sqlite3.Row
        self._connection.executescript(SCHEMA)
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "BaselineStore":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- writing -----------------------------------------------------------
    def save(self, report: dict, label: str) -> Snapshot:
        vehicle = report.get("vehicle") or {}
        profile = report.get("profile") or {}
        taken_at = report.get("generated_at") or time.time()

        cursor = self._connection.execute(
            "INSERT INTO snapshots"
            " (taken_at, label, vin, profile, headline, worst_severity, payload)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                taken_at,
                label,
                vehicle.get("vin"),
                profile.get("name"),
                report.get("headline"),
                report.get("worst_severity"),
                json.dumps(report),
            ),
        )
        self._connection.commit()
        return self.get(cursor.lastrowid)     # type: ignore[arg-type]

    def delete(self, snapshot_id: int) -> bool:
        cursor = self._connection.execute(
            "DELETE FROM snapshots WHERE id = ?", (snapshot_id,)
        )
        self._connection.commit()
        return cursor.rowcount > 0

    # -- reading -----------------------------------------------------------
    def list(self, vin: str | None = None, limit: int = 50) -> list[Snapshot]:
        if vin:
            rows = self._connection.execute(
                "SELECT * FROM snapshots WHERE vin = ? ORDER BY taken_at DESC LIMIT ?",
                (vin, limit),
            )
        else:
            rows = self._connection.execute(
                "SELECT * FROM snapshots ORDER BY taken_at DESC LIMIT ?", (limit,)
            )
        return [_row_to_snapshot(row) for row in rows]

    def get(self, snapshot_id: int) -> Snapshot:
        row = self._connection.execute(
            "SELECT * FROM snapshots WHERE id = ?", (snapshot_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"no snapshot with id {snapshot_id}")
        return _row_to_snapshot(row)

    def latest(self, vin: str | None = None) -> Snapshot | None:
        found = self.list(vin=vin, limit=1)
        return found[0] if found else None

    def resolve(self, reference: str, vin: str | None = None) -> Snapshot:
        """Accept an id, ``latest``/``first``, or a label."""
        reference = reference.strip()
        if reference.isdigit():
            return self.get(int(reference))

        if reference.lower() in ("latest", "last"):
            snapshot = self.latest(vin=vin)
            if snapshot is None:
                raise KeyError("no snapshots saved yet")
            return snapshot

        if reference.lower() == "first":
            found = self.list(vin=vin, limit=1000)
            if not found:
                raise KeyError("no snapshots saved yet")
            return found[-1]

        matches = [s for s in self.list(vin=vin, limit=1000) if s.label == reference]
        if not matches:
            raise KeyError(f"no snapshot labelled {reference!r}")
        return matches[0]


def _row_to_snapshot(row: sqlite3.Row) -> Snapshot:
    return Snapshot(
        id=row["id"],
        taken_at=row["taken_at"],
        label=row["label"],
        vin=row["vin"],
        profile=row["profile"],
        headline=row["headline"],
        worst_severity=row["worst_severity"],
        payload=json.loads(row["payload"]),
    )


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

#: Live readings worth comparing, and whether a rise is good news.
_TRACKED_READINGS = {
    "LONG_FUEL_TRIM_1": ("Long term fuel trim, bank 1", "toward_zero", 3.0),
    "LONG_FUEL_TRIM_2": ("Long term fuel trim, bank 2", "toward_zero", 3.0),
    "SHORT_FUEL_TRIM_1": ("Short term fuel trim, bank 1", "toward_zero", 4.0),
    "SHORT_FUEL_TRIM_2": ("Short term fuel trim, bank 2", "toward_zero", 4.0),
    "CONTROL_MODULE_VOLTAGE": ("Charging voltage", "neutral", 0.4),
    "COOLANT_TEMP": ("Coolant temperature", "neutral", 6.0),
    "TIMING_ADVANCE": ("Timing advance", "neutral", 3.0),
    "MAF": ("Mass air flow", "neutral", 3.0),
    "ENGINE_LOAD": ("Engine load", "neutral", 6.0),
}


def compare(before: Snapshot, after: Snapshot) -> list[Change]:
    """Everything that moved between two snapshots, worst news first."""
    changes: list[Change] = []
    changes.extend(_compare_calibration(before.payload, after.payload))
    changes.extend(_compare_codes(before.payload, after.payload))
    changes.extend(_compare_misfires(before.payload, after.payload))
    changes.extend(_compare_monitors(before.payload, after.payload))
    changes.extend(_compare_readings(before.payload, after.payload))

    order = {"worse": 0, "neutral": 1, "better": 2}
    changes.sort(key=lambda item: order.get(item.direction, 1))
    return changes


def _compare_calibration(before: dict, after: dict) -> list[Change]:
    """A changed calibration means the module was reflashed.

    Two traps here. Mode 09 can simply fail, leaving an empty list - and an
    empty list against a populated one is missing data, not a reflash; saying
    "the module was reflashed" because a read failed sends someone hunting for
    a tune nobody applied. And a tune can rewrite the contents while keeping
    the same CALID string, which only the verification numbers reveal, so
    comparing IDs alone can miss the very change this exists to catch.
    """
    old_cal = before.get("calibration") or {}
    new_cal = after.get("calibration") or {}

    changes: list[Change] = []
    for key, label, what in (
        ("calibration_ids", "ECU calibration", "calibration ID"),
        ("verification_numbers", "Calibration verification", "verification number"),
    ):
        old = old_cal.get(key) or []
        new = new_cal.get(key) or []

        if not old or not new:
            # One side has nothing to compare against.
            if old != new:
                changes.append(Change(
                    category="calibration",
                    label=label,
                    before=", ".join(old) or "not reported",
                    after=", ".join(new) or "not reported",
                    direction="neutral",
                    note=(
                        f"The {what} was missing from one of these scans, so "
                        "whether it changed is unknown. Mode 09 not answering "
                        "is not the same as a reflash."
                    ),
                ))
            continue

        if old == new:
            continue

        changes.append(Change(
            category="calibration",
            label=label,
            before=", ".join(old),
            after=", ".join(new),
            direction="neutral",
            note=(
                f"The {what} changed, so the module was reflashed between "
                "these two snapshots. If that was not deliberate, find out who "
                "did it before trusting anything else here."
            ),
        ))
    return changes


def _compare_codes(before: dict, after: dict) -> list[Change]:
    old = {item["code"] for item in before.get("codes", [])}
    new = {item["code"] for item in after.get("codes", [])}

    changes = []
    appeared = sorted(new - old)
    cleared = sorted(old - new)

    if appeared:
        changes.append(Change(
            category="codes",
            label="New trouble codes",
            before="none of these",
            after=", ".join(appeared),
            direction="worse",
            note="These were not stored at the baseline.",
        ))
    if cleared:
        changes.append(Change(
            category="codes",
            label="Trouble codes gone",
            before=", ".join(cleared),
            after="no longer stored",
            direction="better",
            note=(
                "Gone because the fault was fixed, or because the codes were "
                "cleared - the readiness monitors tell you which."
            ),
        ))

    # A code can stay present and still get worse. Pending means the ECU saw
    # the fault once; stored means it confirmed it on a second drive cycle;
    # permanent means it will not clear until the monitor passes. Comparing
    # only which codes are present hides a fault being confirmed, which is
    # exactly the moment worth knowing about.
    old_status = {item["code"]: item.get("status")
                  for item in before.get("codes", [])}
    new_status = {item["code"]: item.get("status")
                  for item in after.get("codes", [])}

    rank = {"pending": 0, "stored": 1, "permanent": 2}
    for code in sorted(set(old_status) & set(new_status)):
        was, now = old_status[code], new_status[code]
        if was == now or was is None or now is None:
            continue
        changes.append(Change(
            category="codes",
            label=f"{code} changed status",
            before=str(was),
            after=str(now),
            direction=("worse" if rank.get(now, 1) > rank.get(was, 1)
                       else "better"),
            note=(
                "A pending code becoming stored means the ECU confirmed the "
                "fault on another drive cycle."
                if rank.get(now, 1) > rank.get(was, 1)
                else "The fault has not recurred since."
            ),
        ))
    return changes


def _compare_misfires(before: dict, after: dict) -> list[Change]:
    old = {int(k): v for k, v in (before.get("misfire_counts") or {}).items()}
    new = {int(k): v for k, v in (after.get("misfire_counts") or {}).items()}
    if not old or not new:
        return []

    changes = []
    for cylinder in sorted(set(old) & set(new)):
        delta = new[cylinder] - old[cylinder]
        if abs(delta) < 5:
            continue
        # No direction. These counters are typically per drive cycle (see
        # Session.misfire_counts), so they reset when the car is restarted.
        # A count that fell may mean the misfire was fixed, or may only mean
        # the counter rolled over since the snapshot - and calling that
        # "better" tells someone their repair worked when nothing was repaired.
        # A rise is just as ambiguous: it can be a worse misfire or simply more
        # running this cycle. The numbers are shown; the claim is not made.
        changes.append(Change(
            category="misfire",
            label=f"Misfire counts, cylinder {cylinder}",
            before=str(old[cylinder]),
            after=str(new[cylinder]),
            direction="neutral",
            note=(
                f"{'up' if delta > 0 else 'down'} {abs(delta)}. These counters "
                "usually reset each drive cycle, so this is not a trend - what "
                "is worth reading is how the cylinders compare with each other "
                "within each scan."
            ),
        ))
    return changes


def _compare_monitors(before: dict, after: dict) -> list[Change]:
    # Key by MID *and* TID: one monitor reports several distinct tests, and
    # keying on the MID alone keeps only the last of them, so a result that
    # moved to the edge of its limit under another TID never gets compared.
    def by_test(payload):
        return {(test["mid"], test.get("tid")): test
                for test in payload.get("monitor_tests", [])}

    old = by_test(before)
    new = by_test(after)

    changes = []
    for key in sorted(set(old) & set(new)):
        mid = key[0]
        first, second = old[key], new[key]
        if second.get("cylinder") is not None:
            continue        # misfires are reported in full by _compare_misfires
        if first.get("headroom") is None or second.get("headroom") is None:
            continue
        delta = second["headroom"] - first["headroom"]
        if abs(delta) < 0.05:
            continue
        changes.append(Change(
            category="monitor",
            label=second.get("monitor", f"Monitor {mid}"),
            before=f"{first['headroom'] * 100:.0f} % of limit",
            after=f"{second['headroom'] * 100:.0f} % of limit",
            direction="worse" if delta > 0 else "better",
            note=f"{second.get('formatted', '')} against {second.get('limits', '')}",
        ))
    return changes


def _compare_readings(before: dict, after: dict) -> list[Change]:
    old = before.get("readings") or {}
    new = after.get("readings") or {}

    changes = []
    for name, (label, polarity, threshold) in _TRACKED_READINGS.items():
        first, second = old.get(name), new.get(name)
        if not first or not second:
            continue

        old_value, new_value = first.get("value"), second.get("value")
        if not isinstance(old_value, (int, float)) or not isinstance(new_value, (int, float)):
            continue

        delta = new_value - old_value
        if abs(delta) < threshold:
            continue

        direction = "neutral"
        if polarity == "toward_zero":
            # For fuel trims, closer to zero is a healthier engine.
            direction = "better" if abs(new_value) < abs(old_value) else "worse"

        changes.append(Change(
            category="reading",
            label=label,
            before=first.get("formatted", str(old_value)),
            after=second.get("formatted", str(new_value)),
            direction=direction,
            note=f"{'+' if delta > 0 else ''}{delta:.4g}",
        ))
    return changes


def summarise(changes: list[Change]) -> str:
    if not changes:
        return "Nothing measurably changed between these two snapshots."

    worse = sum(1 for c in changes if c.direction == "worse")
    better = sum(1 for c in changes if c.direction == "better")
    other = len(changes) - worse - better

    parts = []
    if worse:
        parts.append(f"{worse} worse")
    if better:
        parts.append(f"{better} better")
    if other:
        parts.append(f"{other} changed")
    return ", ".join(parts) + "."
