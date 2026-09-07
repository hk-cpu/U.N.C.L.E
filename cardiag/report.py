"""Health reports: gather everything the ECU knows and say what it means.

The rules here are deliberately conservative. They flag readings that are
outside what a healthy engine produces and explain why that matters; they do
not attempt to name a single culprit, because the same symptom usually has
several plausible causes.
"""

from __future__ import annotations

import re
import statistics
import time
from dataclasses import dataclass, field
from typing import Any

from . import dtc, mode06, vehicles
from .session import Reading, Session, VehicleInfo
from .pids import MonitorStatus
from .vehicles import VehicleProfile

#: A monitor this close to its own failure limit is worth mentioning even
#: though the ECU still calls it a pass.
HEADROOM_WARNING = 0.85

#: Misfire counters are noisy at low numbers, so a group has to clear this
#: before a difference between cylinders means anything.
MISFIRE_FLOOR = 10

#: Misfire codes name their cylinder in the last two digits: P0301 -> 1.
_MISFIRE = re.compile(r"^P030([1-9A-C])$")

#: Ordered worst-first, matching dtc.SEVERITY_ORDER.
FINDING_SEVERITY = ("critical", "serious", "moderate", "advisory", "info")


@dataclass
class Finding:
    """Something worth telling the owner about."""

    severity: str
    title: str
    detail: str
    suggestion: str = ""

    def to_dict(self) -> dict:
        return {
            "severity": self.severity,
            "title": self.title,
            "detail": self.detail,
            "suggestion": self.suggestion,
        }


@dataclass
class HealthReport:
    vehicle: VehicleInfo
    status: MonitorStatus | None
    codes: list[dtc.Dtc]
    readings: dict[str, Reading]
    findings: list[Finding] = field(default_factory=list)
    freeze_frame: dict[str, Any] = field(default_factory=dict)
    profile: VehicleProfile | None = None
    #: Mode 06 results: what the monitors actually measured.
    monitor_tests: list[mode06.TestResult] = field(default_factory=list)
    misfire_counts: dict[int, int] = field(default_factory=dict)
    calibration: dict[str, list[str]] = field(default_factory=dict)
    generated_at: float = field(default_factory=time.time)

    @property
    def thresholds(self) -> vehicles.Thresholds:
        return self.profile.thresholds if self.profile else vehicles.Thresholds()

    @property
    def worst_severity(self) -> str:
        for level in FINDING_SEVERITY:
            if any(finding.severity == level for finding in self.findings):
                return level
        return "info"

    @property
    def headline(self) -> str:
        if self.status and self.status.mil_on:
            return "Check-engine light is ON"
        if any(finding.severity in ("critical", "serious") for finding in self.findings):
            return "No warning light, but something needs attention"
        if self.codes:
            return "No warning light; stored codes present"
        return "No faults found"

    def to_dict(self) -> dict:
        return {
            "generated_at": self.generated_at,
            "headline": self.headline,
            "worst_severity": self.worst_severity,
            "vehicle": {
                "vin": self.vehicle.vin,
                "ecu_name": self.vehicle.ecu_name,
                "fuel_type": self.vehicle.fuel_type,
                "protocol": self.vehicle.protocol,
                "adapter": self.vehicle.adapter,
                "battery_voltage": self.vehicle.battery_voltage,
            },
            "profile": self.profile.to_dict() if self.profile else None,
            "status": status_dict(self.status),
            "codes": [item.to_dict() for item in self.codes],
            "readings": {
                name: {
                    "description": reading.pid.description,
                    "value": plain_value(reading.value),
                    "unit": reading.pid.unit,
                    "formatted": reading.pid.format(reading.value),
                }
                for name, reading in self.readings.items()
            },
            # Each entry carries its own label so consumers do not have to map
            # PID names back to descriptions themselves.
            "freeze_frame": {
                key: {
                    "description": (
                        value.pid.description if isinstance(value, Reading)
                        else key.replace("_", " ")
                    ),
                    "formatted": (
                        value.pid.format(value.value) if isinstance(value, Reading)
                        else str(value)
                    ),
                }
                for key, value in self.freeze_frame.items()
            },
            "monitor_tests": [test.to_dict() for test in self.monitor_tests],
            "misfire_counts": {str(k): v for k, v in self.misfire_counts.items()},
            "calibration": dict(self.calibration),
            "findings": [finding.to_dict() for finding in self.findings],
        }


def build(session: Session, include_freeze_frame: bool = True,
          profile: VehicleProfile | None = None,
          detect_profile: bool = True,
          include_monitors: bool = True) -> HealthReport:
    """Run a full scan and analyse the result.

    When ``profile`` is omitted the vehicle's VIN is used to pick one, so a car
    cardiag knows about gets model-specific advice without being asked. Pass
    ``detect_profile=False`` to force a purely generic report.

    ``include_monitors`` reads mode 06, which is what surfaces a developing
    fault before it sets a code. It costs a few seconds on a real adapter.
    """
    vehicle = session.vehicle_info()
    status = session.monitor_status()
    codes = session.read_dtcs()

    if profile is None and detect_profile and vehicle.vin:
        profile = vehicles.match_vin(vehicle.vin)

    readings = session.read_many(entry.pid for entry in session.live_pids())

    freeze: dict[str, Any] = {}
    if include_freeze_frame and codes:
        freeze = session.freeze_frame()

    tests: list[mode06.TestResult] = []
    misfires: dict[int, int] = {}
    calibration: dict[str, list[str]] = {}
    if include_monitors:
        tests = session.monitor_tests()
        misfires = {
            cylinder: test.raw_value
            for test in tests
            if (cylinder := mode06.misfire_cylinder(test.mid)) is not None
        }
        if not misfires:
            misfires = session.misfire_counts()
        calibration = session.calibration()

    report = HealthReport(
        vehicle=vehicle,
        status=status,
        codes=codes,
        readings=readings,
        freeze_frame=freeze,
        profile=profile,
        monitor_tests=tests,
        misfire_counts=misfires,
        calibration=calibration,
    )
    report.findings = analyse(report)
    return report


# ---------------------------------------------------------------------------
# Analysis rules
# ---------------------------------------------------------------------------

def analyse(report: HealthReport) -> list[Finding]:
    findings: list[Finding] = []

    findings.extend(_analyse_codes(report))
    findings.extend(_analyse_known_issues(report))
    findings.extend(_analyse_misfire_counts(report))
    findings.extend(_analyse_monitor_headroom(report))
    findings.extend(_analyse_monitors(report))
    findings.extend(_analyse_electrical(report))
    findings.extend(_analyse_fuel_trims(report))
    findings.extend(_analyse_temperatures(report))

    if not findings:
        findings.append(
            Finding(
                "info",
                "Nothing abnormal found",
                "No trouble codes, and every live reading sampled was inside its "
                "normal range.",
            )
        )

    findings.sort(key=lambda item: FINDING_SEVERITY.index(item.severity))
    return findings


def _analyse_codes(report: HealthReport) -> list[Finding]:
    findings = []
    for code in report.codes:
        suggestion = ""
        if code.causes:
            suggestion = "Most likely: " + "; ".join(code.causes[:3]) + "."
        elif code.generic_fallback:
            suggestion = (
                "This code is not in the generic table - look it up against your "
                "car's make and model for the specific meaning."
            )

        detail = f"{code.system}, {code.origin}."

        # A profile turns "cylinder 4" into somewhere you can put a spanner.
        located = locate_code(code.code, report.profile)
        if located:
            detail += f" {located}"
        if report.profile:
            note = report.profile.code_notes.get(code.code)
            if note:
                detail += f" {note}"

        if code.status == "pending":
            detail += (
                " Pending: the fault has been seen once but not confirmed, so no "
                "warning light yet."
            )
        elif code.status == "permanent":
            detail += (
                " Permanent: set by the ECU and clearable only by the ECU itself, "
                "after the fault stays away for several drive cycles."
            )

        findings.append(
            Finding(
                severity=code.severity,
                title=f"{code.code} - {code.description}",
                detail=detail,
                suggestion=suggestion,
            )
        )
    return findings


def locate_code(code: str, profile: VehicleProfile | None) -> str:
    """Say where on the engine a cylinder-specific code points."""
    if profile is None:
        return ""

    match = _MISFIRE.match(code)
    if not match:
        return ""

    cylinder = int(match.group(1), 16)
    if cylinder > profile.engine.cylinders:
        return ""

    sentence = f"On your engine, {profile.engine.locate(cylinder)}."
    if profile.engine.is_deactivated(cylinder):
        sentence += " It is also one of the cylinders MDS shuts down at cruise."
    return sentence


def _analyse_known_issues(report: HealthReport) -> list[Finding]:
    """Surface the failures this particular model is known for."""
    profile = report.profile
    if profile is None or not report.codes:
        return []

    codes = [item.code for item in report.codes]
    findings = []

    for issue in profile.issues_for(codes):
        triggering = sorted({code for code in codes if issue.matches(code)})
        suggestion = ""
        if issue.checks:
            suggestion = "How to check: " + " ".join(
                f"({index}) {check}" for index, check in enumerate(issue.checks, 1)
            )

        findings.append(Finding(
            severity=issue.severity,
            title=f"Known issue on this model: {issue.title}",
            detail=f"Raised by {', '.join(triggering)}. {issue.detail}",
            suggestion=suggestion,
        ))
    return findings


def _analyse_misfire_counts(report: HealthReport) -> list[Finding]:
    """Compare the per-cylinder misfire counters against each other.

    A raw count means little on its own - what matters is one cylinder, or one
    group of cylinders, drifting away from the rest. On an engine with cylinder
    deactivation the deactivated set shares hardware the others do not, so a
    skew along that boundary points somewhere specific.
    """
    counts = report.misfire_counts
    if len(counts) < 4:
        return []

    findings: list[Finding] = []
    values = list(counts.values())
    overall = statistics.median(values)

    # A single cylinder standing far above the rest.
    worst_cylinder = max(counts, key=lambda c: counts[c])
    worst = counts[worst_cylinder]
    others = [counts[c] for c in counts if c != worst_cylinder]
    others_median = statistics.median(others) if others else 0

    if worst >= MISFIRE_FLOOR and worst > max(others_median * 4, others_median + 20):
        where = ""
        if report.profile:
            located = report.profile.engine.locate(worst_cylinder)
            where = f" On your engine, {located}."
        findings.append(Finding(
            "serious",
            f"Cylinder {worst_cylinder} is misfiring far more than the others "
            f"({worst} counts against a median of {others_median:.0f})",
            "The ECU counts misfires per cylinder whether or not it has set a "
            "code. One cylinder this far above its neighbours is a real fault, "
            f"not measurement noise.{where}",
            "Swap that cylinder's coil and plugs with a neighbour, clear the "
            "counters and re-check. If the count follows the parts, it was "
            "ignition; if it stays with the cylinder, look at the injector, then "
            "compression.",
        ))

    # A skew along the cylinder-deactivation boundary.
    profile = report.profile
    if profile and profile.engine.deactivated_cylinders:
        deactivated = set(profile.engine.deactivated_cylinders)
        grouped = [counts[c] for c in counts if c in deactivated]
        ungrouped = [counts[c] for c in counts if c not in deactivated]

        if len(grouped) >= 2 and len(ungrouped) >= 2:
            on_median = statistics.median(grouped)
            off_median = statistics.median(ungrouped)

            if (on_median >= MISFIRE_FLOOR
                    and on_median > max(off_median * 3, off_median + 10)):
                listed = ", ".join(str(c) for c in sorted(deactivated))
                findings.append(Finding(
                    "serious",
                    "Misfire counts are skewed towards the cylinder-deactivation set",
                    f"Cylinders {listed} are averaging {on_median:.0f} counts "
                    f"while the rest average {off_median:.0f}. Those four run "
                    "different lifters from the others, and this is the pattern "
                    "their wear produces - it shows up here well before a "
                    "misfire code is set.",
                    "Treat this as an early warning rather than an emergency. "
                    "Listen for a tick that rises with engine speed once warm, "
                    "and check the oil filter for metal glitter. Catching a "
                    "lifter before it takes the camshaft lobe with it is the "
                    "difference between a repair and an engine.",
                ))
    return findings


def _analyse_monitor_headroom(report: HealthReport) -> list[Finding]:
    """Flag monitors that still pass but are close to their own limit."""
    findings = []
    for test in report.monitor_tests:
        if test.passed is not True:
            continue
        headroom = test.headroom
        if headroom is None or headroom < HEADROOM_WARNING or not test.known_scaling:
            continue
        if mode06.misfire_cylinder(test.mid) is not None:
            continue        # covered in more useful detail above

        findings.append(Finding(
            "moderate",
            f"{test.monitor} is close to failing ({headroom * 100:.0f} % of its limit)",
            f"The ECU measured {test.format_value()} against {test.format_limits()}. "
            "It still counts as a pass, so there is no code, but there is very "
            "little margin left.",
            "Worth acting on before it trips: once it crosses, the warning light "
            "comes on and the car fails an emissions test.",
        ))
    return findings


def _analyse_monitors(report: HealthReport) -> list[Finding]:
    status = report.status
    if status is None:
        return []

    findings = []
    not_ready = status.not_ready

    if not_ready:
        readable = ", ".join(name.replace("_", " ") for name in not_ready)
        findings.append(
            Finding(
                severity="advisory" if status.emissions_ready else "moderate",
                title=f"{len(not_ready)} readiness monitor(s) incomplete",
                detail=(
                    f"These self-tests have not finished since the codes were last "
                    f"cleared: {readable}."
                ),
                suggestion=(
                    "Drive a mixed cycle - cold start, some town driving, then "
                    "15 minutes of steady motorway speed - and re-check. Most "
                    "emissions tests fail a car with more than one incomplete "
                    "monitor."
                ),
            )
        )

    if status.mil_on and not report.codes:
        findings.append(
            Finding(
                severity="moderate",
                title="Warning light is on but no codes were returned",
                detail=(
                    "The ECU reports the lamp is commanded on yet mode 03 returned "
                    "nothing. The fault is usually stored in a module other than the "
                    "engine ECU, which generic OBD-II cannot read."
                ),
                suggestion="A make-specific scan tool will see the other modules.",
            )
        )
    return findings


def _analyse_electrical(report: HealthReport) -> list[Finding]:
    voltage = None
    reading = report.readings.get("CONTROL_MODULE_VOLTAGE")
    if reading is not None:
        voltage = reading.value
    elif report.vehicle.battery_voltage is not None:
        voltage = report.vehicle.battery_voltage

    if voltage is None:
        return []

    limits = report.thresholds
    rpm_reading = report.readings.get("RPM")
    running = bool(rpm_reading and isinstance(rpm_reading.value, (int, float))
                   and rpm_reading.value > 400)

    if running and voltage < limits.charging_low:
        return [Finding(
            "serious",
            f"Charging voltage is low ({voltage:.1f} V)",
            "With the engine running the system should sit between 13.5 V and "
            "14.8 V. Below 13 V the alternator is not keeping up and the battery "
            "is being drained as you drive.",
            "Check the drive belt, then have the alternator output tested.",
        )]
    if voltage > limits.charging_high:
        return [Finding(
            "serious",
            f"Charging voltage is high ({voltage:.1f} V)",
            "Above 15 V the regulator is overcharging, which boils the battery "
            "and can damage electronics.",
            "Have the alternator's voltage regulator checked.",
        )]
    if not running and voltage < 12.2:
        return [Finding(
            "moderate",
            f"Battery voltage is low ({voltage:.1f} V)",
            "A rested, healthy 12 V battery reads about 12.6 V. 12.2 V is roughly "
            "half charged.",
            "Charge it and have the battery load-tested before winter.",
        )]
    return []


def _analyse_fuel_trims(report: HealthReport) -> list[Finding]:
    findings = []
    banks = [
        ("bank 1", "SHORT_FUEL_TRIM_1", "LONG_FUEL_TRIM_1"),
        ("bank 2", "SHORT_FUEL_TRIM_2", "LONG_FUEL_TRIM_2"),
    ]

    for label, short_key, long_key in banks:
        short = report.readings.get(short_key)
        long = report.readings.get(long_key)
        if long is None:
            continue

        total = long.value + (short.value if short else 0.0)
        if total > 15.0:
            findings.append(Finding(
                "moderate",
                f"Fuel trims are high on {label} (+{total:.0f} %)",
                "The ECU is adding well over 10 % extra fuel to hold the mixture "
                "correct, which means it is seeing more air than it expects or "
                "getting less fuel than it commands. Sustained lean running "
                "raises combustion temperatures.",
                "Check for a vacuum leak first - intake gaskets, PCV and brake "
                "servo hoses - then the MAF sensor and fuel filter.",
            ))
        elif total < -15.0:
            findings.append(Finding(
                "moderate",
                f"Fuel trims are low on {label} ({total:.0f} %)",
                "The ECU is pulling fuel out to stop the mixture running rich. "
                "Prolonged rich running washes oil off the bores and can damage "
                "the catalytic converter.",
                "Check for a leaking injector, a stuck fuel pressure regulator, or "
                "a clogged air filter.",
            ))
    return findings


def _analyse_temperatures(report: HealthReport) -> list[Finding]:
    findings = []

    coolant = report.readings.get("COOLANT_TEMP")
    run_time = report.readings.get("RUN_TIME")

    limits = report.thresholds

    if coolant is not None and isinstance(coolant.value, (int, float)):
        if coolant.value > limits.coolant_critical:
            findings.append(Finding(
                "critical",
                f"Engine is overheating ({coolant.value} degC)",
                f"Coolant above {limits.coolant_critical:.0f} degC risks warping "
                "the head and destroying the head gasket.",
                "Stop driving. Let it cool, then check coolant level, the fan and "
                "the water pump before restarting.",
            ))
        elif coolant.value > limits.coolant_warning:
            findings.append(Finding(
                "serious",
                f"Coolant temperature is high ({coolant.value} degC)",
                f"This engine normally holds {limits.coolant_normal_low:.0f}-"
                f"{limits.coolant_normal_high:.0f} degC. Running hotter than that "
                "under normal load points at a cooling system problem.",
                "Check coolant level and that the radiator fan cuts in.",
            ))
        elif (run_time is not None and isinstance(run_time.value, (int, float))
              and run_time.value > 600
              and coolant.value < limits.coolant_normal_low - 10):
            findings.append(Finding(
                "moderate",
                f"Engine is not reaching operating temperature ({coolant.value} degC)",
                f"After {int(run_time.value / 60)} minutes of running the coolant is "
                f"still below {limits.coolant_normal_low - 10:.0f} degC. A thermostat "
                "stuck open costs fuel economy and accelerates engine wear, and "
                "stops the catalyst monitor ever completing.",
                "Replace the thermostat; this is the usual cause and is a cheap fix.",
            ))

    oil = report.readings.get("OIL_TEMP")
    if oil is not None and isinstance(oil.value, (int, float)) and oil.value > 130:
        findings.append(Finding(
            "serious",
            f"Oil temperature is high ({oil.value} degC)",
            "Above about 130 degC oil oxidises quickly and loses film strength.",
            "Check oil level and grade, and the oil cooler if one is fitted.",
        ))
    return findings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def status_dict(status: MonitorStatus | None) -> dict | None:
    if status is None:
        return None
    return {
        "mil_on": status.mil_on,
        "dtc_count": status.dtc_count,
        "compression_ignition": status.compression_ignition,
        "monitors": dict(status.monitors),
        "not_ready": status.not_ready,
        "emissions_ready": status.emissions_ready,
    }


def plain_value(value: Any) -> Any:
    """Convert a decoded value into something JSON can hold."""
    if isinstance(value, (int, float, str, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {key: plain_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [plain_value(item) for item in value]
    if isinstance(value, MonitorStatus):
        return status_dict(value)
    return str(value)
