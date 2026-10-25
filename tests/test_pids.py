"""Decoder tests. Expected values come from the formulas in SAE J1979."""

import pytest

from cardiag import pids


def decode(pid, *raw):
    return pids.BY_PID[pid].decode(bytes(raw))


def test_rpm_is_quarter_counts():
    assert decode(0x0C, 0x1A, 0xF8) == 1726.0
    assert decode(0x0C, 0x00, 0x00) == 0.0
    assert decode(0x0C, 0xFF, 0xFF) == 16383.75


def test_temperatures_are_offset_by_forty():
    assert decode(0x05, 0x00) == -40
    assert decode(0x05, 0x28) == 0
    assert decode(0x05, 0x7B) == 83


def test_percentages_span_the_full_byte():
    assert decode(0x11, 0x00) == 0.0
    assert decode(0x11, 0xFF) == 100.0
    assert decode(0x04, 0x80) == pytest.approx(50.196, abs=0.01)


def test_fuel_trim_is_signed_around_128():
    assert decode(0x06, 0x80) == 0.0
    assert decode(0x06, 0x00) == -100.0
    assert decode(0x06, 0xFF) == pytest.approx(99.219, abs=0.01)


def test_speed_and_maf():
    assert decode(0x0D, 0x64) == 100
    assert decode(0x10, 0x05, 0xDC) == pytest.approx(15.0)


def test_timing_advance_is_signed():
    assert decode(0x0E, 0x80) == 0.0
    assert decode(0x0E, 0xA0) == 16.0
    assert decode(0x0E, 0x00) == -64.0


def test_control_module_voltage_is_millivolts():
    assert decode(0x42, 0x37, 0x28) == pytest.approx(14.12)


def test_supported_bitmap_lists_the_advertised_pids():
    # 0xBE1FA813: the classic example from the OBD-II literature.
    supported = decode(0x00, 0xBE, 0x1F, 0xA8, 0x13)
    assert 0x01 in supported          # bit 1 set
    assert 0x02 not in supported
    assert 0x0C in supported          # RPM
    assert 0x20 in supported          # the next bitmap exists
    assert max(supported) <= 0x20


def test_supported_bitmap_offsets_by_base():
    supported = pids.BY_PID[0x20].decode(bytes([0x80, 0x00, 0x00, 0x00]))
    assert supported == {0x21}


def test_monitor_status_reads_the_lamp_and_count():
    status = decode(0x01, 0x83, 0x07, 0xEF, 0x21)
    assert status.mil_on is True
    assert status.dtc_count == 3
    assert status.compression_ignition is False
    assert status.monitors["misfire"] == "ready"


def test_monitor_status_marks_incomplete_tests():
    status = decode(0x01, 0x00, 0x07, 0x01, 0x01)
    assert status.mil_on is False
    assert status.not_ready == ["catalyst"]
    assert status.emissions_ready is True

    status = decode(0x01, 0x00, 0x07, 0x07, 0x07)
    assert len(status.not_ready) == 3
    assert status.emissions_ready is False


def test_monitor_status_switches_names_for_diesels():
    status = decode(0x01, 0x00, 0x0F, 0x01, 0x00)
    assert status.compression_ignition is True
    assert "nmhc_catalyst" in status.monitors
    assert "catalyst" not in status.monitors


def test_oxygen_sensor_reports_unused_trim_as_none():
    value = decode(0x14, 0x60, 0xFF)
    assert value["voltage"] == pytest.approx(0.48)
    assert value["short_trim_pct"] is None


def test_lookup_accepts_names_and_hex():
    assert pids.get("RPM") is pids.BY_PID[0x0C]
    assert pids.get("0c") is pids.BY_PID[0x0C]
    assert pids.get(0x0C) is pids.BY_PID[0x0C]
    assert pids.get("NOT_A_PID") is None


def test_formatting_is_readable():
    assert pids.BY_PID[0x0C].format(1726.0) == "1726 rpm"
    assert pids.BY_PID[0x05].format(83) == "83 degC"
    assert pids.BY_PID[0x11].format(None) == "--"
    assert pids.BY_PID[0x13].format(["B1S1", "B1S2"]) == "B1S1, B1S2"
    assert "voltage" in pids.BY_PID[0x14].format({"voltage": 0.45, "short_trim_pct": None})


def test_every_pid_declares_a_workable_byte_count():
    for entry in pids.BY_PID.values():
        assert entry.num_bytes >= 1
        # Decoding a run of zero bytes must not raise; it is what a car sends
        # for an unsupported-but-advertised parameter.
        entry.decode(bytes(entry.num_bytes))


def test_dashboard_order_is_priority_first():
    order = pids.dashboard_order({0x05, 0x0C, 0x0D, 0x00})
    assert [entry.name for entry in order][:3] == ["RPM", "SPEED", "COOLANT_TEMP"]
    assert all(entry.priority > 0 for entry in order)
