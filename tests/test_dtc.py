import pytest

from cardiag import dtc


def test_decode_pair_covers_all_four_letters():
    assert dtc.decode_pair(0x01, 0x43) == "P0143"
    assert dtc.decode_pair(0x41, 0x43) == "C0143"
    assert dtc.decode_pair(0x81, 0x43) == "B0143"
    assert dtc.decode_pair(0xC1, 0x43) == "U0143"


def test_decode_pair_keeps_hex_digits():
    assert dtc.decode_pair(0x0A, 0xBC) == "P0ABC"
    assert dtc.decode_pair(0x30, 0x00) == "P3000"


def test_decode_bytes_drops_padding():
    raw = bytes([0x01, 0x43, 0x00, 0x00, 0xC0, 0x11])
    assert dtc.decode_bytes(raw) == ["P0143", "U0011"]


def test_decode_bytes_ignores_a_trailing_odd_byte():
    assert dtc.decode_bytes(bytes([0x01, 0x43, 0x04])) == ["P0143"]


@pytest.mark.parametrize("code", ["P0420", "P0301", "U0100", "C0035", "B1234", "P3000"])
def test_encode_round_trips(code):
    assert dtc.decode_bytes(dtc.encode(code)) == [code]


def test_encode_rejects_nonsense():
    with pytest.raises(ValueError):
        dtc.encode("nope")
    with pytest.raises(ValueError):
        dtc.encode("P042")


def test_describe_known_code_carries_causes():
    described = dtc.describe("P0420")
    assert "Catalyst" in described.description
    assert described.generic_fallback is False
    assert described.causes
    assert described.system.startswith("Powertrain")


def test_describe_unknown_generic_code_falls_back_to_its_subsystem():
    described = dtc.describe("P0655")
    assert described.generic_fallback is True
    assert "computer output" in described.description


def test_describe_manufacturer_code_says_so():
    described = dtc.describe("P1234")
    assert described.origin == "manufacturer specific"
    assert "manufacturer specific" in described.description.lower()


def test_severity_ranking():
    assert dtc.severity_of("P0217") == "critical"     # overheating
    assert dtc.severity_of("P0301") == "serious"      # misfire
    assert dtc.severity_of("U0100") == "serious"      # module offline
    assert dtc.severity_of("C0035") == "serious"      # wheel speed sensor
    assert dtc.severity_of("P0420") == "advisory"     # emissions only
    assert dtc.severity_of("P0700") == "moderate"


def test_sorting_puts_the_worst_first():
    codes = [dtc.describe(code) for code in ("P0420", "P0217", "P0700")]
    codes.sort(key=dtc.sort_key)
    assert [item.code for item in codes] == ["P0217", "P0700", "P0420"]


def test_summarise_counts_each_status():
    codes = [
        dtc.describe("P0420", status="stored"),
        dtc.describe("P0128", status="pending"),
        dtc.describe("P0301", status="permanent"),
    ]
    summary = dtc.summarise(codes)
    assert "1 stored" in summary
    assert "1 pending" in summary
    assert "1 permanent" in summary
    assert "serious" in summary


def test_summarise_handles_a_clean_car():
    assert dtc.summarise([]) == "No trouble codes stored."


def test_describe_is_case_insensitive_and_trims():
    assert dtc.describe("  p0420 ").code == "P0420"
