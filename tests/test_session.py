"""End-to-end tests driving the simulated vehicle through the public API."""

import pytest

from cardiag import report as report_module
from cardiag.session import Session


@pytest.fixture
def healthy():
    with Session("sim://") as car:
        yield car


@pytest.fixture
def faulty():
    with Session("sim://?profile=faulty") as car:
        yield car


def test_connect_negotiates_a_protocol(healthy):
    assert healthy.adapter is not None
    assert "CAN" in healthy.adapter.protocol
    assert healthy.adapter.voltage == pytest.approx(14.1, abs=0.3)


def test_supported_pids_are_discovered_across_bitmap_blocks(healthy):
    supported = healthy.supported_pids
    assert 0x0C in supported          # block A
    assert 0x21 in supported          # block B
    assert 0x42 in supported          # block C
    assert healthy.supports("RPM")
    assert not healthy.supports("HYBRID_BATTERY_LIFE")


def test_supported_pids_are_cached(healthy):
    first = healthy.supported_pids
    assert healthy.supported_pids is first


def test_reading_a_live_pid(healthy):
    reading = healthy.read("RPM")
    assert reading is not None
    assert 0 < reading.value < 7000
    assert reading.pid.unit == "rpm"


def test_reading_an_unsupported_pid_returns_none(healthy):
    assert healthy.read("HYBRID_BATTERY_LIFE") is None


def test_reading_an_unknown_pid_raises(healthy):
    with pytest.raises(KeyError):
        healthy.read("NOT_A_REAL_PID")


def test_read_many_skips_what_the_car_does_not_answer(healthy):
    readings = healthy.read_many(["RPM", "COOLANT_TEMP", "HYBRID_BATTERY_LIFE"])
    assert set(readings) == {"RPM", "COOLANT_TEMP"}


def test_healthy_car_has_no_codes(healthy):
    assert healthy.read_dtcs() == []


def test_faulty_car_reports_stored_and_pending_codes(faulty):
    codes = faulty.read_dtcs()
    by_code = {item.code: item for item in codes}

    assert set(by_code) == {"P0420", "P0171", "P0301", "P0128"}
    assert by_code["P0301"].status == "stored"
    assert by_code["P0128"].status == "pending"
    # The worst code sorts to the front.
    assert codes[0].code == "P0301"


def test_stored_only_skips_pending(faulty):
    codes = faulty.read_dtcs(include_pending=False, include_permanent=False)
    assert all(item.status == "stored" for item in codes)
    assert len(codes) == 3


def test_monitor_status_tracks_the_lamp(healthy, faulty):
    assert healthy.monitor_status().mil_on is False
    status = faulty.monitor_status()
    assert status.mil_on is True
    assert status.dtc_count == 3


def test_clearing_codes_turns_the_lamp_off(faulty):
    assert faulty.read_dtcs()
    faulty.clear_dtcs()
    assert faulty.read_dtcs() == []
    assert faulty.monitor_status().mil_on is False


def test_clearing_resets_the_readiness_monitors(faulty):
    faulty.clear_dtcs()
    status = faulty.monitor_status()
    assert status.not_ready, "monitors should be incomplete straight after a clear"
    assert status.emissions_ready is False


def test_vin_is_read_over_multiple_frames(healthy):
    vin = healthy.read_vin()
    assert vin == "1HGCM82633A004352"
    assert len(vin) == 17


def test_vehicle_info(healthy):
    info = healthy.vehicle_info()
    assert info.vin == "1HGCM82633A004352"
    assert info.fuel_type == "petrol"
    assert info.ecu_name.startswith("ECM")


def test_freeze_frame_carries_the_trigger_code(faulty):
    frame = faulty.freeze_frame()
    assert frame["trigger_code"] == "P0301"
    assert frame["RPM"].value == pytest.approx(2310)
    assert frame["COOLANT_TEMP"].value == 89


def test_healthy_car_has_no_freeze_frame(healthy):
    assert healthy.freeze_frame() == {}


def test_live_pids_are_ordered_for_a_dashboard(healthy):
    names = [entry.name for entry in healthy.live_pids()]
    assert names[0] == "RPM"
    assert "PIDS_A" not in names       # bitmaps are not live data


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

def test_report_on_a_healthy_car_says_so(healthy):
    result = report_module.build(healthy)
    assert result.headline == "No faults found"
    assert result.worst_severity == "info"
    assert result.codes == []


def test_report_on_a_faulty_car_lists_every_code(faulty):
    result = report_module.build(faulty)
    assert result.headline == "Check-engine light is ON"
    assert result.worst_severity == "serious"

    titles = " ".join(finding.title for finding in result.findings)
    for code in ("P0301", "P0171", "P0420", "P0128"):
        assert code in titles


def test_report_flags_high_fuel_trims(faulty):
    result = report_module.build(faulty)
    trims = [f for f in result.findings if "Fuel trims are high" in f.title]
    assert len(trims) == 1
    assert "vacuum leak" in trims[0].suggestion.lower()


def test_report_findings_are_sorted_worst_first(faulty):
    result = report_module.build(faulty)
    order = [report_module.FINDING_SEVERITY.index(f.severity) for f in result.findings]
    assert order == sorted(order)


def test_report_serialises_to_json_safe_types(faulty):
    import json

    payload = report_module.build(faulty).to_dict()
    json.dumps(payload)      # must not raise
    assert payload["status"]["mil_on"] is True
    assert payload["freeze_frame"]["trigger_code"]["formatted"] == "P0301"
    # Every entry carries a human label, not just a raw PID name.
    assert payload["freeze_frame"]["RPM"]["description"] == "Engine speed"


def test_emissions_profile_is_flagged_as_not_ready():
    with Session("sim://?profile=emissions") as car:
        result = report_module.build(car)
        assert result.codes == []
        incomplete = [f for f in result.findings if "readiness monitor" in f.title]
        assert incomplete
        assert "Drive a mixed cycle" in incomplete[0].suggestion


def test_unknown_simulator_profile_is_rejected():
    with pytest.raises(ValueError, match="unknown simulator profile"):
        Session("sim://?profile=nonsense")
