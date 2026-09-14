"""Turn readings into sentences.

The rest of cardiag reports numbers and findings. This module says what they
*mean*, in the words you would use to another person: not "COOLANT_TEMP 89 degC"
but "warmed up and holding steady in its normal band".

Everything here is deterministic and offline. That is deliberate - the moment
you are standing over an engine in a car park is the moment your phone has no
signal, and an explanation that needs the internet is no explanation at all. A
language model can sit on top of this (see ``cardiag.assistant``), but it reads
these conclusions rather than forming its own.

The rule the whole module follows: never state a condition the data does not
support. A channel the car does not answer produces no sentence at all, rather
than a reassuring one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import vehicles
from .report import HealthReport

#: Worst first, matching report.FINDING_SEVERITY.
SEVERITY_ORDER = ("critical", "serious", "moderate", "advisory", "info", "good")

#: Below this the engine has not reached its thermostat and readings that
#: depend on closed-loop fuelling cannot be judged yet.
WARMUP_CEILING = 60.0

#: A fuel trim beyond this is worth naming; beyond twice it, worth worrying.
TRIM_NOTABLE = 10.0
TRIM_SERIOUS = 20.0

#: Engine speeds that separate "off", "idling" and "running".
IDLE_CEILING = 1100.0


@dataclass
class Condition:
    """One plain-language statement about one aspect of the car."""

    topic: str
    #: A few words for the card's heading: "Warmed up", "Charging".
    state: str
    #: The sentence a person reads.
    detail: str
    severity: str = "info"
    #: The numbers this sentence was drawn from, for anyone who wants them.
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "topic": self.topic,
            "state": self.state,
            "detail": self.detail,
            "severity": self.severity,
            "evidence": list(self.evidence),
        }


@dataclass
class Explanation:
    """What the car is doing, said out loud."""

    summary: str
    severity: str
    conditions: list[Condition] = field(default_factory=list)
    #: What to do next, worst finding first. Empty when nothing needs doing.
    actions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "summary": self.summary,
            "severity": self.severity,
            "conditions": [item.to_dict() for item in self.conditions],
            "actions": list(self.actions),
        }

    @property
    def narrative(self) -> str:
        """The whole thing as one block of prose, for speech or a log."""
        parts = [self.summary]
        parts.extend(item.detail for item in self.conditions)
        if self.actions:
            parts.append("Worth doing: " + self.actions[0])
        return " ".join(parts)


def _worst(severities: list[str]) -> str:
    for level in SEVERITY_ORDER:
        if level in severities:
            return level
    return "info"


def _overall(severities: list[str]) -> str:
    """The verdict for the car as a whole.

    A single condition can be "info" - nothing to judge yet, like fuel trims on
    a cold engine. But a car whose worst news is "nothing to judge yet" has
    nothing wrong with it, so the overall verdict reads as good rather than
    inheriting the neutral level of one card.
    """
    worst = _worst(severities)
    return "good" if worst == "info" else worst


def _value(readings: dict, name: str) -> float | None:
    """A numeric reading by name, or None if the car did not answer."""
    entry = readings.get(name)
    if entry is None:
        return None
    value = entry.get("value") if isinstance(entry, dict) else getattr(entry, "value", None)
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


# ---------------------------------------------------------------------------
# The individual explanations
# ---------------------------------------------------------------------------

def _explain_engine(readings: dict) -> Condition | None:
    rpm = _value(readings, "RPM")
    if rpm is None:
        return None

    speed = _value(readings, "SPEED")
    evidence = [f"{rpm:,.0f} rpm"]
    if speed is not None:
        evidence.append(f"{speed:.0f} km/h")

    if rpm < 1:
        return Condition("Engine", "Not running",
                         "The engine is not turning. The ignition is on, which is "
                         "why the adapter can still talk to it.",
                         "info", evidence)
    if rpm < IDLE_CEILING:
        return Condition("Engine", "Idling",
                         f"Idling at {rpm:,.0f} rpm, which is where this V8 should "
                         "sit when it is warm and in neutral.",
                         "good", evidence)
    if speed is not None and speed < 1:
        return Condition("Engine", "Revving",
                         f"Running at {rpm:,.0f} rpm while stationary.",
                         "info", evidence)
    return Condition("Engine", "Under way",
                     f"Running at {rpm:,.0f} rpm"
                     + (f" at {speed:.0f} km/h." if speed is not None else "."),
                     "good", evidence)


def _explain_cooling(readings: dict, limits: vehicles.Thresholds) -> Condition | None:
    coolant = _value(readings, "COOLANT_TEMP")
    if coolant is None:
        return None

    evidence = [f"coolant {coolant:.0f} degC"]
    oil = _value(readings, "OIL_TEMP")
    if oil is not None:
        evidence.append(f"oil {oil:.0f} degC")

    if coolant >= limits.coolant_critical:
        return Condition("Cooling", "Overheating",
                         f"Coolant is at {coolant:.0f} degC. That is past the point "
                         "where damage starts. Stop the car and let it cool before "
                         "driving further.",
                         "critical", evidence)
    if coolant >= limits.coolant_warning:
        return Condition("Cooling", "Running hot",
                         f"Coolant is at {coolant:.0f} degC, above the {limits.coolant_warning:.0f} degC "
                         "line where the cooling system is worth investigating - "
                         "fans, thermostat, radiator, water pump.",
                         "serious", evidence)
    if coolant < WARMUP_CEILING:
        return Condition("Cooling", "Warming up",
                         f"Coolant is at {coolant:.0f} degC and still climbing towards "
                         "its thermostat. Fuel trims and emissions monitors mean "
                         "little until it is warm.",
                         "info", evidence)
    if coolant < limits.coolant_normal_low:
        return Condition("Cooling", "Below normal",
                         f"Coolant is at {coolant:.0f} degC, under the "
                         f"{limits.coolant_normal_low:.0f} degC this engine normally holds. "
                         "A thermostat stuck open will do this.",
                         "advisory", evidence)
    return Condition("Cooling", "Normal",
                     f"Coolant is at {coolant:.0f} degC, in the band this engine "
                     "should hold once warm.",
                     "good", evidence)


def _explain_fuelling(readings: dict, profile) -> Condition | None:
    banks = {
        1: _value(readings, "LONG_FUEL_TRIM_1"),
        2: _value(readings, "LONG_FUEL_TRIM_2"),
    }
    present = {bank: trim for bank, trim in banks.items() if trim is not None}
    if not present:
        return None

    evidence = [f"bank {bank} long trim {trim:+.1f}%" for bank, trim in present.items()]
    worst_bank, worst_trim = max(present.items(), key=lambda item: abs(item[1]))
    side = ""
    if profile is not None:
        where = profile.engine.bank_side.get(worst_bank)
        side = f" ({where} side)" if where else ""

    if abs(worst_trim) >= TRIM_SERIOUS:
        direction = "lean" if worst_trim > 0 else "rich"
        return Condition("Fuelling", f"Bank {worst_bank} {direction}",
                         f"Bank {worst_bank}{side} is {worst_trim:+.0f}% off. The ECU is "
                         f"having to correct hard, which points at "
                         + ("an air leak, a weak fuel supply or a lying oxygen sensor."
                            if worst_trim > 0 else
                            "a leaking injector, high fuel pressure or a rich-reading sensor."),
                         "serious", evidence)
    if abs(worst_trim) >= TRIM_NOTABLE:
        return Condition("Fuelling", f"Bank {worst_bank} drifting",
                         f"Bank {worst_bank}{side} is {worst_trim:+.0f}%. Not a fault yet, "
                         "but it is the direction a developing leak moves in.",
                         "advisory", evidence)

    both = " Both banks agree, so nothing is one-sided." if len(present) > 1 else ""
    return Condition("Fuelling", "Correct",
                     "The mixture needs only small corrections." + both,
                     "good", evidence)


def _explain_electrical(readings: dict, limits: vehicles.Thresholds,
                        rpm: float | None) -> Condition | None:
    volts = _value(readings, "CONTROL_MODULE_VOLTAGE")
    if volts is None:
        return None

    evidence = [f"{volts:.2f} V"]
    running = rpm is not None and rpm > 1

    if not running:
        if volts < 12.0:
            return Condition("Electrical", "Battery low",
                             f"{volts:.1f} V with the engine off. A healthy battery "
                             "rests above 12.4 V; this one is flat enough to be worth "
                             "charging and testing.",
                             "serious", evidence)
        return Condition("Electrical", "Battery resting",
                         f"{volts:.1f} V with the engine off, which is a normal "
                         "resting voltage.",
                         "good", evidence)

    if volts < limits.charging_low:
        return Condition("Electrical", "Not charging",
                         f"Only {volts:.1f} V with the engine running. The alternator "
                         "should be holding above {:.0f} V - check the belt, the "
                         "alternator and its connections.".format(limits.charging_low),
                         "serious", evidence)
    if volts > limits.charging_high:
        return Condition("Electrical", "Overcharging",
                         f"{volts:.1f} V is higher than the charging system should go. "
                         "A failing regulator will boil a battery dry.",
                         "serious", evidence)
    return Condition("Electrical", "Charging",
                     f"{volts:.1f} V with the engine running - the alternator is "
                     "doing its job.",
                     "good", evidence)


def _explain_faults(report: HealthReport) -> Condition:
    mil = bool(report.status and report.status.mil_on)
    codes = report.codes
    evidence = [code.code for code in codes]

    if mil and codes:
        listed = ", ".join(code.code for code in codes[:3])
        more = f" and {len(codes) - 3} more" if len(codes) > 3 else ""
        return Condition("Faults", "Check-engine light on",
                         f"The light is on and the ECU has stored {len(codes)} "
                         f"code{'s' if len(codes) != 1 else ''}: {listed}{more}.",
                         "serious", evidence)
    if mil:
        return Condition("Faults", "Light on, no codes",
                         "The check-engine light is on but no codes are stored. "
                         "That usually means someone cleared them without fixing "
                         "the cause.",
                         "moderate", evidence)
    if codes:
        return Condition("Faults", "Stored codes, no light",
                         f"No warning light, but {len(codes)} code"
                         f"{'s are' if len(codes) != 1 else ' is'} stored: "
                         + ", ".join(code.code for code in codes[:3]) + ".",
                         "moderate", evidence)
    return Condition("Faults", "None stored",
                     "No trouble codes and no warning light.",
                     "good", evidence)


def _explain_readiness(report: HealthReport) -> Condition | None:
    status = report.status
    if status is None or not status.monitors:
        return None

    incomplete = sorted(status.not_ready)
    if not incomplete:
        return Condition("Readiness", "Complete",
                         "Every emissions monitor has run. The car would pass an "
                         "inspection on readiness.",
                         "good", [])

    listed = ", ".join(name.replace("_", " ") for name in incomplete[:4])
    # Regulators allow one incomplete monitor, so one is a note and two is a
    # reason the car will be turned away.
    severity = "advisory" if status.emissions_ready else "moderate"
    return Condition("Readiness", f"{len(incomplete)} not run",
                     f"{len(incomplete)} emissions monitor"
                     f"{'s have' if len(incomplete) != 1 else ' has'} not finished: "
                     f"{listed}. "
                     + ("That is within what an inspection tolerates."
                        if status.emissions_ready else
                        "The car needs more driving before it will pass an inspection."),
                     severity, incomplete)


def _explain_wear(report: HealthReport) -> Condition | None:
    """What the monitors measured, which is where a fault shows up first.

    A car with no codes can still be wearing out. Mode 06 is the only place
    that is visible, so this is the one condition worth stating even when
    everything else reads clean - saying "healthy" over the top of a developing
    misfire would be the most misleading thing this module could do.
    """
    wear = [finding for finding in report.findings
            if "misfir" in finding.title.lower()
            or "close to failing" in finding.title.lower()]
    if not wear:
        if report.monitor_tests:
            return Condition("Wear", "Nothing developing",
                             "The on-board monitors are measuring comfortably inside "
                             "their limits, and no cylinder stands out.",
                             "good", [])
        return None

    worst = min(wear, key=lambda f: SEVERITY_ORDER.index(f.severity))
    evidence = [f"cylinder {cyl}: {count}"
                for cyl, count in sorted(report.misfire_counts.items())]

    extra = (f" There {'are' if len(wear) > 2 else 'is'} {len(wear) - 1} other "
             f"reading{'s' if len(wear) > 2 else ''} pointing the same way."
             if len(wear) > 1 else "")

    # Only the codeless case is an early warning. On a car that has already set
    # a code, the monitors are corroborating it, and saying "no code yet" would
    # contradict the fault card sitting next to this one.
    if report.codes:
        opening = "The monitors back up the stored codes:"
        closing = ""
    else:
        opening = "No code has been set for this yet, but the monitors show it:"
        closing = (" This is the kind of thing that becomes a breakdown if it "
                   "is left.")

    return Condition("Wear", "Developing",
                     f"{opening} {worst.title}.{extra}{closing}",
                     worst.severity, evidence)


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

def from_report(report: HealthReport) -> Explanation:
    """Explain a full health report."""
    readings = {
        name: {"value": reading.value}
        for name, reading in report.readings.items()
    }
    limits = report.thresholds
    rpm = _value(readings, "RPM")

    conditions = [
        item for item in (
            _explain_faults(report),
            _explain_engine(readings),
            _explain_cooling(readings, limits),
            _explain_fuelling(readings, report.profile),
            _explain_electrical(readings, limits, rpm),
            _explain_wear(report),
            _explain_readiness(report),
        ) if item is not None
    ]

    actions = [
        finding.suggestion or finding.title
        for finding in report.findings
        if finding.severity in ("critical", "serious", "moderate")
    ]

    return Explanation(
        summary=_summarise(report, conditions),
        severity=_overall([item.severity for item in conditions]),
        conditions=conditions,
        actions=actions[:5],
    )


def from_live(readings: dict, profile=None) -> Explanation:
    """Explain a live snapshot, where there is no scan to draw on.

    ``readings`` maps PID name to a dict carrying at least ``value`` - the shape
    the live API already returns.
    """
    limits = profile.thresholds if profile else vehicles.Thresholds()
    rpm = _value(readings, "RPM")

    conditions = [
        item for item in (
            _explain_engine(readings),
            _explain_cooling(readings, limits),
            _explain_fuelling(readings, profile),
            _explain_electrical(readings, limits, rpm),
        ) if item is not None
    ]

    if not conditions:
        return Explanation(
            summary="Not enough live data yet to say anything about the car.",
            severity="info",
        )

    severity = _overall([item.severity for item in conditions])
    return Explanation(
        summary=_live_summary(severity, conditions),
        severity=severity,
        conditions=conditions,
    )


def _summarise(report: HealthReport, conditions: list[Condition]) -> str:
    car = report.profile.name if report.profile else "The car"
    # The findings carry analysis no single live reading shows - a misfire
    # skew, a catalyst near its limit. A summary drawn only from the live
    # conditions would call such a car healthy, which is the one thing this
    # module must never do.
    worst = _worst([item.severity for item in conditions]
                   + [finding.severity for finding in report.findings])

    if worst == "critical":
        return f"{car} has a fault that needs attention before you drive it."
    if worst == "serious":
        if not report.codes:
            return (f"{car} has no stored codes, but the monitors show something "
                    "developing.")
        return f"{car} is running, but something needs looking at."
    if report.status and report.status.mil_on:
        return f"{car} has its check-engine light on."
    if report.codes:
        return f"{car} has stored codes but nothing urgent in the live data."
    if worst in ("moderate", "advisory"):
        return f"{car} is broadly healthy, with something worth keeping an eye on."
    return f"{car} looks healthy. Nothing in this scan needs action."


def _live_summary(severity: str, conditions: list[Condition]) -> str:
    if severity in ("critical", "serious"):
        worst = next(item for item in conditions if item.severity == severity)
        return f"{worst.topic} needs attention: {worst.state.lower()}."
    if severity == "advisory":
        return "Running, with something drifting away from normal."
    return "Everything being watched is in its normal range."
