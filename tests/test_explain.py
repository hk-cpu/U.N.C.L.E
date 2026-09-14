"""Explainer tests.

The thing being guarded here is not prose quality, it is honesty: the module
must not describe a car as fine when the data says otherwise, and must not
describe a channel the car never answered.
"""

import pytest

from cardiag import explain, report as report_module, vehicles
from cardiag.session import Session
from cardiag.transport import link_kind, link_label

CHARGER = vehicles.CHARGER_RT_2006


def explain_profile(name):
    with Session(f"sim://?profile={name}") as car:
        return explain.from_report(report_module.build(car))


# ---------------------------------------------------------------------------
# Whole-car explanations
# ---------------------------------------------------------------------------

def test_a_healthy_car_is_described_as_healthy():
    result = explain_profile("charger")
    assert result.severity in ("good", "info")
    assert "healthy" in result.summary
    assert not result.actions


def test_a_worn_car_is_never_called_healthy():
    """The regression this module exists to prevent.

    charger-wear has no codes and no warning light, but its mode 06 counters
    show a cylinder well above its neighbours and a catalyst near its limit.
    An explanation drawn only from the live readings called this car healthy
    while simultaneously listing repairs - the two must not disagree.
    """
    result = explain_profile("charger-wear")

    assert "healthy" not in result.summary.lower()
    assert result.severity in ("serious", "critical", "moderate")
    assert result.actions, "a car with findings must offer something to do"

    wear = next(item for item in result.conditions if item.topic == "Wear")
    assert wear.severity == "serious"
    assert "cylinder 4" in wear.detail.lower()
    assert "No code has been set" in wear.detail


def test_wear_does_not_claim_there_is_no_code_when_there_is_one():
    """The wear card sits beside the fault card; they must not disagree.

    charger-misfire has P0300 stored *and* skewed misfire counters. Describing
    the counters as an early warning with "no code has been set yet" flatly
    contradicts the codes listed one card over.
    """
    result = explain_profile("charger-misfire")
    wear = next(item for item in result.conditions if item.topic == "Wear")

    assert "No code has been set" not in wear.detail
    assert "back up the stored codes" in wear.detail


def test_a_finding_title_keeps_its_code_capitalised():
    result = explain_profile("charger-misfire")
    wear = next(item for item in result.conditions if item.topic == "Wear")
    assert "p0300" not in wear.detail, "a DTC is not a word to lower-case"


def test_the_summary_never_contradicts_the_actions():
    for name in ("charger", "charger-misfire", "charger-wear", "faulty", "default"):
        result = explain_profile(name)
        if result.actions:
            assert "Nothing in this scan needs action" not in result.summary, name


def test_a_car_with_the_light_on_says_so_first():
    result = explain_profile("charger-misfire")
    faults = next(item for item in result.conditions if item.topic == "Faults")
    assert faults.state == "Check-engine light on"
    assert "P0300" in faults.evidence


def test_a_lean_bank_names_the_side_of_the_engine():
    result = explain_profile("charger-misfire")
    fuelling = next(item for item in result.conditions if item.topic == "Fuelling")
    assert "Bank 2" in fuelling.state
    assert "passenger" in fuelling.detail
    assert fuelling.severity == "serious"


def test_the_narrative_reads_as_one_block():
    result = explain_profile("charger")
    assert result.summary in result.narrative
    assert len(result.narrative) > len(result.summary)


# ---------------------------------------------------------------------------
# Live snapshots, where the readings are all there is
# ---------------------------------------------------------------------------

def live(**values):
    return {name: {"value": value} for name, value in values.items()}


def test_nothing_is_said_about_a_channel_the_car_never_answered():
    result = explain.from_live(live(RPM=800.0))
    topics = {item.topic for item in result.conditions}
    assert topics == {"Engine"}
    # No coolant reading means no cooling verdict - not a reassuring one.
    assert "Cooling" not in topics


def test_no_data_at_all_says_so_rather_than_inventing_calm():
    result = explain.from_live({})
    assert not result.conditions
    assert "not enough" in result.summary.lower()


def test_an_overheating_engine_is_critical_and_says_to_stop():
    result = explain.from_live(live(RPM=2000.0, COOLANT_TEMP=120.0), CHARGER)
    cooling = next(item for item in result.conditions if item.topic == "Cooling")
    assert cooling.severity == "critical"
    assert "stop" in cooling.detail.lower()
    assert result.severity == "critical"


def test_the_hot_band_uses_the_profile_threshold_not_a_guess():
    hot = explain.from_live(live(RPM=2000.0, COOLANT_TEMP=112.0), CHARGER)
    assert next(c for c in hot.conditions if c.topic == "Cooling").severity == "serious"

    fine = explain.from_live(live(RPM=2000.0, COOLANT_TEMP=104.0), CHARGER)
    assert next(c for c in fine.conditions if c.topic == "Cooling").severity == "good"


def test_a_cold_engine_is_not_judged_on_its_trims():
    result = explain.from_live(live(RPM=900.0, COOLANT_TEMP=25.0), CHARGER)
    cooling = next(item for item in result.conditions if item.topic == "Cooling")
    assert cooling.state == "Warming up"
    assert cooling.severity == "info"


def test_voltage_is_read_differently_with_the_engine_stopped():
    running = explain.from_live(live(RPM=800.0, CONTROL_MODULE_VOLTAGE=12.2), CHARGER)
    assert next(c for c in running.conditions
                if c.topic == "Electrical").state == "Not charging"

    parked = explain.from_live(live(RPM=0.0, CONTROL_MODULE_VOLTAGE=12.2), CHARGER)
    assert next(c for c in parked.conditions
                if c.topic == "Electrical").state == "Battery resting"


def test_a_flat_battery_with_the_engine_off_is_still_worth_saying():
    result = explain.from_live(live(RPM=0.0, CONTROL_MODULE_VOLTAGE=11.4), CHARGER)
    electrical = next(item for item in result.conditions if item.topic == "Electrical")
    assert electrical.severity == "serious"


def test_overcharging_is_caught_as_well_as_undercharging():
    result = explain.from_live(live(RPM=2000.0, CONTROL_MODULE_VOLTAGE=15.6), CHARGER)
    assert next(c for c in result.conditions
                if c.topic == "Electrical").state == "Overcharging"


def test_a_stationary_engine_is_not_described_as_under_way():
    result = explain.from_live(live(RPM=3000.0, SPEED=0.0))
    assert next(c for c in result.conditions if c.topic == "Engine").state == "Revving"


def test_everything_normal_gets_a_calm_summary():
    result = explain.from_live(
        live(RPM=750.0, COOLANT_TEMP=90.0, CONTROL_MODULE_VOLTAGE=14.1,
             LONG_FUEL_TRIM_1=1.5, LONG_FUEL_TRIM_2=2.0), CHARGER)
    assert result.severity == "good"
    assert "normal range" in result.summary


def test_serialises_to_json():
    import json
    json.dumps(explain_profile("charger-misfire").to_dict())


# ---------------------------------------------------------------------------
# How the adapter is attached
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("url,description,expected", [
    ("tcp://192.168.0.10:35000", "", "wifi"),
    ("sim://", "", "simulated"),
    ("/dev/ttyUSB0", "CH340 USB Serial", "usb"),
    ("/dev/ttyACM0", "", "usb"),
    ("COM3", "Silicon Labs CP210x USB to UART Bridge", "usb"),
    ("/dev/rfcomm0", "", "bluetooth"),
    ("COM5", "Standard Serial over Bluetooth link", "bluetooth"),
    ("/dev/cu.OBDII", "", "bluetooth"),
    ("/dev/cu.usbserial-1420", "", "usb"),
    ("/dev/ttyS0", "", "serial"),
])
def test_link_kind_tells_the_three_apart(url, description, expected):
    assert link_kind(url, description) == expected


def test_a_bluetooth_port_is_not_mislabelled_usb_by_its_driver_text():
    # macOS lists a paired adapter with no "usb" anywhere; a cable always has it.
    assert link_kind("/dev/cu.OBDLink-SPP") == "bluetooth"
    assert link_kind("/dev/cu.usbmodem14201") == "usb"


def test_link_labels_are_human_readable():
    assert link_label("tcp://10.0.0.5:35000") == "WiFi"
    assert link_label("/dev/rfcomm0") == "Bluetooth"
