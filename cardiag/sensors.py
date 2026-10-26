"""Sensor transfer functions for the 5.7 HEMI, from the vehicle reference.

The ECU already hands cardiag engineering values over OBD - coolant in degrees,
MAP in kPa - so these are not for decoding a PID. They are for the bench: you
unplug a sensor and measure it, and this tells you what temperature that
resistance means, or what resistance to expect at a temperature, so a suspect
sensor can be judged against the factory curve instead of a guess. The injector
maths answers the other recurring tuning question - how much the stock injectors
will actually flow, and how much power that supports.

Every number is from the vehicle's technical reference; nothing is extrapolated
beyond the tabulated range without saying so.
"""

from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# ECT / IAT: a two-wire NTC thermistor. Both sensors share this curve.
# ---------------------------------------------------------------------------
#
# Resistance falls monotonically as temperature rises, so resistance <-> temp is
# a clean lookup. (The dual-stage *voltage* is not monotonic - it steps when the
# PCM switches its internal pull-up around 110 F - so voltage is deliberately not
# offered as the lookup key here.)

#: (temperature degC, resistance ohms), coldest first. From the factory table.
NTC_CURVE: tuple[tuple[float, float], ...] = (
    (-40.0, 100_700.0),
    (-20.0, 28_680.0),
    (-10.0, 16_000.0),
    (0.0, 9_420.0),
    (10.0, 5_670.0),
    (20.0, 3_520.0),
    (30.0, 2_238.0),
    (40.0, 1_480.0),
    (50.0, 987.0),
    (60.0, 684.0),
    (70.0, 484.0),
    (80.0, 332.0),
    (90.0, 241.0),
    (100.0, 188.0),
    (110.0, 132.0),
    (120.0, 100.0),
)


@dataclass
class Lookup:
    value: float
    #: True when the input fell outside the tabulated range, so the answer is an
    #: extrapolation rather than a reading off the curve.
    extrapolated: bool = False


def _interpolate(points: list[tuple[float, float]], x: float) -> Lookup:
    """Linear interpolation over (x, y) points sorted ascending in x."""
    if x <= points[0][0]:
        x0, y0 = points[0]
        x1, y1 = points[1]
        extrapolated = x < points[0][0]
    elif x >= points[-1][0]:
        x0, y0 = points[-2]
        x1, y1 = points[-1]
        extrapolated = x > points[-1][0]
    else:
        extrapolated = False
        for (x0, y0), (x1, y1) in zip(points, points[1:]):
            if x0 <= x <= x1:
                break
    y = y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return Lookup(y, extrapolated)


def temp_from_resistance(ohms: float) -> Lookup:
    """Temperature (degC) for a measured NTC resistance."""
    # The curve descends in resistance, so sort by resistance ascending to
    # interpolate temperature against it.
    points = sorted(((r, t) for t, r in NTC_CURVE))
    return _interpolate(points, ohms)


def resistance_from_temp(celsius: float) -> Lookup:
    """Expected NTC resistance (ohms) at a temperature."""
    return _interpolate([(t, r) for t, r in NTC_CURVE], celsius)


def c_to_f(celsius: float) -> float:
    return celsius * 9.0 / 5.0 + 32.0


# ---------------------------------------------------------------------------
# Fuel injectors: flow scales with the square root of the pressure ratio.
# ---------------------------------------------------------------------------

#: The stock 2006 5.7 injector: 27.5 lb/hr rated at the 43.5 psi test pressure.
STOCK_INJECTOR_LBHR = 27.5
INJECTOR_TEST_PSI = 43.5
#: The Gen III returnless rail runs a constant 58 psi.
RAIL_PSI = 58.0


def injector_flow(base_lbhr: float = STOCK_INJECTOR_LBHR,
                  base_psi: float = INJECTOR_TEST_PSI,
                  psi: float = RAIL_PSI) -> float:
    """Actual flow at a rail pressure, from the rated flow at its test pressure.

    Flow rises with the square root of the pressure ratio, so the stock 27.5
    lb/hr at 43.5 psi becomes about 31.75 lb/hr at the 58 psi rail.
    """
    return base_lbhr * (psi / base_psi) ** 0.5


def max_bhp(flow_lbhr: float, cylinders: int = 8, duty: float = 0.80,
            bsfc: float = 0.50) -> float:
    """Crank BHP a set of injectors supports, at a given duty cycle and BSFC.

    HP = flow * cylinders * duty / BSFC - the standard naturally aspirated
    sizing rule. At 80 % duty and 0.50 BSFC the stock injectors support ~406 BHP.
    """
    return flow_lbhr * cylinders * duty / bsfc
