"""Generate examples/charger-pull.csv — a representative wide-open pull.

Deterministic and hardware-free, so `cardiag pull examples/charger-pull.csv`
always has a real log to analyse. The shape mirrors the bench simulator's pull
ramp for a 5.7 HEMI: a short cruise, a full-throttle pull with the revs climbing
from ~1,800 to the limiter, then a lift. Timing is eased back as the revs climb,
the manifold sits near atmospheric at wide-open throttle, and the intake air
heat-soaks across the run — the things a tuner reads off a pull.

Columns match what `cardiag live ... --log` writes: an epoch timestamp plus one
column per channel. Re-run this to regenerate the file.

    python tools/make_example_log.py
"""

from __future__ import annotations

import csv
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "examples" / "charger-pull.csv"

CHANNELS = [
    "RPM", "INTAKE_PRESSURE", "THROTTLE_POS", "TIMING_ADVANCE",
    "INTAKE_TEMP", "LONG_FUEL_TRIM_1", "LONG_FUEL_TRIM_2",
]

STEP = 0.1          # seconds between samples
START = 1_730_000_000.0


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * max(0.0, min(1.0, t))


def _rows() -> list[dict[str, float]]:
    rows: list[dict[str, float]] = []
    t = 0.0

    def add(rpm, map_kpa, throttle, timing, iat, ltft1, ltft2):
        rows.append({
            "timestamp": round(START + t, 1),
            "RPM": round(rpm), "INTAKE_PRESSURE": round(map_kpa),
            "THROTTLE_POS": round(throttle, 1), "TIMING_ADVANCE": round(timing, 1),
            "INTAKE_TEMP": round(iat), "LONG_FUEL_TRIM_1": round(ltft1, 1),
            "LONG_FUEL_TRIM_2": round(ltft2, 1),
        })

    # 1.5 s of light cruise: part throttle, timing well advanced, low load.
    for _ in range(15):
        add(1600, 34, 18, 30, 42, 1.5, 0.8)
        t += STEP

    # ~4 s wide-open pull: throttle pinned, revs climb to the limiter, MAP goes
    # to near-atmospheric, timing eased back under load, intake air heat-soaking.
    pull = 40
    for i in range(pull):
        f = i / (pull - 1)
        add(_lerp(1850, 5600, f), _lerp(92, 99, f), 88,
            _lerp(27.5, 18.0, f), _lerp(44, 60, f),
            _lerp(2.0, 4.5, f), _lerp(0.5, 3.5, f))
        t += STEP

    # Lift and coast: throttle shut, revs falling.
    for i in range(12):
        f = i / 11
        add(_lerp(5200, 1500, f), _lerp(40, 28, f), 6,
            _lerp(22, 28, f), 60, 1.0, 0.4)
        t += STEP

    return rows


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    rows = _rows()
    with OUT.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["timestamp", *CHANNELS])
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {len(rows)} rows to {OUT}")


if __name__ == "__main__":
    main()
