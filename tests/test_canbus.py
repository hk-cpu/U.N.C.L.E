"""Tests for the physical bus and wiring reference map."""

import json

from cardiag import canbus, pids
from cardiag.cli import main


def run(capsys, *argv):
    code = main(list(argv))
    return code, capsys.readouterr().out


# ---------------------------------------------------------------------------
# The data itself
# ---------------------------------------------------------------------------

def test_every_circuit_is_well_formed():
    categories = {canbus.POWER, canbus.GROUND, canbus.IGNITION,
                  canbus.CAN, canbus.SENSOR, canbus.ACTUATOR}
    for circuit in canbus.circuits():
        assert circuit.category in categories
        assert circuit.connector in {"C1", "C2", "C3"}
        assert 1 <= circuit.pin <= 38
        assert circuit.circuit and circuit.function


def test_every_named_pid_actually_exists():
    # A circuit that claims to feed a PID must name one cardiag can decode,
    # or the "trace a reading back to a wire" feature points at nothing.
    for circuit in canbus.circuits():
        if circuit.pid:
            assert pids.get(circuit.pid) is not None, circuit.pid


def test_can_pins_match_the_diagnostic_connector():
    # CAN-C on the PCM (C1 34/35) is the same pair as DLC pins 6/14.
    can = {c.circuit: c for c in canbus.circuits(canbus.CAN)}
    assert can["D65"].pin == 34  # CAN high
    assert can["D64"].pin == 35  # CAN low
    dlc = {p.pin for p in canbus.DLC_PINS}
    assert {6, 14, 16} <= dlc


def test_for_pid_maps_a_reading_to_a_wire():
    circuit = canbus.for_pid("INTAKE_PRESSURE")
    assert circuit is not None
    assert circuit.connector == "C2"
    assert circuit.circuit == "K1"
    # Case-insensitive, and unknown PIDs return nothing rather than guessing.
    assert canbus.for_pid("intake_pressure") is circuit
    assert canbus.for_pid("RPM") is None  # RPM is computed, not a single wire


def test_power_map_separates_constant_from_switched():
    power = canbus.power_map()
    constant = {c.circuit for c in power["constant"]}
    ignition = {c.circuit for c in power["ignition"]}
    # Constant battery (A209) is live key-out; the RUN-START feed (F18) is not.
    assert "A209" in constant
    assert "F18" in ignition
    assert constant.isdisjoint(ignition)


def test_category_filter_is_a_strict_subset():
    sensors = canbus.circuits(canbus.SENSOR)
    assert sensors
    assert all(c.category == canbus.SENSOR for c in sensors)
    assert len(sensors) < len(canbus.circuits())


# ---------------------------------------------------------------------------
# The CLI view (needs no car)
# ---------------------------------------------------------------------------

def test_canbus_command_runs_without_a_car(capsys):
    code, out = run(capsys, "canbus")
    assert code == 0
    assert "CAN-C" in out
    assert "J1962" in out
    assert "MAP" in out or "manifold absolute" in out.lower()


def test_canbus_category_filter(capsys):
    code, out = run(capsys, "canbus", "--category", "ignition")
    assert code == 0
    assert "F18" in out
    # A sensor circuit should not appear when filtering to ignition.
    assert "K1" not in out.split("\n")[0]


def test_canbus_json_is_machine_readable(capsys):
    code, out = run(capsys, "--json", "canbus")
    assert code == 0
    payload = json.loads(out)
    assert payload["diag_request_id"] == 0x7E0
    assert payload["diag_response_id"] == 0x7E8
    assert any(b["speed_kbps"] == 500.0 for b in payload["buses"])
    circuits = {c["circuit"]: c for c in payload["circuits"]}
    assert circuits["K1"]["pid"] == "INTAKE_PRESSURE"
