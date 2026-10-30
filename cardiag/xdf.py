"""Read a calibration bin against a TunerPro XDF — display only.

A bin is a raw dump of the ECU's flash. On its own it is a wall of bytes; an
XDF is the map that says which bytes are the spark table, which are the rev
limiter, and how to turn the stored integers into degrees and RPM. TunerPro and
HP Tuners' editors read exactly this pairing. This module does the *reading*
half of that: given a bin you already have and an XDF written for its OSID, it
shows the tables and constants the XDF defines.

What this deliberately is not:

* It does not edit, write, checksum or flash anything. It opens both files
  read-only and produces numbers to look at.
* It does not ship or infer a map. The XDF is supplied by you, and cardiag does
  not check it matches the bin's OSID — a mismatched XDF reads the wrong
  addresses, so that is on you to get right (`cardiag calibration` reads the
  OSID to match).
* It states its assumptions rather than hiding them. Values are decoded
  big-endian (correct for this car's PowerPC NGC) unless told otherwise, signed
  per the XDF's own type flag, and scaled by the XDF's equation; an equation it
  cannot parse leaves the raw integer and says so.

Nothing here claims more than the two files it was given actually say.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from xml.etree import ElementTree

# ---------------------------------------------------------------------------
# A very small, safe arithmetic evaluator for XDF scaling equations
# ---------------------------------------------------------------------------
#
# XDF equations are things like "X", "X*0.1", "(X*0.5)-64", "X*100/255". They
# are expressions over a single variable X. Evaluating them with eval() would
# run arbitrary code from a file off the internet, so this parses a restricted
# grammar instead: numbers, X, + - * / ^, unary minus and parentheses. Anything
# outside that grammar compiles to None and the caller shows the raw integer.

_TOKEN = re.compile(r"\s*(?:(?P<num>\d+\.?\d*(?:[eE][-+]?\d+)?)"
                    r"|(?P<x>[Xx])|(?P<op>[-+*/^()]))")


def compile_equation(equation: str):
    """Turn an XDF equation into ``f(x) -> float``, or None if unsupported."""
    tokens: list[tuple[str, str]] = []
    pos = 0
    while pos < len(equation):
        if equation[pos].isspace():
            pos += 1
            continue
        match = _TOKEN.match(equation, pos)
        if not match or match.end() == pos:
            return None
        if match.group("num") is not None:
            tokens.append(("num", match.group("num")))
        elif match.group("x") is not None:
            tokens.append(("x", "x"))
        else:
            tokens.append(("op", match.group("op")))
        pos = match.end()
    if not tokens:
        return None

    try:
        parser = _Parser(tokens)
        node = parser.parse()
    except _ParseError:
        return None

    def evaluate(x: float) -> float:
        return node(x)

    return evaluate


class _ParseError(Exception):
    pass


class _Parser:
    """Recursive-descent parser → a tree of ``f(x)`` closures.

    expr   := term (('+'|'-') term)*
    term   := power (('*'|'/') power)*
    power  := unary ('^' power)?
    unary  := '-' unary | atom
    atom   := number | 'x' | '(' expr ')'
    """

    def __init__(self, tokens: list[tuple[str, str]]) -> None:
        self.tokens = tokens
        self.i = 0

    def _peek(self):
        return self.tokens[self.i] if self.i < len(self.tokens) else (None, None)

    def _take(self):
        token = self._peek()
        self.i += 1
        return token

    def parse(self):
        node = self._expr()
        if self.i != len(self.tokens):
            raise _ParseError("trailing tokens")
        return node

    def _expr(self):
        node = self._term()
        while self._peek() == ("op", "+") or self._peek() == ("op", "-"):
            op = self._take()[1]
            rhs = self._term()
            node = (lambda a, b: lambda x: a(x) + b(x))(node, rhs) if op == "+" \
                else (lambda a, b: lambda x: a(x) - b(x))(node, rhs)
        return node

    def _term(self):
        node = self._power()
        while self._peek() == ("op", "*") or self._peek() == ("op", "/"):
            op = self._take()[1]
            rhs = self._power()
            if op == "*":
                node = (lambda a, b: lambda x: a(x) * b(x))(node, rhs)
            else:
                # A zero denominator makes the scaled value undefined. Return
                # NaN, never a fabricated 0.0 - the viewer must not present an
                # invented number as real calibration data. NaN propagates
                # correctly through any further arithmetic.
                node = (lambda a, b: lambda x: a(x) / b(x) if b(x) else
                        float("nan"))(node, rhs)
        return node

    def _power(self):
        node = self._unary()
        if self._peek() == ("op", "^"):
            self._take()
            rhs = self._power()
            node = (lambda a, b: lambda x: a(x) ** b(x))(node, rhs)
        return node

    def _unary(self):
        if self._peek() == ("op", "-"):
            self._take()
            node = self._unary()
            return (lambda a: lambda x: -a(x))(node)
        return self._atom()

    def _atom(self):
        kind, value = self._take()
        if kind == "num":
            number = float(value)
            return lambda x, _n=number: _n
        if kind == "x":
            return lambda x: float(x)
        if (kind, value) == ("op", "("):
            node = self._expr()
            if self._take() != ("op", ")"):
                raise _ParseError("missing )")
            return node
        raise _ParseError(f"unexpected token {value!r}")


# ---------------------------------------------------------------------------
# The XDF model
# ---------------------------------------------------------------------------

#: TunerPro mmedtypeflags: bit 0 marks a signed quantity. 0x10000 is the
#: community-standard float flag (IEEE-754). cardiag decodes integers only, so a
#: float-flagged entry is refused rather than read as an int and shown as a false
#: value - the float bit is not firmly documented, so guessing a decode would be
#: worse than saying plainly that cardiag does not handle it.
_FLAG_SIGNED = 0x01
_FLAG_FLOAT = 0x10000


@dataclass
class Embedded:
    """Where a block of numbers lives in the bin and how wide each one is."""

    address: int
    size_bits: int
    rows: int
    cols: int
    signed: bool
    row_stride_bits: int = 0        # 0 means rows are contiguous
    is_float: bool = False          # IEEE-754; cardiag refuses these

    @property
    def element_bytes(self) -> int:
        return max(1, self.size_bits // 8)


@dataclass
class Scaling:
    equation: str = "X"
    decimals: int = 2

    def apply(self, raw: int) -> float | int:
        fn = compile_equation(self.equation)
        return raw if fn is None else fn(raw)

    @property
    def parsable(self) -> bool:
        return compile_equation(self.equation) is not None


@dataclass
class Axis:
    axis_id: str
    units: str = ""
    embedded: Embedded | None = None
    scaling: Scaling = field(default_factory=Scaling)
    labels: list[str] = field(default_factory=list)


@dataclass
class Table:
    title: str
    units: str
    z: Axis
    x: Axis | None = None
    y: Axis | None = None


@dataclass
class Constant:
    title: str
    units: str
    embedded: Embedded
    scaling: Scaling


@dataclass
class Definition:
    base_offset: int = 0
    base_subtract: bool = True
    tables: list[Table] = field(default_factory=list)
    constants: list[Constant] = field(default_factory=list)

    def find(self, name: str):
        """A table or constant by a case-insensitive title match."""
        needle = name.strip().lower()
        for item in [*self.tables, *self.constants]:
            if item.title.strip().lower() == needle:
                return item
        return None


class XdfError(Exception):
    """The XDF could not be parsed, or the bin does not fit it."""


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _int(value: str | None, default: int = 0) -> int:
    # A genuinely absent or empty optional attribute falls back to the default.
    # A present-but-malformed one (a typo in an address, say) is an error, never
    # a silent 0 - that would read bin byte 0 and pass it off as a real value,
    # exactly the invent-a-number bug this viewer must not have.
    if value is None:
        return default
    value = value.strip()
    if value == "":
        return default
    try:
        return int(value, 16) if value.lower().startswith("0x") else int(value)
    except ValueError:
        pass
    try:
        return int(value, 16)       # bare hex without the 0x prefix, e.g. "1A"
    except ValueError:
        raise XdfError(f"malformed numeric value in the XDF: {value!r}")


def _text(element, tag: str, default: str = "") -> str:
    child = element.find(tag)
    return child.text.strip() if child is not None and child.text else default


def _embedded(axis_el) -> Embedded | None:
    data = axis_el.find("EMBEDDEDDATA")
    if data is None:
        return None
    flags = _int(data.get("mmedtypeflags"))
    return Embedded(
        address=_int(data.get("mmedaddress")),
        size_bits=_int(data.get("mmedelementsizebits"), 8),
        rows=_int(data.get("mmedrowcount"), 1),
        cols=_int(data.get("mmedcolcount"), 1),
        signed=bool(flags & _FLAG_SIGNED),
        row_stride_bits=_int(data.get("mmedmajorstridebits")),
        is_float=bool(flags & _FLAG_FLOAT),
    )


def _scaling(axis_el) -> Scaling:
    math = axis_el.find("MATH")
    equation = (math.get("equation") if math is not None else None) or "X"
    return Scaling(equation=equation, decimals=_int(_text(axis_el, "decimalpl"), 2))


def _axis(axis_el) -> Axis:
    labels = [label.get("value", "")
              for label in axis_el.findall("LABEL")]
    return Axis(
        axis_id=axis_el.get("id", ""),
        units=_text(axis_el, "units"),
        embedded=_embedded(axis_el),
        scaling=_scaling(axis_el),
        labels=labels,
    )


def parse_xdf(text: str) -> Definition:
    """Parse an XDF document into a :class:`Definition`.

    External entities and DTDs are refused outright: an XDF is just a table map,
    it has no business declaring a DOCTYPE, and allowing one opens the usual XML
    entity-expansion and file-read attacks on a file that may have come from a
    forum.
    """
    if re.search(r"<!DOCTYPE", text, re.IGNORECASE):
        raise XdfError("this XDF declares a DOCTYPE, which is not allowed")

    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError as exc:
        raise XdfError(f"not a valid XDF (XML parse error: {exc})") from exc

    definition = Definition()

    header = root.find("XDFHEADER")
    if header is not None:
        base = header.find("BASEOFFSET")
        if base is not None:
            definition.base_offset = _int(base.get("offset"))
            definition.base_subtract = base.get("subtract", "1") not in ("0", "")

    for table_el in root.findall("XDFTABLE"):
        axes = {axis.axis_id: axis for axis in map(_axis, table_el.findall("XDFAXIS"))}
        z = axes.get("z")
        if z is None or z.embedded is None:
            continue        # a table with no data block is nothing to show
        definition.tables.append(Table(
            title=_text(table_el, "title", "(untitled table)"),
            units=z.units,
            z=z, x=axes.get("x"), y=axes.get("y"),
        ))

    for const_el in root.findall("XDFCONSTANT"):
        embedded = _embedded(const_el)
        if embedded is None:
            continue
        definition.constants.append(Constant(
            title=_text(const_el, "title", "(untitled constant)"),
            units=_text(const_el, "units"),
            embedded=embedded,
            scaling=_scaling(const_el),
        ))

    if not definition.tables and not definition.constants:
        raise XdfError("no tables or constants found in this XDF")
    return definition


# ---------------------------------------------------------------------------
# Reading values out of the bin
# ---------------------------------------------------------------------------

def _effective_address(definition: Definition, address: int) -> int:
    # TunerPro's base offset is added to every address; the "subtract" flag,
    # when clear, means the offset is subtracted instead (some maps store file
    # offsets that already include a header).
    if definition.base_subtract:
        return address - definition.base_offset
    return address + definition.base_offset


def read_block(definition: Definition, embedded: Embedded, data: bytes,
               scaling: Scaling, big_endian: bool = True) -> list[list[float | int]]:
    """Decode an embedded block into rows of scaled values."""
    if embedded.is_float:
        raise XdfError(
            "this XDF entry stores IEEE-754 floating-point values, which cardiag "
            "does not decode - it will not read the bytes as an integer and show "
            "a false number. View it in TunerPro instead.")
    order = "big" if big_endian else "little"
    n = embedded.element_bytes
    base = _effective_address(definition, embedded.address)
    stride = (embedded.row_stride_bits // 8) or (embedded.cols * n)

    grid: list[list[float | int]] = []
    for r in range(max(1, embedded.rows)):
        row: list[float | int] = []
        for c in range(max(1, embedded.cols)):
            off = base + r * stride + c * n
            chunk = data[off:off + n]
            if len(chunk) != n:
                raise XdfError(
                    f"address 0x{embedded.address:X} runs past the end of the "
                    f"bin ({len(data)} bytes) - wrong XDF for this file?")
            raw = int.from_bytes(chunk, order, signed=embedded.signed)
            row.append(scaling.apply(raw))
        grid.append(row)
    return grid


def _axis_labels(definition: Definition, axis: Axis | None, count: int,
                 data: bytes, big_endian: bool) -> list[str]:
    """Breakpoint labels for an axis: its own static labels, its embedded
    values, or a plain 0..n-1 index when the XDF gives neither."""
    if axis is not None and axis.labels:
        return axis.labels[:count] + [""] * (count - len(axis.labels))
    if axis is not None and axis.embedded is not None:
        values = read_block(definition, axis.embedded, data, axis.scaling,
                             big_endian)
        flat = [v for row in values for v in row][:count]
        return [_fmt(v, axis.scaling.decimals) for v in flat] \
            + [""] * (count - len(flat))
    return [str(i) for i in range(count)]


def _fmt(value: float | int, decimals: int) -> str:
    if isinstance(value, int):
        return str(value)
    if math.isnan(value):
        return "undef"          # the equation is undefined here (e.g. 1/0)
    return f"{value:.{decimals}f}"


@dataclass
class TableView:
    title: str
    units: str
    x_labels: list[str]
    y_labels: list[str]
    cells: list[list[float | int]]
    equation: str
    equation_parsed: bool
    big_endian: bool

    def to_dict(self) -> dict:
        # An undefined cell (NaN) becomes JSON null, not the invalid literal NaN,
        # and never a fabricated number.
        cells = [[None if isinstance(v, float) and math.isnan(v) else v
                  for v in row] for row in self.cells]
        return {
            "title": self.title, "units": self.units,
            "x_labels": self.x_labels, "y_labels": self.y_labels,
            "cells": cells, "equation": self.equation,
            "equation_parsed": self.equation_parsed,
            "byte_order": "big-endian" if self.big_endian else "little-endian",
        }


def view_table(definition: Definition, table: Table, data: bytes,
               big_endian: bool = True) -> TableView:
    cells = read_block(definition, table.z.embedded, data, table.z.scaling,
                       big_endian)
    rows = len(cells)
    cols = len(cells[0]) if cells else 0
    return TableView(
        title=table.title, units=table.units,
        x_labels=_axis_labels(definition, table.x, cols, data, big_endian),
        y_labels=_axis_labels(definition, table.y, rows, data, big_endian),
        cells=cells,
        equation=table.z.scaling.equation,
        equation_parsed=table.z.scaling.parsable,
        big_endian=big_endian,
    )
