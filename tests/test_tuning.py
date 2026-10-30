"""Tuning analysis tests.

The thing being guarded is the same as everywhere else in this project: the
analysis must not present something as measured when it is not. A cell holding
one sample is not an average, and a log without a channel is not a log showing
that channel at zero.
"""

import pytest

from cardiag import tuning
from cardiag.tuning import (MIN_CELL_SAMPLES, Pull, Sample, analyse_pull,
                            build_table, compare_pulls, find_pulls,
                            samples_from_rows)


def sample(at, **values):
    return Sample(at, dict(values))


# ---------------------------------------------------------------------------
# Channel names
# ---------------------------------------------------------------------------

def test_every_channel_this_module_names_exists_in_the_pid_table():
    """The bug this catches cost a silent, total failure.

    `find_pulls` defaulted to "THROTTLE_POS"; the table calls it
    "THROTTLE_POS". Synthetic tests used the same wrong name, so they agreed
    with each other and with nothing real - and a wrong channel name produces
    zero pulls, which looks exactly like a log containing no pulls. Pin the
    names against the actual table rather than against the test's own fixtures.
    """
    from cardiag import pids

    named = {
        tuning.THROTTLE_CHANNEL, "RPM", "INTAKE_PRESSURE", "TIMING_ADVANCE",
        "INTAKE_TEMP", "LONG_FUEL_TRIM_1", "LONG_FUEL_TRIM_2",
    }
    for channel in sorted(named):
        assert pids.get(channel) is not None, f"{channel} is not a real PID name"


def ramp(count, *, start=0.0, step=0.1, **channels):
    """A run of samples where each channel moves linearly from a to b."""
    out = []
    for i in range(count):
        fraction = i / max(count - 1, 1)
        values = {}
        for name, (low, high) in channels.items():
            values[name] = low + (high - low) * fraction
        out.append(Sample(start + i * step, values))
    return out


# ---------------------------------------------------------------------------
# Binning
# ---------------------------------------------------------------------------

def test_a_value_lands_in_exactly_one_cell():
    edges = (0, 10, 20, 30)
    seen = [tuning._bin(edges, value) for value in (0, 9.9, 10, 19.9, 20, 29.9)]
    assert seen == [0, 0, 1, 1, 2, 2]


def test_the_top_edge_is_included_rather_than_dropped():
    edges = (0, 10, 20)
    assert tuning._bin(edges, 20) == 1
    assert tuning._bin(edges, 20.1) is None
    assert tuning._bin(edges, -0.1) is None


# ---------------------------------------------------------------------------
# The table
# ---------------------------------------------------------------------------

def test_a_cell_with_too_few_samples_reports_nothing():
    """One sample in a cell is where the log was polled, not a measurement."""
    table = tuning.Table("TIMING_ADVANCE", "RPM", "INTAKE_PRESSURE",
                         tuning.RPM_EDGES, tuning.MAP_EDGES)

    table.add(2100, 55, 20.0)
    assert table.mean_at(tuning._bin(tuning.RPM_EDGES, 2100),
                         tuning._bin(tuning.MAP_EDGES, 55)) is None

    for _ in range(MIN_CELL_SAMPLES):
        table.add(2100, 55, 20.0)
    assert table.mean_at(tuning._bin(tuning.RPM_EDGES, 2100),
                         tuning._bin(tuning.MAP_EDGES, 55)) == 20.0


def test_the_table_averages_what_lands_in_a_cell():
    table = tuning.Table("TIMING_ADVANCE", "RPM", "INTAKE_PRESSURE",
                         tuning.RPM_EDGES, tuning.MAP_EDGES)
    for value in (10.0, 20.0, 30.0):
        table.add(2100, 55, value)

    ix = tuning._bin(tuning.RPM_EDGES, 2100)
    iy = tuning._bin(tuning.MAP_EDGES, 55)
    assert table.mean_at(ix, iy) == 20.0
    assert table.cells[(ix, iy)].minimum == 10.0
    assert table.cells[(ix, iy)].maximum == 30.0


def test_samples_off_the_axes_are_counted_not_clamped():
    table = tuning.Table("TIMING_ADVANCE", "RPM", "INTAKE_PRESSURE",
                         tuning.RPM_EDGES, tuning.MAP_EDGES)
    assert table.add(9000, 55, 20.0) is False     # past the rpm axis
    assert table.skipped == 1
    assert table.samples == 0


def test_a_log_without_the_channel_produces_no_table():
    """An empty grid reads as 'the engine never went there'. It is not that."""
    samples = ramp(20, RPM=(1000, 4000), INTAKE_PRESSURE=(30, 90))
    assert build_table(samples, "TIMING_ADVANCE") is None


def test_a_log_with_the_channel_produces_a_table():
    samples = ramp(60, RPM=(1000, 4000), INTAKE_PRESSURE=(30, 90),
                   TIMING_ADVANCE=(25, 18))
    table = build_table(samples, "TIMING_ADVANCE")

    assert table is not None
    assert table.samples == 60
    assert table.visited >= 1
    assert table.to_dict()["channel"] == "TIMING_ADVANCE"


def test_the_table_serialises():
    import json
    samples = ramp(60, RPM=(1000, 4000), INTAKE_PRESSURE=(30, 90),
                   TIMING_ADVANCE=(25, 18))
    json.dumps(build_table(samples, "TIMING_ADVANCE").to_dict())


# ---------------------------------------------------------------------------
# Reading rows
# ---------------------------------------------------------------------------

def test_rows_without_a_usable_timestamp_are_dropped():
    rows = [{"time": "1.0", "RPM": "800"},
            {"time": "", "RPM": "900"},
            {"RPM": "1000"}]
    assert [s.at for s in samples_from_rows(rows)] == [1.0]


def test_blank_columns_are_not_read_as_zero():
    rows = [{"time": "1.0", "RPM": "800", "OIL_TEMP": ""}]
    (only,) = samples_from_rows(rows)
    assert only.get("RPM") == 800.0
    assert only.get("OIL_TEMP") is None


def test_rows_are_sorted_by_time():
    rows = [{"time": "3.0"}, {"time": "1.0"}, {"time": "2.0"}]
    assert [s.at for s in samples_from_rows(rows)] == [1.0, 2.0, 3.0]


# ---------------------------------------------------------------------------
# Finding pulls
# ---------------------------------------------------------------------------

def test_a_wide_open_run_is_found():
    log = (ramp(10, start=0, RPM=(800, 900), THROTTLE_POS=(5, 8))
           + ramp(40, start=1.0, RPM=(1500, 5200), THROTTLE_POS=(90, 95))
           + ramp(10, start=5.0, RPM=(3000, 1200), THROTTLE_POS=(4, 3)))

    pulls = find_pulls(log)
    assert len(pulls) == 1
    assert pulls[0].rpm_range[1] > 5000


def test_a_stationary_rev_is_not_a_pull():
    """Throttle alone is not enough - the engine has to be going somewhere."""
    log = ramp(40, RPM=(5000, 1000), THROTTLE_POS=(90, 90))
    assert find_pulls(log) == []


def test_a_brief_stab_of_throttle_is_not_a_pull():
    log = (ramp(4, start=0, step=0.1, RPM=(2000, 2400),
                THROTTLE_POS=(95, 95))
           + ramp(10, start=2.0, RPM=(2000, 1000), THROTTLE_POS=(5, 5)))
    assert find_pulls(log) == []


def test_two_pulls_in_one_log_are_separated():
    log = (ramp(40, start=0, RPM=(1500, 5000), THROTTLE_POS=(92, 95))
           + ramp(20, start=10.0, RPM=(2000, 1500), THROTTLE_POS=(5, 5))
           + ramp(40, start=20.0, RPM=(1500, 5000), THROTTLE_POS=(92, 95)))
    assert len(find_pulls(log)) == 2


# ---------------------------------------------------------------------------
# What the pull shows
# ---------------------------------------------------------------------------

def topics(observations):
    return {item.topic: item for item in observations}


def test_timing_held_steady_reads_as_good():
    pull = Pull(ramp(30, RPM=(1500, 5000), TIMING_ADVANCE=(24, 23)))
    timing = topics(analyse_pull(pull))["Timing"]
    assert timing.severity == "good"


def test_timing_pulled_back_hard_is_flagged():
    # Advance peaks early then falls away while the engine is still pulling.
    pull = Pull(ramp(15, start=0, RPM=(1500, 3000), TIMING_ADVANCE=(26, 26))
                + ramp(15, start=1.5, RPM=(3000, 5000), TIMING_ADVANCE=(26, 14)))
    timing = topics(analyse_pull(pull))["Timing"]

    # A big fall is flagged for attention, but the timing trace alone can't tell
    # a normal spark-table taper from knock retard, so it is not asserted as
    # knock ('serious') - it names both causes and says to correlate.
    assert timing.severity == "moderate"
    assert "12.0 degrees" in timing.detail
    detail = timing.detail.lower()
    assert "table" in detail and "retard" in detail
    assert "usually means" not in detail


def test_a_small_timing_drop_is_only_advisory():
    pull = Pull(ramp(15, start=0, RPM=(1500, 3000), TIMING_ADVANCE=(26, 26))
                + ramp(15, start=1.5, RPM=(3000, 5000), TIMING_ADVANCE=(26, 22)))
    assert topics(analyse_pull(pull))["Timing"].severity == "advisory"


def test_heat_soak_across_a_pull_is_called_out():
    pull = Pull(ramp(30, RPM=(1500, 5000), INTAKE_TEMP=(38, 60)))
    intake = topics(analyse_pull(pull))["Intake air"]

    assert intake.severity == "advisory"
    assert "heat soak" in intake.detail.lower()


def test_a_bank_still_correcting_at_wot_is_serious():
    pull = Pull(ramp(30, RPM=(1500, 5000), LONG_FUEL_TRIM_2=(22, 24)))
    fuelling = [item for item in analyse_pull(pull) if item.topic == "Fuelling"]

    assert len(fuelling) == 1
    assert fuelling[0].severity == "serious"
    assert "bank 2" in fuelling[0].detail.lower()


def test_settled_trims_read_as_good():
    pull = Pull(ramp(30, RPM=(1500, 5000),
                     LONG_FUEL_TRIM_1=(2, 3), LONG_FUEL_TRIM_2=(1, 2)))
    fuelling = [item for item in analyse_pull(pull) if item.topic == "Fuelling"]
    assert len(fuelling) == 2
    assert all(item.severity == "good" for item in fuelling)


def test_channels_the_log_lacks_produce_no_observation():
    """Silence, not a reassuring verdict, for something never recorded."""
    pull = Pull(ramp(30, RPM=(1500, 5000)))
    found = topics(analyse_pull(pull))

    assert "Pull" in found
    assert "Timing" not in found
    assert "Fuelling" not in found
    assert "Intake air" not in found


def test_observations_serialise():
    import json
    pull = Pull(ramp(30, RPM=(1500, 5000), TIMING_ADVANCE=(26, 20)))
    json.dumps([item.to_dict() for item in analyse_pull(pull)])


# ---------------------------------------------------------------------------
# Comparing two runs
# ---------------------------------------------------------------------------

def test_comparing_pulls_reports_the_change_in_peak_timing():
    before = Pull(ramp(20, RPM=(1500, 5000), TIMING_ADVANCE=(20, 18),
                       INTAKE_TEMP=(40, 45)))
    after = Pull(ramp(20, RPM=(1500, 5000), TIMING_ADVANCE=(26, 24),
                      INTAKE_TEMP=(40, 45)))

    peak = topics(compare_pulls(before, after))["Peak timing"]
    assert "+6.0" in peak.detail


def test_a_hotter_second_run_is_called_out_as_a_confound():
    """Otherwise heat soak reads as the change having made things worse."""
    before = Pull(ramp(20, RPM=(1500, 5000), TIMING_ADVANCE=(26, 24),
                       INTAKE_TEMP=(38, 44)))
    after = Pull(ramp(20, RPM=(1500, 5000), TIMING_ADVANCE=(22, 20),
                      INTAKE_TEMP=(58, 64)))

    found = topics(compare_pulls(before, after))
    assert "Caution" in found
    assert "heat soak" in found["Caution"].detail.lower()


def test_nothing_is_compared_when_the_channel_is_missing_from_one_run():
    before = Pull(ramp(20, RPM=(1500, 5000), TIMING_ADVANCE=(26, 24)))
    after = Pull(ramp(20, RPM=(1500, 5000)))
    assert "Peak timing" not in topics(compare_pulls(before, after))


def test_a_steady_rev_at_the_limiter_is_not_a_pull():
    """Throttle alone cannot tell a run from a stationary rev.

    The earlier predicate only rejected RPM *dropping* by more than 50 per
    sample, so holding a steady 5,000 rpm with the throttle open produced a
    full pull analysis - timing, heat soak, trims - for a car that never moved.
    """
    log = ramp(40, RPM=(5000, 5000), THROTTLE_POS=(95, 95))
    assert find_pulls(log) == []


def test_a_barely_rising_rev_is_not_a_pull():
    log = ramp(40, RPM=(5000, 5150), THROTTLE_POS=(95, 95))
    assert find_pulls(log) == []


def test_a_pull_through_a_gearshift_is_not_split_in_two():
    """A real pull dips through the upshift; that is one run, not two."""
    log = (ramp(20, start=0, RPM=(1500, 5400), THROTTLE_POS=(95, 95))
           + ramp(4, start=2.0, RPM=(5400, 3400), THROTTLE_POS=(95, 95))
           + ramp(20, start=2.4, RPM=(3400, 5600), THROTTLE_POS=(95, 95)))

    pulls = find_pulls(log)
    assert len(pulls) == 1
    assert pulls[0].rpm_range == (1500, 5600)


# ---------------------------------------------------------------------------
# Air/fuel against the safe window (the tuning instrument)
# ---------------------------------------------------------------------------

def _wot_pull(**channels):
    """A short wide-open pull carrying the given constant channels."""
    out = []
    for i in range(8):
        frac = i / 7
        values = {"RPM": 2000 + 3000 * frac, "THROTTLE_POS": 88.0}
        values.update(channels)
        out.append(Sample(100 + i * 0.2, values))
    return Pull(out)


def _afr(pull):
    return next((o for o in analyse_pull(pull) if o.topic == "Air/fuel"), None)


def test_commanded_afr_in_the_window_reads_good():
    obs = _afr(_wot_pull(COMMANDED_EQUIV_RATIO=0.86))   # ~12.6:1
    assert obs is not None and obs.severity == "good"
    assert "12.6:1" in obs.detail
    # Commanded, not measured: the caveat must be there.
    assert "commanded" in obs.detail.lower()
    assert "wideband" in obs.detail.lower()


def test_lean_commanded_afr_is_flagged_serious():
    obs = _afr(_wot_pull(COMMANDED_EQUIV_RATIO=0.95))   # ~13.9:1
    assert obs.severity == "serious"
    assert "lean" in obs.detail.lower()


def test_rich_afr_is_only_informational():
    obs = _afr(_wot_pull(COMMANDED_EQUIV_RATIO=0.80))   # ~11.7:1
    assert obs.severity == "info"
    assert "richer" in obs.detail.lower()


def test_a_logged_wideband_is_used_as_the_measurement():
    obs = _afr(_wot_pull(WIDEBAND_AFR=13.4))
    assert obs.severity == "serious"                   # 13.4 is lean
    assert "measured" in obs.detail.lower()
    # Measured, so the "confirm with a wideband" caveat is absent.
    assert "wideband is the only way" not in obs.detail


def test_a_logged_lambda_is_converted_to_afr():
    obs = _afr(_wot_pull(WIDEBAND_LAMBDA=0.86))         # measured ~12.6:1
    assert obs.severity == "good"
    assert "measured" in obs.detail.lower()


def test_no_afr_channel_means_no_afr_observation():
    assert _afr(_wot_pull(TIMING_ADVANCE=25.0)) is None


def test_pull_table_uses_engine_load_when_map_is_absent():
    # A log with calculated load but no manifold pressure should still get an
    # RPM x load table, not none.
    samples = ramp(30, RPM=(1500, 5000), ENGINE_LOAD=(20, 95),
                   TIMING_ADVANCE=(24, 20))
    assert tuning.load_channel(samples) == "ENGINE_LOAD"
    table = tuning.build_table(samples, "TIMING_ADVANCE",
                               y_channel=tuning.load_channel(samples))
    assert table is not None
    assert table.y_channel == "ENGINE_LOAD"


def test_load_channel_prefers_manifold_pressure_when_present():
    samples = ramp(10, RPM=(1500, 5000), INTAKE_PRESSURE=(30, 95),
                   ENGINE_LOAD=(20, 90))
    assert tuning.load_channel(samples) == "INTAKE_PRESSURE"
