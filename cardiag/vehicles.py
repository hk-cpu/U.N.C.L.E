"""Vehicle profiles: what cardiag knows about a specific car.

Generic OBD-II tells you a cylinder number and a bank number. A profile turns
that into something you can act on - which side of the engine to open, whether
that cylinder is one the engine shuts down at cruise, how many plugs it has -
and adds the failure modes that particular model is known for.

Everything here is model-specific knowledge, kept separate from the SAE tables
in :mod:`cardiag.dtc` so the two are never confused. Where a claim is a
tendency rather than a specification it is worded as one.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import vin as vin_module


@dataclass(frozen=True)
class EngineLayout:
    """The physical arrangement, used to locate a fault from a code."""

    name: str
    cylinders: int
    #: Cylinder number -> bank number, as the ECU counts banks.
    cylinder_bank: dict[int, int]
    #: Bank number -> where it sits when you are looking at the engine.
    bank_side: dict[int, str]
    firing_order: tuple[int, ...] = ()
    #: Cylinders shut down by a displacement-on-demand system, if fitted.
    deactivated_cylinders: tuple[int, ...] = ()
    plugs_per_cylinder: int = 1
    coils_per_cylinder: int = 1

    @property
    def total_plugs(self) -> int:
        return self.cylinders * self.plugs_per_cylinder

    def bank_of(self, cylinder: int) -> int | None:
        return self.cylinder_bank.get(cylinder)

    def side_of(self, cylinder: int) -> str | None:
        bank = self.bank_of(cylinder)
        return self.bank_side.get(bank) if bank else None

    def is_deactivated(self, cylinder: int) -> bool:
        return cylinder in self.deactivated_cylinders

    def locate(self, cylinder: int) -> str:
        """A sentence telling you where to look for a given cylinder."""
        bank = self.bank_of(cylinder)
        if bank is None:
            return f"cylinder {cylinder}"

        where = f"cylinder {cylinder} is on bank {bank}"
        side = self.bank_side.get(bank)
        if side:
            where += f", the {side} side"
        if self.plugs_per_cylinder > 1:
            where += f", and has {self.plugs_per_cylinder} spark plugs"
        return where


@dataclass(frozen=True)
class Step:
    """One step of a guided procedure."""

    action: str
    #: What a good result looks like, so the step has a pass/fail.
    expect: str = ""
    #: Live PIDs worth watching while doing it.
    watch: tuple[str, ...] = ()
    #: Anything that can hurt you or the car if done carelessly.
    caution: str = ""

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "expect": self.expect,
            "watch": list(self.watch),
            "caution": self.caution,
        }


@dataclass(frozen=True)
class KnownIssue:
    """A failure this model is known for, and how to tell if it is yours."""

    title: str
    detail: str
    #: Short identifier so the CLI and UI can name one issue.
    key: str = ""
    #: Trouble codes that make this issue a live suspect.
    codes: tuple[str, ...] = ()
    #: Codes matched by prefix, for whole families such as misfires.
    code_prefixes: tuple[str, ...] = ()
    checks: tuple[str, ...] = ()
    #: An ordered diagnosis you can actually follow, where one exists.
    procedure: tuple[Step, ...] = ()
    severity: str = "moderate"

    def matches(self, code: str) -> bool:
        if code in self.codes:
            return True
        return bool(self.code_prefixes) and code.startswith(self.code_prefixes)


@dataclass(frozen=True)
class Thresholds:
    """Model-specific normal ranges, overriding the generic defaults."""

    coolant_normal_low: float = 80.0
    coolant_normal_high: float = 105.0
    coolant_warning: float = 110.0
    coolant_critical: float = 118.0
    charging_low: float = 13.0
    charging_high: float = 15.0


@dataclass(frozen=True)
class VehicleProfile:
    key: str
    name: str
    years: str
    engine: EngineLayout
    #: What the car should negotiate, so a mismatch is worth mentioning.
    expected_protocol: str = ""
    transmission: str = ""
    notes: tuple[str, ...] = ()
    known_issues: tuple[KnownIssue, ...] = ()
    #: Extra context for specific codes on this model.
    code_notes: dict[str, str] = field(default_factory=dict)
    thresholds: Thresholds = field(default_factory=Thresholds)
    #: VIN fragments that identify this model: (position, value) pairs.
    vin_signature: tuple[tuple[int, str], ...] = ()

    def issues_for(self, codes: list[str]) -> list[KnownIssue]:
        """Known issues that any of these codes points at, in order."""
        found: list[KnownIssue] = []
        for issue in self.known_issues:
            if any(issue.matches(code) for code in codes) and issue not in found:
                found.append(issue)
        return found

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "years": self.years,
            "engine": {
                "name": self.engine.name,
                "cylinders": self.engine.cylinders,
                "firing_order": list(self.engine.firing_order),
                "deactivated_cylinders": list(self.engine.deactivated_cylinders),
                "plugs_per_cylinder": self.engine.plugs_per_cylinder,
                "total_plugs": self.engine.total_plugs,
                "bank_side": dict(self.engine.bank_side),
            },
            "transmission": self.transmission,
            "expected_protocol": self.expected_protocol,
            "notes": list(self.notes),
            "known_issues": [
                {
                    "key": issue.key,
                    "title": issue.title,
                    "detail": issue.detail,
                    "codes": list(issue.codes),
                    "code_prefixes": list(issue.code_prefixes),
                    "checks": list(issue.checks),
                    "procedure": [step.to_dict() for step in issue.procedure],
                    "severity": issue.severity,
                }
                for issue in self.known_issues
            ],
        }


# ---------------------------------------------------------------------------
# 2006 Dodge Charger R/T - 5.7 L HEMI V8, LX platform
# ---------------------------------------------------------------------------

# Chrysler numbers its V8 cylinders with the odd bank on the left (driver's side
# in a left-hand-drive car) and the even bank on the right. Bank 1 is always the
# bank containing cylinder 1, so on this engine bank 1 is the driver's side.
HEMI_57 = EngineLayout(
    name="5.7 L HEMI V8",
    cylinders=8,
    cylinder_bank={1: 1, 3: 1, 5: 1, 7: 1, 2: 2, 4: 2, 6: 2, 8: 2},
    bank_side={1: "driver's (left)", 2: "passenger's (right)"},
    firing_order=(1, 8, 4, 3, 6, 5, 7, 2),
    # MDS drops the engine to four cylinders at light load.
    deactivated_cylinders=(1, 4, 6, 7),
    # The HEMI combustion chamber uses two plugs per cylinder - 16 in total.
    plugs_per_cylinder=2,
    coils_per_cylinder=1,
)

CHARGER_RT_2006 = VehicleProfile(
    key="charger-rt-2006",
    name="Dodge Charger R/T (5.7 L HEMI V8)",
    years="2006",
    engine=HEMI_57,
    expected_protocol="ISO 15765-4 CAN (11 bit, 500 kbaud)",
    transmission="NAG1 (W5A580) 5-speed automatic",
    notes=(
        "This engine has 16 spark plugs, two per cylinder. A misfire code names "
        "the cylinder, not which of its two plugs failed, so both get replaced "
        "together.",
        "MDS shuts down cylinders 1, 4, 6 and 7 at light cruise. Those four "
        "cylinders use different lifters from the other four and fail more "
        "often, so a misfire on one of them is worth treating differently.",
        "Bank 1 is the driver's side on this engine (cylinders 1, 3, 5, 7). "
        "Bank 2 is the passenger's side (2, 4, 6, 8).",
        "Firing order is 1-8-4-3-6-5-7-2.",
    ),
    known_issues=(
        KnownIssue(
            title="MDS lifter or camshaft lobe failure",
            key="mds-lifter",
            detail=(
                "The best known failure on this engine. The lifters for the four "
                "MDS cylinders (1, 4, 6, 7) can collapse or spall, taking the "
                "camshaft lobe with them. It usually starts as a tick that is "
                "loudest when the engine is warm, and turns into a misfire on one "
                "of those four cylinders. A misfire on 1, 4, 6 or 7 that does not "
                "move when you swap the coil and plugs points here."
            ),
            code_prefixes=("P030",),
            codes=("P0300", "P0301", "P0304", "P0306", "P0307", "P052A", "P052B"),
            checks=(
                "Note which cylinder is missing - is it one of 1, 4, 6, 7?",
                "Swap the coil with a neighbouring cylinder and clear the code. If "
                "the misfire stays on the same cylinder, it is not ignition.",
                "Listen for a tick that rises with engine speed, warm.",
                "Pull the oil filter and look for metal glitter, which means the "
                "camshaft is already being ground away.",
            ),
            procedure=(
                Step(
                    action="Read the per-cylinder misfire counters "
                           "('cardiag misfires').",
                    expect="Counts roughly even across all eight. If cylinders "
                           "1, 4, 6 and 7 stand out as a group, that is the MDS "
                           "set and this issue is live.",
                ),
                Step(
                    action="Warm the engine to operating temperature and listen "
                           "at the top of each bank with the bonnet up.",
                    expect="A steady tick that rises with engine speed and does "
                           "not quieten as it warms points at a lifter. A tick "
                           "that fades once warm is more likely a manifold leak.",
                    watch=("COOLANT_TEMP", "RPM"),
                    caution="Keep hands, sleeves and the probe well clear of the "
                            "belt and fans. Do not lean over a running engine.",
                ),
                Step(
                    action="Swap the coil and both plugs from the suspect "
                           "cylinder with a neighbouring cylinder, clear the "
                           "codes, then drive a cycle and re-read.",
                    expect="If the misfire moves with the parts it was ignition, "
                           "and this issue is not your problem. If it stays on "
                           "the same cylinder, it is mechanical.",
                    caution="Let the engine cool before pulling plugs. Aluminium "
                            "heads strip easily when hot.",
                ),
                Step(
                    action="Cut open the oil filter, or drain and inspect the "
                           "oil, looking for metal.",
                    expect="Clean oil means the camshaft is probably still "
                           "intact. Glitter or flakes means the lobe is already "
                           "wearing and the job just got much bigger.",
                ),
                Step(
                    action="Run a compression test on the suspect cylinder.",
                    expect="A cylinder well below its neighbours confirms the "
                           "valve is not opening properly - a collapsed lifter "
                           "or a wiped lobe.",
                ),
            ),
            severity="serious",
        ),
        KnownIssue(
            title="Broken exhaust manifold bolts",
            key="manifold-bolts",
            detail=(
                "Very common on the 5.7. The bolts fatigue and snap, usually at "
                "the ends of the manifold, letting exhaust escape ahead of the "
                "oxygen sensor. The upstream sensor then sees the extra oxygen "
                "and reads lean, so the ECU adds fuel it does not need - which is "
                "why this shows up as a lean code rather than an exhaust code."
            ),
            codes=("P0171", "P0174", "P2096", "P2098"),
            checks=(
                "Listen for a tick on cold start that quietens as the manifold "
                "expands and seals - the classic sign.",
                "Look for black soot streaks around the manifold-to-head joint.",
                "Compare bank 1 and bank 2 fuel trims: one bank much leaner than "
                "the other points at that side's manifold.",
            ),
            procedure=(
                Step(
                    action="Compare long term fuel trim on both banks with the "
                           "engine warm and idling.",
                    expect="Both banks within about 10 %. One bank much leaner "
                           "than the other is the tell - the leak is on that "
                           "bank's manifold.",
                    watch=("LONG_FUEL_TRIM_1", "LONG_FUEL_TRIM_2",
                           "SHORT_FUEL_TRIM_1", "SHORT_FUEL_TRIM_2"),
                ),
                Step(
                    action="Start the engine from stone cold and listen at each "
                           "manifold.",
                    expect="A tick that is loud cold and fades as the metal "
                           "expands and seals is a broken bolt or a blown "
                           "gasket. That fading is the classic signature.",
                    caution="Cold start only - the manifolds reach several "
                            "hundred degrees within minutes.",
                ),
                Step(
                    action="With the engine off and cool, inspect the "
                           "manifold-to-head joint on the lean bank.",
                    expect="Black soot streaks radiating from a bolt hole mean "
                           "that bolt has snapped and exhaust is escaping past "
                           "the flange.",
                    caution="Give it an hour after running. These get hot enough "
                            "to burn through a glove.",
                ),
                Step(
                    action="Count the bolts you can actually see and reach.",
                    expect="Knowing how many are broken and where decides "
                           "whether this is an afternoon or a machine shop - "
                           "the ends are the usual casualties, and a bolt that "
                           "snapped flush needs extracting.",
                ),
            ),
            severity="moderate",
        ),
        KnownIssue(
            title="Oil pressure sending unit failure",
            key="oil-pressure",
            detail=(
                "The sender fails far more often than the oil pump does on these "
                "engines, and gives a low or erratic reading with no other "
                "symptom. Confirm with a mechanical gauge before doing anything "
                "drastic - but do not simply ignore it, because the failure it "
                "imitates is the one that destroys the engine."
            ),
            codes=("P0520", "P0521", "P0522", "P0523"),
            checks=(
                "Check the oil level first.",
                "Fit a mechanical gauge to confirm the real pressure before "
                "condemning anything.",
            ),
            procedure=(
                Step(
                    action="Check the oil level on the dipstick before anything "
                           "else.",
                    expect="Between the marks. Low oil is a real cause of low "
                           "pressure and costs nothing to rule out.",
                ),
                Step(
                    action="Fit a mechanical oil pressure gauge in place of the "
                           "sender and read it at idle and at 2000 rpm, warm.",
                    expect="Healthy pressure at both. If the mechanical gauge "
                           "reads fine while the ECU reports low, the sender is "
                           "the fault and the engine is not in danger.",
                    watch=("RPM", "COOLANT_TEMP"),
                    caution="Do not keep driving on a genuine low-pressure "
                            "reading. If the mechanical gauge is also low, stop "
                            "and do not restart it - that is how bearings die.",
                ),
                Step(
                    action="If the mechanical reading is genuinely low, stop and "
                           "investigate the pump, pickup screen and bearing "
                           "clearances before running the engine again.",
                    expect="This is the one case on this list where continuing "
                           "to drive turns a repair into a replacement engine.",
                ),
            ),
            severity="serious",
        ),
        KnownIssue(
            title="Catalytic converter efficiency",
            key="catalyst",
            detail=(
                "With two banks this engine has two catalysts and two downstream "
                "sensors, so P0420 and P0430 are separate faults on opposite "
                "sides. Both are frequently caused by something upstream - a "
                "misfire or a rich mixture poisons the catalyst - so treat any "
                "misfire or fuel trim code as the thing to fix first."
            ),
            codes=("P0420", "P0430"),
            checks=(
                "Fix any misfire or lean/rich code before condemning a catalyst.",
                "Check the downstream sensor is actually lazy rather than the "
                "catalyst being dead.",
            ),
            severity="advisory",
        ),
        KnownIssue(
            title="Thermostat and cooling system",
            key="cooling",
            detail=(
                "A thermostat stuck open keeps the engine below its proper "
                "temperature, which costs fuel and stops the catalyst monitor ever "
                "completing. Water pump failure is also common by this mileage."
            ),
            codes=("P0128", "P0125", "P0116", "P0117", "P0118"),
            checks=(
                "Watch coolant temperature on the live dashboard from cold: it "
                "should climb steadily to around 90 degC and hold.",
                "Check for coolant weeping from the water pump weep hole.",
            ),
            severity="moderate",
        ),
        KnownIssue(
            title="NAG1 transmission faults",
            key="transmission",
            detail=(
                "The 5-speed automatic behind this engine stores its own codes. "
                "Generic OBD-II sees only the P07xx family; the transmission "
                "module holds far more detail that needs a Chrysler-capable tool."
            ),
            code_prefixes=("P07",),
            checks=(
                "Check fluid level and condition - burnt fluid smells like it "
                "sounds.",
                "A Chrysler-specific scan tool will read the TCM's own codes.",
            ),
            severity="moderate",
        ),
    ),
    code_notes={
        "P0301": "Cylinder 1 is an MDS cylinder - see the MDS lifter issue below.",
        "P0304": "Cylinder 4 is an MDS cylinder - see the MDS lifter issue below.",
        "P0306": "Cylinder 6 is an MDS cylinder - see the MDS lifter issue below.",
        "P0307": "Cylinder 7 is an MDS cylinder - see the MDS lifter issue below.",
        "P0171": "Bank 1 is the driver's side. On this engine a lean bank is very "
                 "often a cracked or leaking exhaust manifold rather than a "
                 "vacuum leak.",
        "P0174": "Bank 2 is the passenger's side. On this engine a lean bank is "
                 "very often a cracked or leaking exhaust manifold rather than a "
                 "vacuum leak.",
        "P0420": "Bank 1 catalyst - driver's side.",
        "P0430": "Bank 2 catalyst - passenger's side.",
        "P0520": "The oil pressure sender is a known failure on this engine, "
                 "and fails far more often than the oil pump does. Confirm "
                 "with a mechanical gauge before assuming the worst - but do "
                 "not simply ignore it either.",
        "P0700": "This is the transmission module asking for the warning "
                 "light, not a fault in itself. The NAG1 stores its own codes, "
                 "and generic OBD-II only sees the P07xx family - the detail "
                 "needs a Chrysler-capable tool.",
        "P2110": "Limp mode: the throttle is being held to a reduced opening. "
                 "Usually a dirty throttle body or a pedal position sensor on "
                 "this platform.",
    },
    thresholds=Thresholds(
        # The 5.7 runs deliberately warm - the mid to high 90s is normal, not a
        # fault. Sustained running above 110 degC in traffic is the point at
        # which the cooling system is worth investigating, so that is the
        # warning line rather than something higher.
        coolant_normal_low=85.0,
        coolant_normal_high=105.0,
        coolant_warning=110.0,
        coolant_critical=118.0,
    ),
    # Positions are 1-based, the way VINs are written. 2B3 is a Canadian-built
    # Dodge passenger car (these were made in Brampton), position 8 is the
    # engine code and position 10 the model year. Together these pin down a
    # 2006 Dodge car with the 5.7 HEMI, which is this one.
    vin_signature=((1, "2"), (2, "B"), (3, "3"), (8, "H"), (10, "6")),
)


PROFILES: dict[str, VehicleProfile] = {
    CHARGER_RT_2006.key: CHARGER_RT_2006,
}

#: Shorthand people actually type.
ALIASES = {
    "charger": CHARGER_RT_2006.key,
    "charger-rt": CHARGER_RT_2006.key,
    "hemi": CHARGER_RT_2006.key,
    "2006-charger": CHARGER_RT_2006.key,
}


def get(key: str) -> VehicleProfile | None:
    key = key.strip().lower()
    return PROFILES.get(ALIASES.get(key, key))


def match_vin(vin: str) -> VehicleProfile | None:
    """Pick a profile from a VIN, if one clearly matches.

    Matching is deliberately strict: a wrong profile would give confidently
    wrong advice, which is worse than no profile at all.
    """
    decoded = vin_module.decode(vin)
    if not decoded.trustworthy:
        return None

    for profile in PROFILES.values():
        if not profile.vin_signature:
            continue
        if all(decoded.vin[position - 1] == value
               for position, value in profile.vin_signature):
            return profile
    return None


def describe_choices() -> str:
    return ", ".join(sorted(PROFILES))
