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
