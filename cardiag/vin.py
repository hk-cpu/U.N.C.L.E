"""VIN validation and decoding.

The structural parts of a VIN are defined by ISO 3779 and, for cars sold in
North America, by 49 CFR 565: the check digit, the model year code and the
world manufacturer identifier are all deterministic and are decoded properly
here. Everything past that - trim, engine, plant - is manufacturer-assigned and
undocumented, so only a small hand-verified table is used and anything outside
it is reported as unknown rather than guessed at.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass

VIN_LENGTH = 17

#: I, O and Q are never used in a VIN, so their presence means a typo.
INVALID_LETTERS = set("IOQ")

#: Letter values for the check digit calculation (49 CFR 565.15).
_TRANSLITERATION = {
    "A": 1, "B": 2, "C": 3, "D": 4, "E": 5, "F": 6, "G": 7, "H": 8,
    "J": 1, "K": 2, "L": 3, "M": 4, "N": 5, "P": 7, "R": 9,
    "S": 2, "T": 3, "U": 4, "V": 5, "W": 6, "X": 7, "Y": 8, "Z": 9,
}

_WEIGHTS = (8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2)

#: Position 10, cycling every 30 years. A=1980 and the sequence skips I,O,Q,U,Z.
_YEAR_CODES = "ABCDEFGHJKLMNPRSTVWXY123456789"

#: World manufacturer identifiers we can name. Deliberately short: a wrong
#: manufacturer is worse than an unknown one.
WMI = {
    "1C3": "Chrysler (USA)",
    "1C4": "Chrysler (USA, multipurpose vehicle)",
    "1C6": "Chrysler (USA, truck)",
    "1B3": "Dodge (USA)",
    "1D4": "Dodge (USA, multipurpose vehicle)",
    "2A4": "Chrysler (Canada, multipurpose vehicle)",
    "2B3": "Dodge (Canada, passenger car)",
    "2C3": "Chrysler (Canada, passenger car)",
    "2D4": "Dodge (Canada, multipurpose vehicle)",
    "3C4": "Chrysler (Mexico)",
    "1G1": "Chevrolet (USA)",
    "1FA": "Ford (USA)",
    "1HG": "Honda (USA)",
    "JHM": "Honda (Japan)",
    "JTD": "Toyota (Japan)",
    "WBA": "BMW (Germany)",
    "WDB": "Mercedes-Benz (Germany)",
    "WVW": "Volkswagen (Germany)",
}

#: Position 11, assembly plant. Only decoded for manufacturers we have a table
#: for, since the letters mean different plants for every maker.
CHRYSLER_PLANTS = {
    "H": "Brampton, Ontario",
    "C": "Jefferson North, Detroit",
    "D": "Belvidere, Illinois",
    "N": "Sterling Heights, Michigan",
    "R": "Windsor, Ontario",
    "S": "Dodge City, Warren, Michigan",
}

#: Position 8, engine. Verified for the 2005-2010 LX cars (Charger, 300,
#: Magnum, Challenger) only - Chrysler reused these letters elsewhere.
CHRYSLER_LX_ENGINES = {
    "H": "5.7 L HEMI V8",
    "W": "6.1 L HEMI V8 (SRT8)",
    "G": "3.5 L V6 high output",
    "V": "2.7 L V6",
}

_CHRYSLER_WMI_PREFIXES = ("1C", "1B", "1D", "2A", "2B", "2C", "2D", "3C")


@dataclass
class VinInfo:
    """What can be established from a VIN with confidence."""

    vin: str
    valid_format: bool
    check_digit_ok: bool
    wmi: str
    manufacturer: str | None
    model_year: int | None
    plant: str | None
    engine: str | None
    serial: str
    problems: list[str]

    @property
    def trustworthy(self) -> bool:
        return self.valid_format and self.check_digit_ok

    def to_dict(self) -> dict:
        return {
            "vin": self.vin,
            "valid_format": self.valid_format,
            "check_digit_ok": self.check_digit_ok,
            "wmi": self.wmi,
            "manufacturer": self.manufacturer,
            "model_year": self.model_year,
            "plant": self.plant,
            "engine": self.engine,
            "serial": self.serial,
            "problems": list(self.problems),
        }


def normalise(vin: str) -> str:
    return "".join(vin.split()).upper()


def check_digit(vin: str) -> str | None:
    """Compute position 9. Returns ``None`` if the VIN cannot be transliterated."""
    vin = normalise(vin)
    if len(vin) != VIN_LENGTH:
        return None

    total = 0
    for character, weight in zip(vin, _WEIGHTS):
        if character.isdigit():
            value = int(character)
        elif character in _TRANSLITERATION:
            value = _TRANSLITERATION[character]
        else:
            return None
        total += value * weight

    remainder = total % 11
    return "X" if remainder == 10 else str(remainder)


def model_year(code: str, now: int | None = None) -> int | None:
    """Decode position 10.

    The code repeats every 30 years, so the most recent year that is not in the
    future is chosen - which is right for any car still on the road.
    """
    code = code.upper()
    if code not in _YEAR_CODES:
        return None

    index = _YEAR_CODES.index(code)
    current = now or datetime.date.today().year
    year = 1980 + index
    while year + 30 <= current + 1:
        year += 30
    return year


def decode(vin: str, now: int | None = None) -> VinInfo:
    """Decode what is decodable, and say plainly what is not."""
    vin = normalise(vin)
    problems: list[str] = []

    if len(vin) != VIN_LENGTH:
        problems.append(f"a VIN is 17 characters; this one is {len(vin)}")
    for letter in sorted(set(vin) & INVALID_LETTERS):
        problems.append(f"{letter!r} never appears in a VIN - it is probably a misread")
    if not vin.isalnum():
        problems.append("a VIN contains only letters and digits")

    valid_format = not problems

    expected = check_digit(vin) if valid_format else None
    check_ok = bool(expected is not None and vin[8] == expected)
    if valid_format and not check_ok:
        problems.append(
            f"check digit is {vin[8]!r} but should be {expected!r} - "
            "the VIN was probably mistyped"
        )

    wmi = vin[:3] if len(vin) >= 3 else vin
    manufacturer = WMI.get(wmi)

    year = model_year(vin[9], now=now) if len(vin) > 9 else None

    plant = None
    engine = None
    if any(wmi.startswith(prefix) for prefix in _CHRYSLER_WMI_PREFIXES):
        if len(vin) > 10:
            plant = CHRYSLER_PLANTS.get(vin[10])
        if len(vin) > 7:
            engine = CHRYSLER_LX_ENGINES.get(vin[7])

    return VinInfo(
        vin=vin,
        valid_format=valid_format,
        check_digit_ok=check_ok,
        wmi=wmi,
        manufacturer=manufacturer,
        model_year=year,
        plant=plant,
        engine=engine,
        serial=vin[11:] if len(vin) == VIN_LENGTH else "",
        problems=problems,
    )
