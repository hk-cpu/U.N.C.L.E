"""Tests for the sensor transfer functions."""

import json

import pytest

from cardiag import sensors
from cardiag.cli import main


def run(capsys, *argv):
    code = main(list(argv))
    return code, capsys.readouterr().out


# ---------------------------------------------------------------------------
# NTC thermistor curve
# ---------------------------------------------------------------------------

def test_resistance_reads_back_to_the_tabulated_temperature():
    # 484 ohms is the 70 degC point in the factory table.
    look = sensors.temp_from_resistance(484)
    assert look.value == pytest.approx(70, abs=0.5)
    assert look.extrapolated is False


def test_temperature_gives_the_tabulated_resistance():
    look = sensors.resistance_from_temp(90)
    assert look.value == pytest.approx(241, abs=1)


def test_the_lookup_interpolates_between_points():
    # Halfway (in resistance) between the 80 degC (332) and 90 degC (241) points.
    look = sensors.temp_from_resistance((332 + 241) / 2)
    assert 80 < look.value < 90


def test_out_of_range_is_flagged_as_extrapolated():
    assert sensors.temp_from_resistance(500_000).extrapolated is True
    assert sensors.resistance_from_temp(200).extrapolated is True


def test_resistance_and_temperature_round_trip():
    look_r = sensors.resistance_from_temp(50)
    look_t = sensors.temp_from_resistance(look_r.value)
    assert look_t.value == pytest.approx(50, abs=1)


# ---------------------------------------------------------------------------
# Injectors
# ---------------------------------------------------------------------------

def test_injector_flow_scales_with_root_pressure_ratio():
    # Stock 27.5 lb/hr at 43.5 psi is ~31.75 at the 58 psi rail.
    assert sensors.injector_flow(27.5, 43.5, 58.0) == pytest.approx(31.75, abs=0.05)


def test_stock_injectors_support_about_406_bhp():
    flow = sensors.injector_flow()          # all defaults: stock at the rail
    assert sensors.max_bhp(flow) == pytest.approx(406, abs=2)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def test_sensor_temp_from_ohms(capsys):
    code, out = run(capsys, "sensor", "temp", "--ohms", "484")
    assert code == 0
    assert "70 degC" in out and "158 degF" in out


def test_sensor_temp_from_celsius(capsys):
    code, out = run(capsys, "sensor", "temp", "--celsius", "90")
    assert code == 0
    assert "241 ohms" in out


def test_sensor_temp_needs_an_input(capsys):
    code, _ = run(capsys, "sensor", "temp")
    assert code == 2


def test_sensor_injector_defaults_to_stock(capsys):
    code, out = run(capsys, "sensor", "injector", "--flow", "27.5")
    assert code == 0
    assert "31.8 lb/hr" in out
    assert "406 BHP" in out


def test_sensor_json(capsys):
    code, out = run(capsys, "--json", "sensor", "injector", "--flow", "42")
    payload = json.loads(out)
    assert payload["rated_flow_lbhr"] == 42
    assert payload["max_bhp"] > 500
