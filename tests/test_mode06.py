"""Mode 06 decoding, and the analysis built on top of it."""

import pytest

from cardiag import mode06, report as report_module
from cardiag.session import Session


def record(mid, tid, scaling, value, minimum, maximum):
    return bytes([mid, tid, scaling,
                  value >> 8, value & 0xFF,
                  minimum >> 8, minimum & 0xFF,
                  maximum >> 8, maximum & 0xFF])


# ---------------------------------------------------------------------------
# Record parsing
# ---------------------------------------------------------------------------

def test_parses_one_record():
    (result,) = mode06.parse_records(record(0xA5, 0x0B, 0x24, 168, 0, 200))
    assert result.mid == 0xA5
    assert result.tid == 0x0B
    assert result.raw_value == 168
    assert result.raw_max == 200


def test_parses_several_records():
    payload = record(0xA2, 0x0B, 0x24, 3, 0, 200) + record(0xA3, 0x0B, 0x24, 9, 0, 200)
    results = mode06.parse_records(payload)
    assert [r.mid for r in results] == [0xA2, 0xA3]
    assert [r.raw_value for r in results] == [3, 9]


def test_a_trailing_partial_record_is_ignored():
    payload = record(0xA2, 0x0B, 0x24, 3, 0, 200) + b"\xA3\x0B"
    assert len(mode06.parse_records(payload)) == 1


# ---------------------------------------------------------------------------
# Scaling
# ---------------------------------------------------------------------------

def test_counts_scale_one_to_one():
    (result,) = mode06.parse_records(record(0xA2, 0x0B, 0x24, 42, 0, 200))
    assert result.value == 42
    assert result.unit == "counts"
    assert result.known_scaling is True


def test_ratio_scaling():
    raw = int(0.5 / 0.0000305)
    (result,) = mode06.parse_records(record(0x41, 0x80, 0x05, raw, 0, 0xFFFE))
    assert result.value == pytest.approx(0.5, abs=0.001)


def test_unknown_scaling_reports_the_raw_value_rather_than_guessing():
    (result,) = mode06.parse_records(record(0x41, 0x01, 0xFE, 1234, 0, 0))
    assert result.known_scaling is False
    assert result.value == 1234
    assert "raw" in result.format_value()
    assert "FE" in result.format_value()


# ---------------------------------------------------------------------------
# Limits and headroom
# ---------------------------------------------------------------------------

def test_pass_and_fail_against_the_ecus_own_limits():
    passing = mode06.parse_records(record(0xA2, 0x0B, 0x24, 10, 0, 200))[0]
    failing = mode06.parse_records(record(0xA2, 0x0B, 0x24, 250, 0, 200))[0]
    assert passing.passed is True
    assert failing.passed is False


def test_no_limits_means_no_verdict():
    (result,) = mode06.parse_records(record(0x41, 0x01, 0x24, 100, 0, 0))
    assert result.passed is None
    assert result.headroom is None


def test_headroom_measures_how_much_of_the_band_is_used():
    (result,) = mode06.parse_records(record(0xA2, 0x0B, 0x24, 180, 0, 200))
    assert result.headroom == pytest.approx(0.9)


def test_headroom_is_clamped():
    (result,) = mode06.parse_records(record(0xA2, 0x0B, 0x24, 400, 0, 200))
    assert result.headroom == 1.0


def test_headroom_warns_approaching_either_limit():
    # A test with both a floor and a ceiling can fail at either end. Approaching
    # the lower bound is just as close to failing as approaching the upper one,
    # so it must read high too - measuring only distance from the floor (as this
    # once did) left a value sliding toward raw_min looking perfectly healthy.
    near_max = mode06.parse_records(record(0xA2, 0x0B, 0x24, 190, 20, 200))[0]
    near_min = mode06.parse_records(record(0xA2, 0x0B, 0x24, 30, 20, 200))[0]
    middle = mode06.parse_records(record(0xA2, 0x0B, 0x24, 110, 20, 200))[0]
    assert near_max.passed is near_min.passed is True
    assert near_max.headroom == pytest.approx(0.889, abs=0.01)
    assert near_min.headroom == pytest.approx(0.889, abs=0.01)
    assert middle.headroom == pytest.approx(0.0)


def test_a_minimum_limit_is_respected():
    (result,) = mode06.parse_records(record(0x01, 0x07, 0x24, 5, 20, 200))
    assert result.passed is False


# ---------------------------------------------------------------------------
# Monitor identity
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("mid,cylinder", [
    (0xA2, 1), (0xA5, 4), (0xA9, 8), (0xAB, 10),
])
def test_misfire_mids_map_to_cylinders(mid, cylinder):
    assert mode06.misfire_cylinder(mid) == cylinder


def test_non_misfire_mids_have_no_cylinder():
    assert mode06.misfire_cylinder(0xA1) is None       # the engine-wide summary
    assert mode06.misfire_cylinder(0x41) is None


def test_monitor_names():
    assert "Misfire cylinder 4" == mode06.monitor_name(0xA5)
    assert "Catalyst" in mode06.monitor_name(0x41)
    assert "not in the standard table" in mode06.monitor_name(0xF3)


def test_supported_bitmap_decoding():
    # Bitmap lives in the value+min fields: 0x80000000 advertises MID base+1.
    payload = record(0x00, 0x00, 0x01, 0x8000, 0x0000, 0)
    assert mode06.decode_supported(0x00, payload) == {0x01}


def test_to_dict_is_json_safe():
    import json

    (result,) = mode06.parse_records(record(0xA5, 0x0B, 0x24, 168, 0, 200))
    payload = result.to_dict()
    json.dumps(payload)
    assert payload["cylinder"] == 4
    assert payload["passed"] is True


# ---------------------------------------------------------------------------
# Against the simulated car
# ---------------------------------------------------------------------------

def test_monitors_are_discovered_across_the_bitmap_chain():
    with Session("sim://?profile=charger") as car:
        monitors = car.supported_monitors()
        assert 0x41 in monitors and 0x42 in monitors       # both catalysts
        assert {0xA2, 0xA5, 0xA9} <= monitors              # cylinders 1, 4, 8


def test_misfire_counts_cover_every_cylinder():
    with Session("sim://?profile=charger") as car:
        counts = car.misfire_counts()
        assert sorted(counts) == list(range(1, 9))


def test_calibration_is_read():
    with Session("sim://?profile=charger") as car:
        calibration = car.calibration()
        assert calibration["calibration_ids"] == ["68RT0057AA"]
        assert calibration["verification_numbers"] == ["4A1B7C2D"]


def test_a_healthy_car_raises_no_misfire_findings():
    with Session("sim://?profile=charger") as car:
        result = report_module.build(car)
        titles = " ".join(f.title for f in result.findings)
        assert "misfiring far more" not in titles
        assert "skewed" not in titles


def test_wear_is_caught_before_any_code_is_set():
    """The whole point: no codes, no warning light, but the wear is visible."""
    with Session("sim://?profile=charger-wear") as car:
        result = report_module.build(car)

        assert result.codes == []
        assert result.status.mil_on is False

        titles = [f.title for f in result.findings]
        assert any("skewed towards the cylinder-deactivation set" in t for t in titles)
        assert any("close to failing" in t for t in titles)
        # It must not be reported as a clean bill of health.
        assert result.headline != "No faults found"


def test_the_skew_finding_names_the_deactivated_cylinders():
    with Session("sim://?profile=charger-wear") as car:
        result = report_module.build(car)
        finding = next(f for f in result.findings if "skewed" in f.title)
        assert "1, 4, 6, 7" in finding.detail


def test_a_single_bad_cylinder_is_called_out_with_its_location():
    with Session("sim://?profile=charger-misfire") as car:
        result = report_module.build(car)
        finding = next(f for f in result.findings if "misfiring far more" in f.title)
        assert "Cylinder 4" in finding.title
        assert "bank 2" in finding.detail


def test_monitors_can_be_skipped():
    with Session("sim://?profile=charger-wear") as car:
        result = report_module.build(car, include_monitors=False)
        assert result.monitor_tests == []
        assert result.misfire_counts == {}
        # Without mode 06 this car looks perfectly healthy, which is exactly
        # why reading it matters.
        assert result.headline == "No faults found"


def test_a_car_with_unready_monitors_is_not_headlined_as_clean():
    """The headline sat directly above the findings contradicting it.

    The emissions profile has no codes and no warning light but four
    incomplete readiness monitors, which _analyse_monitors raises as a
    moderate finding. "No faults found" over the top of that told the owner
    the car was fine while the same report said its emissions pre-check
    would fail.
    """
    from cardiag import report as report_module
    from cardiag.session import Session

    with Session("sim://?profile=emissions") as car:
        result = report_module.build(car)

    assert result.headline != "No faults found"
    assert "monitors are not ready" in result.headline
    assert not result.codes and not (result.status and result.status.mil_on)


def test_a_genuinely_clean_car_is_still_headlined_as_clean():
    from cardiag import report as report_module
    from cardiag.session import Session

    with Session("sim://?profile=charger") as car:
        assert report_module.build(car).headline == "No faults found"


def test_a_silent_ecu_is_not_a_clean_bill_of_health():
    """No findings because nothing was wrong, or because nothing was read?

    The adapter can negotiate a protocol and then get nothing back - a key only
    part way round, a module asleep, a flaky cable. Every analyser then returns
    nothing, and the fallback used to announce "No trouble codes, and every
    live reading sampled was inside its normal range" when not one reading had
    been sampled.
    """
    from cardiag import report as report_module
    from cardiag.session import Session

    with Session("sim://?profile=charger") as car:
        car.connect()
        car.transport.ignition = False      # modules asleep, adapter still up
        result = report_module.build(car)

    assert result.has_data is False
    assert result.headline == "The scan returned no usable data"
    assert [f.severity for f in result.findings] == ["serious"]
    assert "did not happen" in result.findings[0].detail


def test_a_car_that_answered_is_still_judged_normally():
    from cardiag import report as report_module
    from cardiag.session import Session

    with Session("sim://?profile=charger") as car:
        result = report_module.build(car)

    assert result.has_data is True
    assert result.headline == "No faults found"

def test_the_worst_count_survives_when_a_cylinder_reports_twice():
    """ECUs report several mode 06 tests per cylinder.

    Typically a this-drive-cycle counter and a longer-run average. The dict
    comprehension this replaced kept whichever arrived last, so a low reading
    could mask a high one - losing exactly the fault this tool exists to
    surface. Session.misfire_counts already took the max; report.build did not.
    """
    from cardiag import mode06
    from cardiag import report as report_module
    from cardiag.session import Session

    base = mode06.MISFIRE_BASE + 4                      # cylinder 4
    pair = [
        mode06.TestResult(mid=base, tid=0x0B, scaling_id=0x24,
                          raw_value=168, raw_min=0, raw_max=0),
        mode06.TestResult(mid=base, tid=0x0C, scaling_id=0x24,
                          raw_value=3, raw_min=0, raw_max=0),   # arrives last
    ]

    with Session("sim://?profile=charger-wear") as car:
        car.monitor_tests = lambda *a, **k: pair
        result = report_module.build(car)

    assert result.misfire_counts[4] == 168, "the worst count must win, not the last"


def test_report_and_session_agree_about_misfire_counts():
    from cardiag import report as report_module
    from cardiag.session import Session

    with Session("sim://?profile=charger-wear") as car:
        result = report_module.build(car)
        assert result.misfire_counts == car.misfire_counts()
