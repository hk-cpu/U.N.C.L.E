"""Tests for the read-only XDF bin viewer.

The viewer must decode exactly what the two files say and never more: the right
bytes, the right scaling, the stated byte order — and when it cannot parse an
equation or the address runs off the end, it says so rather than inventing a
number.
"""

import math

import pytest

from cardiag import xdf


# ---------------------------------------------------------------------------
# The equation evaluator — no eval(), a restricted grammar
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("equation, x, expected", [
    ("X", 10, 10),
    ("X*0.5", 10, 5),
    ("(X*0.5)-64", 128, 0),
    ("X*100/255", 255, 100),
    ("-X", 5, -5),
    ("X^2", 3, 9),
    ("X - 40", 60, 20),
])
def test_equations_evaluate(equation, x, expected):
    fn = xdf.compile_equation(equation)
    assert fn is not None
    assert fn(x) == pytest.approx(expected)


@pytest.mark.parametrize("equation", ["", "import os", "X; drop", "foo(X)", "X@2"])
def test_unsupported_equations_compile_to_none(equation):
    assert xdf.compile_equation(equation) is None


def test_an_unparsable_equation_leaves_the_raw_value():
    scaling = xdf.Scaling(equation="log(X)")
    assert not scaling.parsable
    assert scaling.apply(42) == 42          # the raw integer, untouched


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

SPARK_XDF = """<XDFFORMAT version="1.80">
  <XDFHEADER><BASEOFFSET offset="0x0" subtract="0" /></XDFHEADER>
  <XDFTABLE>
    <title>Spark Advance</title>
    <XDFAXIS id="x"><units>RPM</units>
      <LABEL index="0" value="800" /><LABEL index="1" value="1600" />
      <LABEL index="2" value="2400" />
    </XDFAXIS>
    <XDFAXIS id="y"><units>kPa</units>
      <LABEL index="0" value="30" /><LABEL index="1" value="100" />
    </XDFAXIS>
    <XDFAXIS id="z"><units>deg</units>
      <EMBEDDEDDATA mmedaddress="0x10" mmedelementsizebits="8"
                    mmedrowcount="2" mmedcolcount="3" mmedtypeflags="0x00" />
      <MATH equation="X*0.5" /><decimalpl>1</decimalpl>
    </XDFAXIS>
  </XDFTABLE>
  <XDFCONSTANT>
    <title>Rev Limiter</title><units>RPM</units>
    <EMBEDDEDDATA mmedaddress="0x20" mmedelementsizebits="16" mmedtypeflags="0x00" />
    <MATH equation="X" />
  </XDFCONSTANT>
</XDFFORMAT>
"""

# raw spark values *0.5 = 20,22,24 / 12,14,16; rev limiter 5600 at 0x20 (16-bit BE)
SPARK_BIN = bytearray(64)
SPARK_BIN[0x10:0x16] = bytes([40, 44, 48, 24, 28, 32])
SPARK_BIN[0x20:0x22] = (5600).to_bytes(2, "big")
SPARK_BIN = bytes(SPARK_BIN)


def test_parse_finds_tables_and_constants():
    definition = xdf.parse_xdf(SPARK_XDF)
    assert len(definition.tables) == 1
    assert len(definition.constants) == 1

    table = definition.tables[0]
    assert table.title == "Spark Advance"
    assert table.z.embedded.address == 0x10
    assert (table.z.embedded.rows, table.z.embedded.cols) == (2, 3)
    assert table.z.embedded.signed is False

    constant = definition.constants[0]
    assert constant.embedded.size_bits == 16


def test_a_doctype_is_refused():
    hostile = '<!DOCTYPE foo [<!ENTITY x "y">]>\n' + SPARK_XDF
    with pytest.raises(xdf.XdfError):
        xdf.parse_xdf(hostile)


def test_malformed_xml_is_an_error():
    with pytest.raises(xdf.XdfError):
        xdf.parse_xdf("<XDFFORMAT><XDFTABLE>")


def test_an_xdf_with_nothing_to_show_is_an_error():
    with pytest.raises(xdf.XdfError):
        xdf.parse_xdf('<XDFFORMAT version="1.80"></XDFFORMAT>')


# ---------------------------------------------------------------------------
# Reading values out of the bin
# ---------------------------------------------------------------------------

def test_a_table_decodes_with_its_scaling_and_axes():
    definition = xdf.parse_xdf(SPARK_XDF)
    view = xdf.view_table(definition, definition.tables[0], SPARK_BIN)

    assert view.cells == [[20.0, 22.0, 24.0], [12.0, 14.0, 16.0]]
    assert view.x_labels == ["800", "1600", "2400"]
    assert view.y_labels == ["30", "100"]
    assert view.equation_parsed is True


def test_a_constant_reads_big_endian_by_default():
    definition = xdf.parse_xdf(SPARK_XDF)
    grid = xdf.read_block(definition, definition.constants[0].embedded,
                          SPARK_BIN, definition.constants[0].scaling)
    assert grid[0][0] == 5600


def test_byte_order_changes_the_value():
    definition = xdf.parse_xdf(SPARK_XDF)
    little = xdf.read_block(definition, definition.constants[0].embedded,
                            SPARK_BIN, definition.constants[0].scaling,
                            big_endian=False)
    assert little[0][0] == int.from_bytes((5600).to_bytes(2, "big"), "little")


def test_signedness_follows_the_type_flag():
    signed_xdf = """<XDFFORMAT version="1.80"><XDFCONSTANT><title>s</title>
      <EMBEDDEDDATA mmedaddress="0x0" mmedelementsizebits="8" mmedtypeflags="0x01" />
      <MATH equation="X" /></XDFCONSTANT></XDFFORMAT>"""
    definition = xdf.parse_xdf(signed_xdf)
    grid = xdf.read_block(definition, definition.constants[0].embedded,
                          b"\xff", definition.constants[0].scaling)
    assert grid[0][0] == -1       # signed; unsigned would be 255


def test_an_address_past_the_end_is_an_error_not_a_guess():
    definition = xdf.parse_xdf(SPARK_XDF)
    with pytest.raises(xdf.XdfError):
        xdf.read_block(definition, definition.constants[0].embedded,
                       b"\x00\x00", definition.constants[0].scaling)


def test_division_by_zero_is_undefined_not_a_fake_zero():
    # An equation whose denominator hits zero must not present 0.0 as if it were
    # a real scaled value - that is exactly the "invent a number" bug the project
    # exists to avoid. It reads as undefined (NaN) and propagates.
    fn = xdf.compile_equation("1/X")
    assert fn(2) == pytest.approx(0.5)
    assert math.isnan(fn(0))
    assert math.isnan(xdf.Scaling("1/X").apply(0))


def test_undefined_cells_render_as_undef_and_serialise_as_null():
    assert xdf._fmt(float("nan"), 2) == "undef"
    view = xdf.TableView("t", "", [], [], [[float("nan"), 1.5]], "1/X", True, True)
    assert view.to_dict()["cells"] == [[None, 1.5]]


def test_malformed_numeric_field_is_rejected_not_silently_zero():
    # Absent/empty optional attributes keep the default...
    assert xdf._int(None, 7) == 7
    assert xdf._int("", 7) == 7
    # ...valid decimal and hex (with or without 0x) parse...
    assert xdf._int("0x10") == 16
    assert xdf._int("1A") == 26
    # ...but a present, malformed value is an error, not a silent 0 that would
    # read bin byte 0 as if it were real calibration data.
    with pytest.raises(xdf.XdfError):
        xdf._int("0xZZ")


def test_floating_point_entries_are_refused_not_misdecoded():
    # A float-flagged (IEEE-754) entry must not be read as an integer and shown
    # as a plausible-but-false calibration value - cardiag decodes ints only.
    from xml.etree import ElementTree
    axis = ElementTree.fromstring(
        '<XDFAXIS><EMBEDDEDDATA mmedaddress="0x0" mmedelementsizebits="32" '
        'mmedtypeflags="0x10000"/></XDFAXIS>')
    emb = xdf._embedded(axis)
    assert emb.is_float is True
    with pytest.raises(xdf.XdfError):
        xdf.read_block(xdf.Definition(), emb, b"\x3f\x80\x00\x00", xdf.Scaling("X"))


def test_a_negative_effective_address_is_refused_not_wrapped():
    # A base-offset subtraction that underflows gives a negative effective
    # address. data[off:off+n] would then read real bytes from the END of the
    # bin (Python negative indexing) and present them as a value - here the old
    # code would have returned byte 27. It must raise instead.
    definition = xdf.Definition(base_offset=0x100, base_subtract=True)
    emb = xdf.Embedded(address=0xFB, size_bits=8, rows=1, cols=1, signed=False)
    with pytest.raises(xdf.XdfError):
        xdf.read_block(definition, emb, bytes(range(32)), xdf.Scaling("X"))
