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
