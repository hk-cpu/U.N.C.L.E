"""VIN decoding tests, checked against VINs with known-correct check digits."""

import pytest

from cardiag import vin

#: A real-format 2006 Dodge Charger R/T VIN, and a well-known valid Honda VIN.
CHARGER = "2B3KA53H66H123456"
HONDA = "1HGCM82633A004352"


def test_check_digit_matches_known_good_vins():
    assert vin.check_digit(CHARGER) == CHARGER[8]
    assert vin.check_digit(HONDA) == HONDA[8]


def test_check_digit_rejects_a_transposition():
    # Swapping two characters must change the check digit; that is the point.
    swapped = CHARGER[:4] + CHARGER[5] + CHARGER[4] + CHARGER[6:]
    assert vin.check_digit(swapped) != swapped[8]


def test_check_digit_needs_a_full_length_vin():
    assert vin.check_digit("2B3KA53H6") is None


def test_check_digit_rejects_illegal_letters():
    assert vin.check_digit("IB3KA53H66H123456") is None


@pytest.mark.parametrize("code,year", [
    ("6", 2006),
    ("1", 2001),
    ("9", 2009),
    ("A", 2010),
    # 'Y' is 2000 or 2030; in 2026 only 2000 has happened.
    ("Y", 2000),
])
def test_model_year_codes(code, year):
    assert vin.model_year(code, now=2026) == year


def test_model_year_never_returns_a_future_year():
    for code in "ABCDEFGHJKLMNPRSTVWXY123456789":
        year = vin.model_year(code, now=2026)
        assert year is not None and year <= 2027


def test_model_year_rejects_letters_that_never_appear():
    for letter in "IOQUZ":
        assert vin.model_year(letter, now=2026) is None


def test_model_year_picks_the_most_recent_plausible_cycle():
    # 'A' is 1980, 2010 and 2040. In 2026 the right answer is 2010.
    assert vin.model_year("A", now=2026) == 2010
    # In 1995 the only one that has happened is 1980.
    assert vin.model_year("A", now=1995) == 1980


def test_decode_a_charger_vin():
    info = vin.decode(CHARGER, now=2026)
    assert info.trustworthy
    assert info.model_year == 2006
    assert info.engine == "5.7 L HEMI V8"
    assert info.plant == "Brampton, Ontario"
    assert info.manufacturer == "Dodge (Canada, passenger car)"
    assert info.problems == []


def test_decode_reports_a_bad_check_digit():
    broken = CHARGER[:8] + ("0" if CHARGER[8] != "0" else "1") + CHARGER[9:]
    info = vin.decode(broken)
    assert info.valid_format is True
    assert info.check_digit_ok is False
    assert info.trustworthy is False
    assert any("check digit" in problem for problem in info.problems)


def test_decode_reports_wrong_length():
    info = vin.decode("2B3KA53H6")
    assert info.valid_format is False
    assert any("17 characters" in problem for problem in info.problems)


def test_decode_flags_letters_that_never_appear_in_a_vin():
    info = vin.decode("2B3KA53H66H12345O")
    assert info.valid_format is False
    assert any("'O'" in problem for problem in info.problems)


def test_engine_code_is_only_decoded_for_manufacturers_we_have_a_table_for():
    # The Honda VIN has 'M' in the engine position; we must not pretend to know
    # what Honda means by it.
    assert vin.decode(HONDA).engine is None


def test_normalise_handles_spacing_and_case():
    assert vin.normalise(" 2b3ka53h 66h123456 ") == CHARGER
    assert vin.decode(" 2b3ka53h66h123456 ").trustworthy
