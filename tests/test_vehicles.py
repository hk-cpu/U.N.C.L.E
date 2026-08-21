"""Vehicle profile tests, focused on the 2006 Charger R/T."""

import json

import pytest

from cardiag import report as report_module, vehicles
from cardiag.cli import main
from cardiag.session import Session
from cardiag.transport.simulator import CHARGER_VIN

CHARGER = vehicles.CHARGER_RT_2006


def run(capsys, *argv):
    code = main(list(argv))
    return code, capsys.readouterr().out


# ---------------------------------------------------------------------------
# The profile itself
# ---------------------------------------------------------------------------

def test_lookup_by_key_and_alias():
    assert vehicles.get("charger-rt-2006") is CHARGER
    assert vehicles.get("charger") is CHARGER
    assert vehicles.get("HEMI") is CHARGER
    assert vehicles.get("not-a-car") is None


def test_hemi_bank_layout_follows_chrysler_numbering():
    engine = CHARGER.engine
    # Odd cylinders on bank 1, even on bank 2.
    assert [engine.bank_of(c) for c in (1, 3, 5, 7)] == [1, 1, 1, 1]
    assert [engine.bank_of(c) for c in (2, 4, 6, 8)] == [2, 2, 2, 2]
    assert "driver" in engine.side_of(1)
    assert "passenger" in engine.side_of(2)


def test_hemi_has_two_plugs_per_cylinder():
    assert CHARGER.engine.plugs_per_cylinder == 2
    assert CHARGER.engine.total_plugs == 16


def test_mds_cylinders():
    engine = CHARGER.engine
    assert engine.deactivated_cylinders == (1, 4, 6, 7)
    assert engine.is_deactivated(4) is True
    assert engine.is_deactivated(2) is False


def test_firing_order_covers_every_cylinder_once():
    order = CHARGER.engine.firing_order
    assert sorted(order) == list(range(1, 9))


def test_locate_describes_bank_side_and_plugs():
    text = CHARGER.engine.locate(4)
    assert "bank 2" in text
    assert "passenger" in text
    assert "2 spark plugs" in text


def test_issues_match_by_exact_code():
    issues = CHARGER.issues_for(["P0521"])
    assert [issue.title for issue in issues] == ["Oil pressure sending unit failure"]


def test_issues_match_by_prefix():
    # The transmission issue is keyed on the whole P07xx family.
    titles = [issue.title for issue in CHARGER.issues_for(["P0740"])]
    assert "NAG1 transmission faults" in titles


def test_issues_do_not_match_unrelated_codes():
    assert CHARGER.issues_for(["P0500"]) == []


def test_profile_serialises_to_json():
    json.dumps(CHARGER.to_dict())


# ---------------------------------------------------------------------------
# VIN matching
# ---------------------------------------------------------------------------

def test_charger_vin_matches_the_profile():
    assert vehicles.match_vin(CHARGER_VIN) is CHARGER


def test_a_vin_with_a_bad_check_digit_never_matches():
    broken = CHARGER_VIN[:8] + "0" + CHARGER_VIN[9:]
    assert vehicles.match_vin(broken) is None


def test_a_different_car_does_not_match():
    assert vehicles.match_vin("1HGCM82633A004352") is None


def test_a_2006_dodge_without_the_hemi_does_not_match():
    # Same car, 2.7 V6 engine code in position 8 - not an R/T.
    from cardiag import vin as vin_module

    base = list(CHARGER_VIN)
    base[7] = "V"
    candidate = "".join(base)
    fixed = candidate[:8] + (vin_module.check_digit(candidate) or "0") + candidate[9:]
    assert vehicles.match_vin(fixed) is None


# ---------------------------------------------------------------------------
# Profile-aware reporting
# ---------------------------------------------------------------------------

@pytest.fixture
def charger():
    with Session("sim://?profile=charger-misfire") as car:
        yield car


def test_report_auto_detects_the_profile_from_the_vin(charger):
    result = report_module.build(charger)
    assert result.profile is CHARGER


def test_detection_can_be_turned_off(charger):
    result = report_module.build(charger, detect_profile=False)
    assert result.profile is None


def test_misfire_finding_names_the_bank_and_side(charger):
    result = report_module.build(charger)
    finding = next(f for f in result.findings if f.title.startswith("P0304"))
    assert "bank 2" in finding.detail
    assert "passenger" in finding.detail
    assert "MDS" in finding.detail


def test_mds_known_issue_is_raised_for_an_mds_cylinder(charger):
    result = report_module.build(charger)
    titles = [f.title for f in result.findings]
    assert any("MDS lifter" in title for title in titles)


def test_lean_bank_two_raises_the_manifold_bolt_issue(charger):
    result = report_module.build(charger)
    manifold = next(f for f in result.findings if "manifold bolts" in f.title)
    assert "P0174" in manifold.detail


def test_bank_two_fuel_trims_are_read_and_analysed(charger):
    result = report_module.build(charger)
    assert "LONG_FUEL_TRIM_2" in result.readings

    trims = [f for f in result.findings if "Fuel trims are high" in f.title]
    assert len(trims) == 1
    assert "bank 2" in trims[0].title


def test_a_healthy_charger_raises_no_known_issues():
    with Session("sim://?profile=charger") as car:
        result = report_module.build(car)
        assert result.profile is CHARGER
        assert not [f for f in result.findings if "Known issue" in f.title]


def test_generic_car_gets_no_bank_side_wording():
    with Session("sim://?profile=faulty") as car:
        result = report_module.build(car)
        assert result.profile is None
        finding = next(f for f in result.findings if f.title.startswith("P0301"))
        assert "bank" not in finding.detail.lower()


def test_hemi_thresholds_tolerate_a_hotter_engine():
    generic = vehicles.Thresholds()
    assert CHARGER.thresholds.coolant_warning > generic.coolant_warning


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def test_vehicle_command_without_a_car(capsys):
    code, out = run(capsys, "--vehicle", "charger", "vehicle")
    assert code == 0
    assert "5.7 L HEMI V8" in out
    assert "16 (2 per cylinder)" in out
    assert "1-8-4-3-6-5-7-2" in out
    assert "MDS lifter" in out


def test_vehicle_command_detects_from_the_car(capsys):
    code, out = run(capsys, "--sim", "--sim-profile", "charger", "vehicle")
    assert code == 0
    assert "Dodge Charger R/T" in out
    assert CHARGER_VIN in out


def test_vehicle_command_on_an_unknown_car(capsys):
    code, out = run(capsys, "--sim", "vehicle")
    assert code == 0
    assert "No vehicle profile applies" in out


def test_vehicle_command_json(capsys):
    code, out = run(capsys, "--vehicle", "charger", "--json", "vehicle")
    payload = json.loads(out)
    assert payload["engine"]["total_plugs"] == 16
    assert payload["engine"]["deactivated_cylinders"] == [1, 4, 6, 7]


def test_unknown_vehicle_profile_is_rejected(capsys):
    code, _ = run(capsys, "--vehicle", "delorean", "scan")
    assert code == 2


def test_vehicle_none_disables_detection(capsys):
    code, out = run(capsys, "--sim", "--sim-profile", "charger-misfire",
                    "--vehicle", "none", "--json", "scan")
    assert json.loads(out)["profile"] is None


def test_scan_shows_the_profile_name(capsys):
    code, out = run(capsys, "--sim", "--sim-profile", "charger-misfire", "scan")
    assert "Profile: Dodge Charger R/T" in out


def test_lookup_with_a_vehicle_adds_model_context(capsys):
    code, out = run(capsys, "--vehicle", "charger", "lookup", "P0304")
    assert code == 0
    assert "passenger" in out
    assert "MDS lifter" in out


def test_lookup_without_a_vehicle_stays_generic(capsys):
    code, out = run(capsys, "lookup", "P0304")
    assert code == 0
    assert "MDS" not in out


def test_vin_command_decodes_an_argument(capsys):
    code, out = run(capsys, "--json", "vin", CHARGER_VIN)
    payload = json.loads(out)
    assert code == 0
    assert payload["model_year"] == 2006
    assert payload["profile"] == "charger-rt-2006"


def test_vin_command_reads_from_the_car(capsys):
    code, out = run(capsys, "--sim", "--sim-profile", "charger", "vin")
    assert code == 0
    assert CHARGER_VIN in out
    assert "Brampton" in out


def test_vin_command_reports_a_bad_vin(capsys):
    broken = CHARGER_VIN[:8] + "0" + CHARGER_VIN[9:]
    code, out = run(capsys, "vin", broken)
    assert code == 1
    assert "INVALID" in out or "check digit" in out
