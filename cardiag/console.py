"""Terminal output helpers: colour, headings, bars and tables.

Colour switches itself off when output is redirected, when ``NO_COLOR`` is set,
or when ``TERM=dumb``, so piping to a file gives clean text.
"""

from __future__ import annotations

import os
import shutil
import sys

_CODES = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "blue": "\033[34m",
    "magenta": "\033[35m",
    "cyan": "\033[36m",
    "grey": "\033[90m",
    "bright_red": "\033[91m",
}

SEVERITY_COLOUR = {
    "critical": "bright_red",
    "serious": "red",
    "moderate": "yellow",
    "advisory": "cyan",
    "info": "green",
}

SEVERITY_MARK = {
    "critical": "!!",
    "serious": " !",
    "moderate": " *",
    "advisory": " -",
    "info": " +",
}


def colour_enabled() -> bool:
    if os.environ.get("NO_COLOR") is not None:
        return False
    if os.environ.get("CARDIAG_FORCE_COLOR"):
        return True
    if os.environ.get("TERM", "") == "dumb":
        return False
    return sys.stdout.isatty()


def paint(text: str, *styles: str) -> str:
    if not styles or not colour_enabled():
        return text
    prefix = "".join(_CODES.get(style, "") for style in styles)
    return f"{prefix}{text}{_CODES['reset']}"


def severity(text: str, level: str) -> str:
    return paint(text, SEVERITY_COLOUR.get(level, "reset"))


def width(default: int = 80) -> int:
    try:
        return shutil.get_terminal_size((default, 24)).columns
    except OSError:  # pragma: no cover - unusual terminals
        return default


def heading(text: str) -> str:
    line = "-" * max(0, min(width(), 78) - len(text) - 1)
    return paint(f"{text} {line}", "bold")


def bar(value: float, minimum: float, maximum: float, size: int = 24) -> str:
    """A simple horizontal meter, used on the live dashboard."""
    if maximum <= minimum:
        return " " * size
    fraction = (value - minimum) / (maximum - minimum)
    fraction = max(0.0, min(1.0, fraction))
    filled = int(round(fraction * size))
    return "#" * filled + "." * (size - filled)


def table(rows: list[tuple[str, ...]], headers: tuple[str, ...] | None = None,
          indent: str = "  ") -> str:
    """Render aligned columns. Every row must have the same length."""
    if not rows:
        return ""

    all_rows = ([headers] if headers else []) + rows
    columns = len(all_rows[0])
    widths = [max(len(str(row[index])) for row in all_rows) for index in range(columns)]

    lines = []
    if headers:
        lines.append(indent + paint(
            "  ".join(str(headers[i]).ljust(widths[i]) for i in range(columns)).rstrip(),
            "bold",
        ))
    for row in rows:
        lines.append(indent + "  ".join(
            str(row[i]).ljust(widths[i]) for i in range(columns)
        ).rstrip())
    return "\n".join(lines)


def wrap(text: str, indent: str = "     ", limit: int | None = None) -> str:
    """Wrap prose to the terminal width, with a hanging indent."""
    import textwrap

    limit = limit or min(width(), 90)
    return textwrap.fill(
        text,
        width=max(40, limit),
        initial_indent=indent,
        subsequent_indent=indent,
    )
