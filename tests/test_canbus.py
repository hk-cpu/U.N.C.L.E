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


# ---------------------------------------------------------------------------
# Grounding topology and fuses
# ---------------------------------------------------------------------------

def test_grounds_are_well_formed_and_cover_the_map():
    names = {g.name for g in canbus.grounds()}
    # The PCM ground point and the DLC ground point are the two that matter most
    # to cardiag, so they must be present.
    assert {"G102", "G202"} <= names
    for g in canbus.grounds():
        assert g.name.startswith("G")
        assert g.location and g.serves


def test_pcm_ground_names_the_pcm_circuits():
    g102 = canbus.BY_GROUND["G102"]
    assert "PCM" in g102.serves
    # It ties back to the PCM ground circuits the pinout already defines.
    assert "Z816" in g102.note and "Z13" in g102.note


def test_key_fuses_exist_for_both_panels():
    assert canbus.fuses(canbus.IPM), "the under-hood IPM must have fuses"
    assert canbus.fuses(canbus.PDC), "the trunk PDC must have fuses"
    # Every fuse that claims to be the same wire as a circuit must name a real
    # circuit, or the cross-reference points at nothing.
    for fuse in canbus.fuses():
        if fuse.circuit:
            assert fuse.circuit in canbus.BY_CIRCUIT


def test_pcm_constant_feed_links_to_its_circuit():
    linked = canbus.fuses_for_circuit("A209")
    assert [f.position for f in linked] == ["Fuse 11"]
    assert "keep-alive" in linked[0].feeds.lower()


def test_ignition_run_start_fuse_links_to_the_wake_circuit():
    # Fuse 29 feeds F18, the RUN-START wire cockpit mode watches.
    assert [f.position for f in canbus.fuses_for_circuit("F18")] == ["Fuse 29"]


def test_canbus_command_shows_grounds_and_fuses(capsys):
    code, out = run(capsys, "canbus")
    assert code == 0
    assert "G102" in out and "grounding" in out.lower()
    assert "Fuse 11" in out and "Rear PDC" in out


def test_canbus_category_filter_hides_grounds_and_fuses(capsys):
    # The grounds/fuses blocks are part of the full reference, not a circuit
    # category, so a narrow filter should not print them.
    code, out = run(capsys, "canbus", "--category", "sensor")
    assert code == 0
    assert "G102" not in out
    assert "Fuse 11" not in out


def test_canbus_json_includes_grounds_and_fuses(capsys):
    code, out = run(capsys, "--json", "canbus")
    payload = json.loads(out)
    assert any(g["name"] == "G202" for g in payload["grounds"])
    assert any(f["position"] == "Fuse 11" for f in payload["fuses"])
    assert "ohm" in payload["ground_spec"]
