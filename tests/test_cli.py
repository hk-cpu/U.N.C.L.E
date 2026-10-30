"""CLI tests. Every case runs against the simulator, so no hardware is needed."""

import json
import sqlite3

import pytest

from cardiag import logger as logger_module
from cardiag.cli import main
from cardiag.session import Session


def run(capsys, *argv):
    """Invoke the CLI and return ``(exit_code, stdout)``."""
    code = main(list(argv))
    return code, capsys.readouterr().out


def test_scan_on_a_healthy_car_exits_zero(capsys):
    code, out = run(capsys, "--sim", "scan")
    assert code == 0
    assert "No faults found" in out


def test_scan_on_a_faulty_car_exits_nonzero(capsys):
    code, out = run(capsys, "--sim", "--sim-profile", "faulty", "scan")
    assert code == 1
    assert "Check-engine light is ON" in out
    assert "P0301" in out


def test_scan_is_the_default_command(capsys):
    code, out = run(capsys, "--sim")
    assert code == 0
    assert "Findings" in out


def test_scan_json_is_parseable(capsys):
    code, out = run(capsys, "--sim", "--sim-profile", "faulty", "--json", "scan")
    payload = json.loads(out)
    assert code == 1
    assert payload["headline"] == "Check-engine light is ON"
    assert {item["code"] for item in payload["codes"]} == {"P0420", "P0171", "P0301", "P0128"}


def test_codes_command_lists_causes(capsys):
    code, out = run(capsys, "--sim", "--sim-profile", "faulty", "codes")
    assert code == 1
    assert "Spark plug or coil on cylinder 1" in out


def test_codes_on_a_clean_car(capsys):
    code, out = run(capsys, "--sim", "codes")
    assert code == 0
    assert "No trouble codes stored." in out


def test_codes_stored_only(capsys):
    code, out = run(capsys, "--sim", "--sim-profile", "faulty", "--json", "codes", "--stored-only")
    payload = json.loads(out)
    assert all(item["status"] == "stored" for item in payload)


def test_clear_requires_confirmation_when_not_a_tty(capsys):
    code, _ = run(capsys, "--sim", "--sim-profile", "faulty", "clear")
    assert code == 2


def test_clear_with_yes_works(capsys):
    code, out = run(capsys, "--sim", "--sim-profile", "faulty", "clear", "--yes")
    assert code == 0
    assert "Codes cleared." in out


def test_info_reports_the_vin(capsys):
    code, out = run(capsys, "--sim", "--json", "info")
    payload = json.loads(out)
    assert code == 0
    assert payload["vin"] == "1HGCM82633A004352"
    assert "CAN" in payload["protocol"]


def test_read_named_pids(capsys):
    code, out = run(capsys, "--sim", "--json", "read", "RPM", "COOLANT_TEMP")
    payload = json.loads(out)
    assert code == 0
    assert payload["RPM"]["unit"] == "rpm"
    assert payload["COOLANT_TEMP"]["value"] is not None


def test_read_accepts_hex_pids(capsys):
    code, out = run(capsys, "--sim", "--json", "read", "0C")
    assert code == 0
    assert "RPM" in json.loads(out)


def test_read_rejects_an_unknown_pid(capsys):
    code, _ = run(capsys, "--sim", "read", "NOT_A_PID")
    assert code == 2


def test_read_reports_an_unsupported_pid_as_null(capsys):
    code, out = run(capsys, "--sim", "--json", "read", "HYBRID_BATTERY_LIFE")
    assert code == 0
    assert json.loads(out)["HYBRID_BATTERY_LIFE"] is None


def test_pids_lists_only_supported_by_default(capsys):
    code, out = run(capsys, "--sim", "--json", "pids")
    names = {item["name"] for item in json.loads(out)}
    assert code == 0
    assert "RPM" in names
    assert "HYBRID_BATTERY_LIFE" not in names


def test_pids_all_lists_everything_without_a_car(capsys):
    code, out = run(capsys, "--json", "pids", "--all")
    payload = json.loads(out)
    assert code == 0
    assert len(payload) > 60


def test_monitors_command(capsys):
    code, out = run(capsys, "--sim", "--sim-profile", "emissions", "monitors")
    assert code == 0
    assert "Emissions pre-check: fail" in out


def test_freeze_command(capsys):
    code, out = run(capsys, "--sim", "--sim-profile", "faulty", "freeze")
    assert code == 0
    assert "P0301" in out
    assert "2310 rpm" in out


def test_freeze_on_a_clean_car(capsys):
    code, out = run(capsys, "--sim", "freeze")
    assert code == 0
    assert "No freeze frame stored" in out


def test_lookup_needs_no_connection(capsys):
    code, out = run(capsys, "lookup", "P0420")
    assert code == 0
    assert "Catalyst system efficiency" in out
    assert "Aged catalytic converter" in out


def test_lookup_rejects_a_malformed_code(capsys):
    code, _ = run(capsys, "lookup", "banana")
    assert code == 2


def test_lookup_json(capsys):
    code, out = run(capsys, "--json", "lookup", "P0301", "U0100")
    payload = json.loads(out)
    assert [item["code"] for item in payload] == ["P0301", "U0100"]


def test_live_for_a_fixed_duration_writes_a_log(capsys, tmp_path):
    target = tmp_path / "drive.csv"
    code, out = run(capsys, "--sim", "live", "RPM", "SPEED",
                    "--duration", "0.5", "--refresh", "0.1", "--log", str(target))
    assert code == 0
    assert target.exists()

    rows = target.read_text().strip().splitlines()
    assert rows[0].split(",")[2:] == ["RPM", "SPEED"]
    assert len(rows) > 1


def test_live_rejects_an_unknown_pid(capsys):
    with pytest.raises(KeyError):
        main(["--sim", "live", "NOT_A_PID", "--duration", "0.1"])


def test_live_tune_records_the_channels_a_pull_is_read_from(capsys, tmp_path):
    target = tmp_path / "pull.csv"
    code, _ = run(capsys, "--sim", "--sim-profile", "charger", "live", "--tune",
                  "--duration", "0.5", "--refresh", "0.1", "--log", str(target))
    assert code == 0

    header = target.read_text().splitlines()[0].split(",")
    # The channels that make a log analysable as a pull must be present.
    for channel in ("RPM", "INTAKE_PRESSURE", "THROTTLE_POS", "TIMING_ADVANCE"):
        assert channel in header

    # And the recorded log is readable by `cardiag pull`.
    code, out = run(capsys, "pull", str(target))
    assert code == 0


# ---------------------------------------------------------------------------
# Loggers
# ---------------------------------------------------------------------------

def test_sqlite_logger_stores_one_row_per_reading(tmp_path):
    target = tmp_path / "drive.db"
    with Session("sim://") as car:
        readings = car.read_many(["RPM", "COOLANT_TEMP"])
        with logger_module.SqliteLogger(target) as log:
            log.write(readings)
            log.write(readings)
            assert log.rows == 4

    connection = sqlite3.connect(str(target))
    names = [row[0] for row in connection.execute("SELECT DISTINCT name FROM readings")]
    assert sorted(names) == ["COOLANT_TEMP", "RPM"]
    connection.close()


def test_open_logger_picks_the_backend_from_the_extension(tmp_path):
    assert isinstance(
        logger_module.open_logger(tmp_path / "a.csv", ["RPM"]), logger_module.CsvLogger
    )
    assert isinstance(
        logger_module.open_logger(tmp_path / "a.db", ["RPM"]), logger_module.SqliteLogger
    )


def test_csv_logger_flattens_structured_readings(tmp_path):
    target = tmp_path / "o2.csv"
    with Session("sim://") as car:
        readings = car.read_many(["O2_B1S1"])
        with logger_module.CsvLogger(target, ["O2_B1S1"]) as log:
            log.write(readings)

    value = target.read_text().strip().splitlines()[1].split(",")[2]
    assert 0.0 <= float(value) <= 1.5


# ---------------------------------------------------------------------------
# Port listing
# ---------------------------------------------------------------------------

def test_ports_recommends_a_port_that_looks_like_an_adapter(capsys, monkeypatch):
    from cardiag.transport import serial_link

    monkeypatch.setattr(serial_link, "list_ports",
                        lambda: [("/dev/ttyUSB0", "CH340 USB Serial")])
    code, out = run(capsys, "ports")
    assert code == 0
    assert "likely adapter" in out
    assert "cardiag --port /dev/ttyUSB0 scan" in out


def test_ports_does_not_recommend_a_builtin_serial_port(capsys, monkeypatch):
    from cardiag.transport import serial_link

    monkeypatch.setattr(serial_link, "list_ports", lambda: [("/dev/ttyS0", "n/a")])
    code, out = run(capsys, "ports")
    assert code == 0
    assert "None of these look like an OBD adapter" in out
    assert "cardiag --port /dev/ttyS0 scan" not in out


def test_ports_with_nothing_attached(capsys, monkeypatch):
    from cardiag.transport import serial_link

    monkeypatch.setattr(serial_link, "list_ports", list)
    code, out = run(capsys, "ports")
    assert code == 1
    assert "--sim" in out


# ---------------------------------------------------------------------------
# Mode 06, calibration and guided procedures
# ---------------------------------------------------------------------------

def test_tests_command_shows_notable_monitors(capsys):
    code, out = run(capsys, "--sim", "--sim-profile", "charger-wear", "tests")
    assert code == 0
    assert "Misfire cylinder 4" in out
    assert "Catalyst efficiency bank 2" in out


def test_tests_command_json(capsys):
    code, out = run(capsys, "--sim", "--sim-profile", "charger", "--json", "tests")
    payload = json.loads(out)
    assert code == 0
    cylinders = {item["cylinder"] for item in payload if item["cylinder"]}
    assert cylinders == set(range(1, 9))


def test_misfires_command_marks_the_deactivated_set(capsys):
    code, out = run(capsys, "--sim", "--sim-profile", "charger-wear", "misfires")
    assert code == 0
    assert "cylinder 4" in out
    assert "deactivated at cruise" in out


def test_misfires_command_json_includes_the_mds_set(capsys):
    code, out = run(capsys, "--sim", "--sim-profile", "charger",
                    "--json", "misfires")
    payload = json.loads(out)
    assert payload["deactivated_cylinders"] == [1, 4, 6, 7]
    assert payload["counts"]["4"] == 2


def test_calibration_command(capsys):
    code, out = run(capsys, "--sim", "--sim-profile", "charger", "calibration")
    assert code == 0
    assert "68RT0057AA" in out
    # The ID is explained as the OSID a tuning definition has to match.
    assert "OSID" in out
    assert "cardiag bin" in out


def test_calibration_identifies_a_chrysler_part_number():
    from cardiag.cli import _looks_like_chrysler_part, _calibration_match_notes

    assert _looks_like_chrysler_part("04896405AG")
    assert _looks_like_chrysler_part("P04896405AE")
    assert not _looks_like_chrysler_part("68RT0057AA")
    # The Chrysler-specific note only appears for a matching part number.
    assert any("04896405" in note for note in _calibration_match_notes(["04896405AG"]))
    assert not any("04896405" in note for note in _calibration_match_notes(["68RT0057AA"]))


def test_scan_can_skip_mode_06(capsys):
    code, out = run(capsys, "--sim", "--sim-profile", "charger-wear",
                    "--json", "scan", "--no-monitors")
    payload = json.loads(out)
    assert payload["monitor_tests"] == []


def test_fix_lists_available_procedures(capsys):
    code, out = run(capsys, "--vehicle", "charger", "fix")
    assert code == 0
    assert "mds-lifter" in out
    assert "manifold-bolts" in out


def test_fix_walks_a_procedure(capsys):
    code, out = run(capsys, "--vehicle", "charger", "fix", "mds-lifter")
    assert code == 0
    assert "1." in out and "5." in out
    assert "Caution:" in out
    assert "cardiag live" in out


def test_fix_rejects_an_unknown_procedure(capsys):
    code, _ = run(capsys, "--vehicle", "charger", "fix", "nonsense")
    assert code == 2


def test_fix_needs_a_profile(capsys):
    code, _ = run(capsys, "--sim", "fix", "mds-lifter")
    assert code == 2


def test_fix_json(capsys):
    code, out = run(capsys, "--vehicle", "charger", "--json", "fix", "oil-pressure")
    payload = json.loads(out)
    assert payload["key"] == "oil-pressure"
    assert len(payload["procedure"]) == 3
    assert any(step["caution"] for step in payload["procedure"])


# ---------------------------------------------------------------------------
# Running as an app
# ---------------------------------------------------------------------------

def test_app_and_ui_are_both_commands(capsys):
    from cardiag.cli import build_parser

    parser = build_parser()
    actions = [a for a in parser._actions if a.dest == "command"]
    names = set(actions[0].choices)
    assert {"ui", "app", "install-launcher"} <= names


def test_app_command_asks_for_a_window(monkeypatch):
    from cardiag import cli

    seen = {}

    def fake_run(host, port, open_browser, app_window):
        seen.update(host=host, port=port, app_window=app_window)

    import cardiag.web.server as server_module
    monkeypatch.setattr(server_module, "run", fake_run)

    assert cli.main(["app", "--no-browser"]) == 0
    assert seen["app_window"] is True

    assert cli.main(["ui", "--no-browser"]) == 0
    assert seen["app_window"] is False


def test_install_launcher_command(capsys, tmp_path, monkeypatch):
    from cardiag.web import launcher

    monkeypatch.setattr(launcher.Path, "home", classmethod(lambda cls: tmp_path))
    code, out = run(capsys, "install-launcher")
    assert code == 0
    assert "Created" in out


def test_install_launcher_json(capsys, tmp_path, monkeypatch):
    from cardiag.web import launcher

    monkeypatch.setattr(launcher.Path, "home", classmethod(lambda cls: tmp_path))
    code, out = run(capsys, "--json", "install-launcher")
    payload = json.loads(out)
    assert code == 0
    assert payload["path"].endswith((".desktop", ".command", ".bat"))


# ---------------------------------------------------------------------------
# pull: analysing a recorded log off-car
# ---------------------------------------------------------------------------

from pathlib import Path

EXAMPLE_LOG = Path(__file__).resolve().parent.parent / "examples" / "charger-pull.csv"


def test_pull_analyses_the_example_log(capsys):
    code, out = run(capsys, "pull", str(EXAMPLE_LOG))
    assert code == 0
    assert "Pull 1" in out
    assert "Timing" in out
    assert "TIMING_ADVANCE across RPM and load" in out


def test_pull_json_is_machine_readable(capsys):
    code, out = run(capsys, "--json", "pull", str(EXAMPLE_LOG))
    assert code == 0
    payload = json.loads(out)
    assert payload["samples"] > 0
    assert payload["pulls"], "the example log contains a pull"
    pull = payload["pulls"][0]
    assert pull["rpm_range"][0] < pull["rpm_range"][1]
    assert pull["observations"]
    assert payload["table"]["x_channel"] == "RPM"


def test_pull_reports_no_pull_in_a_cruise_log(capsys, tmp_path):
    log = tmp_path / "cruise.csv"
    log.write_text(
        "timestamp,RPM,THROTTLE_POS,INTAKE_PRESSURE,TIMING_ADVANCE\n"
        + "".join(f"{100 + i * 0.2},2000,18,34,28\n" for i in range(20)))
    code, out = run(capsys, "pull", str(log))
    assert code == 0
    assert "No wide-open pull" in out


def test_pull_on_a_missing_file_fails(capsys):
    code, _ = run(capsys, "pull", "/no/such/log.csv")
    assert code == 2


def test_pull_on_a_log_without_the_channel_says_so(capsys, tmp_path):
    # The log has a pull but no timing column, so the table is absent, not zeros.
    log = tmp_path / "notiming.csv"
    rows = "".join(
        f"{100 + i * 0.2},{1800 + 180 * i},88,{60 + 2 * i}\n" for i in range(20))
    log.write_text("timestamp,RPM,THROTTLE_POS,INTAKE_PRESSURE\n" + rows)
    code, out = run(capsys, "pull", str(log))
    assert code == 0
    assert "does not contain TIMING_ADVANCE" in out


# ---------------------------------------------------------------------------
# bin: read-only XDF calibration viewer
# ---------------------------------------------------------------------------

EXAMPLE_BIN = Path(__file__).resolve().parent.parent / "examples" / "demo.bin"
EXAMPLE_XDF = Path(__file__).resolve().parent.parent / "examples" / "demo.xdf"


def test_bin_lists_the_definitions(capsys):
    code, out = run(capsys, "bin", str(EXAMPLE_BIN), str(EXAMPLE_XDF), "--list")
    assert code == 0
    assert "Spark Advance" in out
    assert "Rev Limiter" in out


def test_bin_shows_a_table_with_scaled_values(capsys):
    code, out = run(capsys, "bin", str(EXAMPLE_BIN), str(EXAMPLE_XDF),
                    "--table", "Spark Advance")
    assert code == 0
    assert "20.0" in out and "16.0" in out        # corners of the scaled table
    assert "big-endian" in out


def test_bin_shows_a_constant(capsys):
    code, out = run(capsys, "bin", str(EXAMPLE_BIN), str(EXAMPLE_XDF),
                    "--table", "Rev Limiter")
    assert code == 0
    assert "5600" in out


def test_bin_json(capsys):
    code, out = run(capsys, "--json", "bin", str(EXAMPLE_BIN), str(EXAMPLE_XDF),
                    "--table", "Spark Advance")
    payload = json.loads(out)
    assert payload["cells"][0][0] == 20.0
    assert payload["byte_order"] == "big-endian"


def test_bin_unknown_table_fails(capsys):
    code, _ = run(capsys, "bin", str(EXAMPLE_BIN), str(EXAMPLE_XDF),
                  "--table", "Nope")
    assert code == 2


def test_bin_missing_file_fails(capsys):
    code, _ = run(capsys, "bin", "/no/such.bin", str(EXAMPLE_XDF))
    assert code == 2


def test_bin_rejects_a_doctype_xdf(capsys, tmp_path):
    hostile = tmp_path / "evil.xdf"
    hostile.write_text('<!DOCTYPE x [<!ENTITY a "b">]><XDFFORMAT></XDFFORMAT>')
    code, _ = run(capsys, "bin", str(EXAMPLE_BIN), str(hostile))
    assert code == 2


# ---------------------------------------------------------------------------
# frames: annotating a raw CAN-C capture (no car)
# ---------------------------------------------------------------------------

CANDUMP = (
    "(1703001000.000000) can0 312#0ABB11223344FF00\n"
    "(1703001000.013000) can0 200#000012340000ABCD\n"
    "(1703001000.026000) can0 312#0AC011223344FF00\n"
    "(1703001000.039000) can0 210#00005500000000AA\n"
    "(1703001000.052000) can0 999#DEADBEEF\n"
    "(1703001000.065000) can0 312#0AD511223344FF00\n"
)


def test_frames_list(capsys):
    code, out = run(capsys, "frames", "--list")
    assert code == 0
    assert "Engine data" in out
    assert "Brake pressure" in out


def test_frames_summary_groups_and_flags_unknown(capsys, tmp_path):
    cap = tmp_path / "cap.log"
    cap.write_text(CANDUMP)
    code, out = run(capsys, "frames", str(cap))
    assert code == 0
    assert "0x312" in out and "Engine data" in out
    assert "0x999" in out and "unknown" in out
    assert "not in the frame map" in out


def test_frames_one_id_shows_raw_only(capsys, tmp_path):
    cap = tmp_path / "cap.log"
    cap.write_text(CANDUMP)
    code, out = run(capsys, "frames", str(cap), "--id", "0x312")
    assert code == 0
    assert "0abb11223344ff00" in out
    assert "no scaling is applied" in out


def test_frames_json(capsys, tmp_path):
    cap = tmp_path / "cap.log"
    cap.write_text(CANDUMP)
    code, out = run(capsys, "--json", "frames", str(cap))
    payload = json.loads(out)
    assert payload["total_frames"] == 6
    ids = {row["id"]: row for row in payload["ids"]}
    assert ids["0x312"]["count"] == 3
    assert ids["0x999"]["known"] is False


def test_frames_rate_uses_intervals_not_frame_count(capsys, tmp_path):
    # 0x312 appears 3 times over a 0.065 s span, so the cadence is
    # (3-1)/0.065 = ~30.8 Hz. The old count/span calc gave ~46.2 Hz.
    cap = tmp_path / "cap.log"
    cap.write_text(CANDUMP)
    code, out = run(capsys, "--json", "frames", str(cap))
    hz = {r["id"]: r["hz"] for r in json.loads(out)["ids"]}["0x312"]
    assert 30.0 < hz < 31.0


def test_frames_parses_savvycan_csv(capsys, tmp_path):
    cap = tmp_path / "cap.csv"
    cap.write_text(
        "Time Stamp,ID,Extended,Dir,Bus,LEN,D1,D2,D3,D4,D5,D6,D7,D8\n"
        "0.0,0x210,false,Rx,0,8,00,00,55,00,00,00,00,AA\n")
    code, out = run(capsys, "frames", str(cap))
    assert code == 0
    assert "Brake pressure" in out


def test_frames_on_an_empty_capture_fails(capsys, tmp_path):
    cap = tmp_path / "empty.log"
    cap.write_text("nothing to see here\n")
    code, _ = run(capsys, "frames", str(cap))
    assert code == 2
