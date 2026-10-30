"""Reading a log the way a tuner reads one.

A scan tool answers "what is the engine doing now". A tuner asks a different
question: "across this whole pull, what was it doing *at each operating point*" -
at 3,000 rpm and 80 kPa, what was the timing, and were the trims still sane.
That question is answered with a table, not a line graph, and binning a log into
RPM x load cells is the central move.

Everything here is analysis of logs cardiag already records. Nothing in this
module writes to the car, and nothing in it infers a calibration table: it
reports what the engine *did*, which is a measurement, not a map.

Two rules carried over from the rest of the project:

* A cell holding one sample is not a result. Cells below a minimum count report
  None rather than a confident average drawn from noise.
* A channel the log does not contain produces no analysis, rather than a
  reassuring one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

#: The channels a tuner records to read a pull back: load and timing to see
#: what the engine did, trims and the commanded ratio to see how it fuelled,
#: intake and coolant temperature because both move the tune. These are PID
#: names from :mod:`cardiag.pids`; a car that does not answer one simply leaves
#: that column blank. Kept here so the CLI logger and the app agree on the set.
LOG_CHANNELS = (
    "RPM", "SPEED", "ENGINE_LOAD", "MAF", "INTAKE_PRESSURE", "THROTTLE_POS",
    "TIMING_ADVANCE", "INTAKE_TEMP", "COOLANT_TEMP",
    "SHORT_FUEL_TRIM_1", "LONG_FUEL_TRIM_1",
    "SHORT_FUEL_TRIM_2", "LONG_FUEL_TRIM_2",
    "COMMANDED_EQUIV_RATIO", "O2_B1S1", "O2_B2S1",
)

#: Below this many samples a cell is treated as unvisited. One sample in a cell
#: is an artefact of where the log happened to be polled, not a measurement of
#: what the engine does there.
MIN_CELL_SAMPLES = 3

#: RPM bin edges for a 5.7 HEMI: tight where it idles and cruises, wider up top
#: where you pass through quickly.
RPM_EDGES = (0, 800, 1200, 1600, 2000, 2400, 2800, 3200, 3600, 4000,
             4400, 4800, 5200, 5600, 6000)

#: Manifold pressure in kPa. On a naturally aspirated engine this runs from deep
#: vacuum at over-run up to roughly atmospheric at wide-open throttle, so it
#: doubles as the load axis a tuner actually thinks in.
MAP_EDGES = (0, 20, 30, 40, 50, 60, 70, 80, 90, 100)

#: Calculated load, for logs without a MAP channel.
LOAD_EDGES = (0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100)

#: Throttle above this, with the engine pulling, counts as wide open.
WOT_THROTTLE = 70.0

#: A pull shorter than this is a blip, not a run worth analysing.
MIN_PULL_SECONDS = 1.5

#: And one that did not gain this many revs is not a pull at all. Throttle
#: alone cannot tell a run from a stationary rev held at the limiter.
MIN_PULL_RPM_RISE = 400.0

#: Safe wide-open-throttle air/fuel window for the 5.7 NGC3, from the vehicle's
#: own tuning reference (PE commanded AFR). These are targets to read a log
#: against - cardiag reports whether a pull sits in the window; it does not
#: write anything to the car.
WOT_AFR_TARGET_LOW = 12.2
WOT_AFR_TARGET_HIGH = 12.8
#: Leaner than this at wide-open throttle is detonation territory on a naturally
#: aspirated engine, the more so in heat.
WOT_AFR_LEAN_LIMIT = 13.0
#: Lambda 1.0 for pump gasoline, to turn an equivalence ratio into an AFR.
STOICH_GASOLINE = 14.64

#: Column names an external wideband might be logged under (AFR directly, then
#: lambda). The car's own narrowband O2 is deliberately not used for this: it
#: saturates rich at WOT and says nothing in the region a pull lives in.
WIDEBAND_AFR_CHANNELS = ("WIDEBAND_AFR", "WBAFR", "AFR")
WIDEBAND_LAMBDA_CHANNELS = ("WIDEBAND_LAMBDA", "WBLAMBDA", "LAMBDA")


# ---------------------------------------------------------------------------
# Samples
# ---------------------------------------------------------------------------

@dataclass
class Sample:
    """One moment of a log: a timestamp and whatever channels were read."""

    at: float
    values: dict[str, float] = field(default_factory=dict)

    def get(self, channel: str) -> float | None:
        value = self.values.get(channel)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return float(value)


#: Column names that carry the timestamp, best first. cardiag's own CsvLogger
#: writes "timestamp"; defaulting to "time" meant every row of its own logs was
#: skipped, leaving the analysis with no samples and no complaint.
TIME_KEYS = ("timestamp", "time", "elapsed_s")


def samples_from_rows(rows: Iterable[dict[str, Any]],
                      time_key: str | None = None) -> list[Sample]:
    """Build samples from CSV-ish rows, skipping anything unparseable.

    cardiag's own CSV log writes an epoch timestamp and one column per channel,
    so this reads its logs directly; a row from anywhere else works too as long
    as it has a timestamp and numeric columns. Pass ``time_key`` to name the
    column explicitly, otherwise the first of :data:`TIME_KEYS` present is used.
    """
    rows = list(rows)
    keys = (time_key,) if time_key else TIME_KEYS
    chosen = next((key for key in keys
                   if any(key in row for row in rows)), keys[0])

    out: list[Sample] = []
    for row in rows:
        try:
            at = float(row[chosen])
        except (KeyError, TypeError, ValueError):
            continue

        values: dict[str, float] = {}
        for key, raw in row.items():
            if key in TIME_KEYS:
                continue
            try:
                values[key] = float(raw)
            except (TypeError, ValueError):
                continue        # a blank or a string column is simply not data
        out.append(Sample(at, values))

    out.sort(key=lambda sample: sample.at)
    return out


# ---------------------------------------------------------------------------
# The table view
# ---------------------------------------------------------------------------

def _bin(edges: Sequence[float], value: float) -> int | None:
    """Index of the bin `value` falls in, or None if it is off the end."""
    if value < edges[0] or value > edges[-1]:
        return None
    for index in range(len(edges) - 1):
        # Upper edge is inclusive only in the last bin, so a value never lands
        # in two cells.
        if value < edges[index + 1] or (index == len(edges) - 2
                                        and value <= edges[-1]):
            if value >= edges[index]:
                return index
    return None


@dataclass
class Cell:
    count: int = 0
    total: float = 0.0
    minimum: float | None = None
    maximum: float | None = None

    def add(self, value: float) -> None:
        self.count += 1
        self.total += value
        self.minimum = value if self.minimum is None else min(self.minimum, value)
        self.maximum = value if self.maximum is None else max(self.maximum, value)

    @property
    def mean(self) -> float | None:
        if self.count < MIN_CELL_SAMPLES:
            return None
        return self.total / self.count

    def to_dict(self) -> dict:
        return {"count": self.count, "mean": self.mean,
                "min": self.minimum, "max": self.maximum}


@dataclass
class Table:
    """A channel averaged over RPM x load cells - the tuner's view of a log."""

    channel: str
    x_channel: str
    y_channel: str
    x_edges: tuple[float, ...]
    y_edges: tuple[float, ...]
    cells: dict[tuple[int, int], Cell] = field(default_factory=dict)
    #: Samples that had the axes but not the channel, or fell off the axes.
    skipped: int = 0

    def add(self, x: float, y: float, value: float) -> bool:
        ix = _bin(self.x_edges, x)
        iy = _bin(self.y_edges, y)
        if ix is None or iy is None:
            self.skipped += 1
            return False
        self.cells.setdefault((ix, iy), Cell()).add(value)
        return True

    def mean_at(self, ix: int, iy: int) -> float | None:
        cell = self.cells.get((ix, iy))
        return cell.mean if cell else None

    @property
    def visited(self) -> int:
        """Cells with enough samples to mean anything."""
        return sum(1 for cell in self.cells.values()
                   if cell.count >= MIN_CELL_SAMPLES)

    @property
    def samples(self) -> int:
        return sum(cell.count for cell in self.cells.values())

    def label_x(self, index: int) -> str:
        return f"{self.x_edges[index]:.0f}-{self.x_edges[index + 1]:.0f}"

    def label_y(self, index: int) -> str:
        return f"{self.y_edges[index]:.0f}-{self.y_edges[index + 1]:.0f}"

    def to_dict(self) -> dict:
        return {
            "channel": self.channel,
            "x_channel": self.x_channel,
            "y_channel": self.y_channel,
            "x_labels": [self.label_x(i) for i in range(len(self.x_edges) - 1)],
            "y_labels": [self.label_y(i) for i in range(len(self.y_edges) - 1)],
            "cells": [
                {"x": ix, "y": iy, **cell.to_dict()}
                for (ix, iy), cell in sorted(self.cells.items())
            ],
            "visited": self.visited,
            "samples": self.samples,
            "skipped": self.skipped,
            "min_cell_samples": MIN_CELL_SAMPLES,
        }


def load_channel(samples: Sequence[Sample]) -> str:
    """The load axis a pull table should bin against.

    Manifold pressure is the truer load signal, so it wins where the log has it;
    but a log that carries only calculated ENGINE_LOAD should still get a table
    rather than none. Falls back to the MAP default when neither is present (so
    build_table returns None honestly, rather than guessing an axis).
    """
    if any(s.get("INTAKE_PRESSURE") is not None for s in samples):
        return "INTAKE_PRESSURE"
    if any(s.get("ENGINE_LOAD") is not None for s in samples):
        return "ENGINE_LOAD"
    return "INTAKE_PRESSURE"


def build_table(samples: Sequence[Sample], channel: str,
                x_channel: str = "RPM",
                y_channel: str = "INTAKE_PRESSURE",
                x_edges: Sequence[float] = RPM_EDGES,
                y_edges: Sequence[float] | None = None) -> Table | None:
    """Bin one channel across two axes. None when the log cannot support it.

    Returning None rather than an empty table matters: an empty grid on screen
    looks like "the engine never went there", when the truth is "this log never
    recorded that channel".
    """
    if y_edges is None:
        y_edges = MAP_EDGES if y_channel == "INTAKE_PRESSURE" else LOAD_EDGES

    table = Table(channel, x_channel, y_channel,
                  tuple(x_edges), tuple(y_edges))

    usable = 0
    for sample in samples:
        x = sample.get(x_channel)
        y = sample.get(y_channel)
        value = sample.get(channel)
        if x is None or y is None or value is None:
            continue
        usable += 1
        table.add(x, y, value)

    return table if usable else None


# ---------------------------------------------------------------------------
# Finding the pulls
# ---------------------------------------------------------------------------

@dataclass
class Pull:
    """One wide-open-throttle run picked out of a longer log."""

    samples: list[Sample]

    @property
    def seconds(self) -> float:
        return self.samples[-1].at - self.samples[0].at if self.samples else 0.0

    @property
    def rpm_range(self) -> tuple[float, float] | None:
        values = [s.get("RPM") for s in self.samples]
        present = [v for v in values if v is not None]
        return (min(present), max(present)) if present else None

    def series(self, channel: str) -> list[tuple[float, float]]:
        """(seconds-from-start, value) for one channel, missing rows dropped."""
        if not self.samples:
            return []
        start = self.samples[0].at
        return [(s.at - start, s.get(channel)) for s in self.samples
                if s.get(channel) is not None]  # type: ignore[misc]


#: The throttle channel's name in the PID table. Spelled out here because an
#: analysis that silently finds no pulls - which is what a wrong channel name
#: produces - is indistinguishable from a log with no pulls in it.
THROTTLE_CHANNEL = "THROTTLE_POS"


def find_pulls(samples: Sequence[Sample],
               throttle_channel: str = THROTTLE_CHANNEL,
               min_seconds: float = MIN_PULL_SECONDS) -> list[Pull]:
    """Pick the wide-open-throttle runs out of a log.

    A pull is a stretch where the throttle is open and the engine is gaining
    revs. Requiring both matters: throttle alone catches a stationary rev, and
    rising rpm alone catches every upshift-free crawl away from a light.
    """
    runs: list[Pull] = []
    current: list[Sample] = []

    def close(gathered: list[Sample]) -> None:
        if not gathered:
            return
        pull = Pull(gathered)
        if pull.seconds < min_seconds:
            return
        # The net rise is what separates a run from a stationary rev. Testing
        # it sample by sample instead would also split a real pull in two at
        # the dip through an upshift.
        first = next((s.get("RPM") for s in gathered if s.get("RPM") is not None), None)
        last = next((s.get("RPM") for s in reversed(gathered)
                     if s.get("RPM") is not None), None)
        if first is None or last is None:
            return
        if last - first < MIN_PULL_RPM_RISE:
            return
        runs.append(pull)

    for sample in samples:
        throttle = sample.get(throttle_channel)
        if throttle is not None and throttle >= WOT_THROTTLE \
                and sample.get("RPM") is not None:
            current.append(sample)
            continue
        close(current)
        current = []

    close(current)
    return runs


# ---------------------------------------------------------------------------
# What a tuner reads off a pull
# ---------------------------------------------------------------------------

@dataclass
class Observation:
    """One thing the pull shows, said plainly."""

    topic: str
    detail: str
    severity: str = "info"
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"topic": self.topic, "detail": self.detail,
                "severity": self.severity, "evidence": list(self.evidence)}


def analyse_pull(pull: Pull) -> list[Observation]:
    """Read a pull the way a tuner would, from the channels that are present."""
    found: list[Observation] = []

    rpm_range = pull.rpm_range
    if rpm_range:
        found.append(Observation(
            "Pull",
            f"{pull.seconds:.1f} s from {rpm_range[0]:,.0f} to "
            f"{rpm_range[1]:,.0f} rpm.",
            "info",
            [f"{len(pull.samples)} samples"],
        ))

    found.extend(_observe_afr(pull))
    found.extend(_observe_timing(pull))
    found.extend(_observe_intake_temp(pull))
    found.extend(_observe_trims(pull))
    return found


def _mean_series(pull: Pull, channel: str) -> float | None:
    series = pull.series(channel)
    if len(series) < 3:
        return None
    values = [value for _, value in series]
    return sum(values) / len(values)


def _wideband_afr(pull: Pull) -> float | None:
    """A logged wideband's mean AFR across the pull, if one is present."""
    for name in WIDEBAND_AFR_CHANNELS:
        mean = _mean_series(pull, name)
        if mean is not None:
            return mean
    for name in WIDEBAND_LAMBDA_CHANNELS:
        mean = _mean_series(pull, name)
        if mean is not None:
            return mean * STOICH_GASOLINE
    return None


def _observe_afr(pull: Pull) -> list[Observation]:
    """Air/fuel at wide-open throttle, read against the safe window.

    A wideband, if it was logged, is the measurement. Otherwise the ECU's
    commanded ratio (COMMANDED_EQUIV_RATIO) is reported as exactly what it is -
    a target the car is aiming for, not a measurement of what it achieved - so a
    reading is never dressed up as more than the log supports.
    """
    measured = _wideband_afr(pull)
    if measured is not None:
        afr, kind = measured, "measured"
    else:
        lam = _mean_series(pull, "COMMANDED_EQUIV_RATIO")
        if lam is None:
            return []
        afr, kind = lam * STOICH_GASOLINE, "commanded"

    window = f"{WOT_AFR_TARGET_LOW:.1f}-{WOT_AFR_TARGET_HIGH:.1f}:1"
    evidence = [f"{kind} {afr:.1f}:1 at WOT", f"safe window {window}"]
    unverified = ("" if kind == "measured" else " That is what the ECU is "
                  "targeting, not what it burned - a wideband is the only way to "
                  "confirm it.")

    if afr >= WOT_AFR_LEAN_LIMIT:
        return [Observation(
            "Air/fuel", f"{kind.capitalize()} AFR at wide-open throttle is "
            f"{afr:.1f}:1, leaner than the {window} safe window. Lean at WOT is "
            "detonation territory, the more so in the heat; the safe direction is "
            "more fuel." + unverified, "serious", evidence)]
    if afr > WOT_AFR_TARGET_HIGH:
        return [Observation(
            "Air/fuel", f"{kind.capitalize()} AFR is {afr:.1f}:1, a little lean of "
            f"the {window} window. A touch more fuel is the safe way to move it."
            + unverified, "advisory", evidence)]
    if afr >= WOT_AFR_TARGET_LOW:
        return [Observation(
            "Air/fuel", f"{kind.capitalize()} AFR is {afr:.1f}:1, inside the "
            f"{window} safe window." + unverified, "good", evidence)]
    return [Observation(
        "Air/fuel", f"{kind.capitalize()} AFR is {afr:.1f}:1, richer than the "
        f"{window} window - safe, but leaving a little power on the table."
        + unverified, "info", evidence)]


def _observe_timing(pull: Pull) -> list[Observation]:
    """Advance falling as revs rise - what a knock-retard event would look like.

    This car exposes no knock-retard PID, so the only visible evidence is spark
    advance coming down while the engine is still pulling. But a WOT spark table
    normally tapers advance as RPM and load climb, so a fall on its own cannot be
    told apart from knock correction. It is reported as an observation to
    correlate, never as a confirmed knock event.
    """
    series = pull.series("TIMING_ADVANCE")
    if len(series) < 6:
        return []

    values = [value for _, value in series]
    peak = max(values)
    peak_at = values.index(peak)
    after = values[peak_at:]
    if len(after) < 3:
        return []

    drop = peak - min(after)
    evidence = [f"peak {peak:.1f} deg", f"low {min(after):.1f} deg"]

    if drop >= 6.0:
        return [Observation(
            "Timing", f"Advance fell {drop:.1f} degrees after peaking during the "
            "pull. The timing trace alone can't say why: a WOT spark table "
            "tapers advance as RPM and load rise, and knock retard would look "
            "the same here. Correlate it before reading knock into it - a smooth "
            "fall that tracks RPM and load looks like the table, a sharp or "
            "load-triggered drop looks more like retard, and if it is retard, "
            "fuel grade and intake temperature are where to look.",
            "moderate", evidence)]
    if drop >= 3.0:
        return [Observation(
            "Timing", f"Advance eased back {drop:.1f} degrees late in the pull. "
            "Most likely the spark table coming down with RPM and load, but it "
            "is also the direction knock retard moves - the trace can't tell the "
            "two apart.",
            "advisory", evidence)]
    return [Observation(
        "Timing", f"Advance held within {drop:.1f} degrees of its peak through "
        "the pull.", "good", evidence)]


def _observe_intake_temp(pull: Pull) -> list[Observation]:
    series = pull.series("INTAKE_TEMP")
    if len(series) < 4:
        return []

    first = series[0][1]
    last = series[-1][1]
    rise = last - first
    evidence = [f"{first:.0f} to {last:.0f} degC"]

    if rise >= 15.0:
        return [Observation(
            "Intake air", f"Intake temperature climbed {rise:.0f} degC across the "
            "pull. Heat soak like that costs timing on the next run - let it "
            "cool before comparing back-to-back pulls.",
            "advisory", evidence)]
    return [Observation(
        "Intake air", f"Intake temperature moved {rise:+.0f} degC across the pull.",
        "info", evidence)]


def _observe_trims(pull: Pull) -> list[Observation]:
    """At wide-open throttle the ECU runs open loop, so trims should be still.

    A bank still correcting hard under full throttle is the interesting case -
    it means the correction was carried in from closed loop and the mixture was
    already wrong before the throttle opened.
    """
    found: list[Observation] = []
    for bank, channel in ((1, "LONG_FUEL_TRIM_1"), (2, "LONG_FUEL_TRIM_2")):
        series = pull.series(channel)
        if len(series) < 4:
            continue

        values = [value for _, value in series]
        mean = sum(values) / len(values)
        evidence = [f"bank {bank} mean {mean:+.1f}%"]

        if abs(mean) >= 15.0:
            found.append(Observation(
                "Fuelling", f"Bank {bank} was still {mean:+.0f}% at wide-open "
                "throttle. That correction was carried in from closed loop - "
                "the mixture is off before the throttle even opens.",
                "serious", evidence))
        elif abs(mean) >= 8.0:
            found.append(Observation(
                "Fuelling", f"Bank {bank} averaged {mean:+.0f}% through the pull.",
                "advisory", evidence))
        else:
            found.append(Observation(
                "Fuelling", f"Bank {bank} held at {mean:+.0f}% - nothing being "
                "corrected hard.", "good", evidence))
    return found


def compare_pulls(before: Pull, after: Pull) -> list[Observation]:
    """What changed between two runs - the before and after of a change."""
    found: list[Observation] = []

    for channel, label, unit in (("TIMING_ADVANCE", "Peak timing", "deg"),
                                 ("INTAKE_TEMP", "Starting intake air", "degC")):
        first = before.series(channel)
        second = after.series(channel)
        if len(first) < 3 or len(second) < 3:
            continue

        if channel == "TIMING_ADVANCE":
            a = max(value for _, value in first)
            b = max(value for _, value in second)
        else:
            a, b = first[0][1], second[0][1]

        change = b - a
        found.append(Observation(
            label, f"{a:.1f} -> {b:.1f} {unit} ({change:+.1f}).",
            "info", [f"before {a:.1f}", f"after {b:.1f}"]))

    # A hotter second run explains a worse second run, so say so rather than
    # letting the comparison imply the change itself made things worse.
    intake = [item for item in found if item.topic.startswith("Starting intake")]
    if intake and "+" in intake[0].detail:
        found.append(Observation(
            "Caution", "The second pull started hotter than the first. Compare "
            "runs from the same intake temperature, or heat soak will look "
            "like a result.", "advisory", []))
    return found
