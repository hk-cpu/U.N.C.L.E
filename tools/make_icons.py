#!/usr/bin/env python3
"""Generate the app icons.

Run this to regenerate ``cardiag/web/static/icon-*.png``. The icons are checked
in, so this only needs running when the mark changes.

PNGs are written by hand rather than with Pillow: cardiag installs with no
dependencies beyond pyserial, and an icon generator is not worth breaking that
for. Everything is drawn at 4x and averaged down, which is enough antialiasing
for a round gauge on a flat ground.
"""

from __future__ import annotations

import math
import struct
import sys
import zlib
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "cardiag" / "web" / "static"

#: Matches the UI's dark surface and accent, so the icon belongs to the app.
GROUND = (26, 26, 25)
ACCENT = (57, 135, 229)
NEEDLE = (255, 255, 255)
WARN = (250, 178, 25)

SUPERSAMPLE = 4


def write_png(path: Path, width: int, height: int, pixels: list[list[tuple]]) -> None:
    """Write 8-bit RGBA rows to a PNG."""
    raw = bytearray()
    for row in pixels:
        raw.append(0)                       # filter type 0 (none)
        for red, green, blue, alpha in row:
            raw += bytes((red, green, blue, alpha))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )


def draw(size: int, padding: float, rounded: bool) -> list[list[tuple]]:
    """Draw the gauge mark at ``size`` pixels.

    ``padding`` is the fraction of the canvas kept clear around the mark, which
    is what a maskable icon needs so nothing important gets cropped.
    """
    big = size * SUPERSAMPLE
    centre = big / 2
    radius = big * (0.5 - padding) * 0.78

    # The gauge sweeps from lower-left round to lower-right, like a tachometer.
    start, end = math.radians(140), math.radians(400)
    thickness = radius * 0.30
    # The needle sits in the upper right, where a warmed-up engine would read.
    needle_angle = math.radians(330)
    needle_length = radius * 0.72
    needle_width = big * 0.030

    corner = big * 0.22 if rounded else 0.0
    canvas = [[(0, 0, 0, 0)] * big for _ in range(big)]

    for y in range(big):
        for x in range(big):
            if not _inside_ground(x, y, big, corner, rounded):
                continue
            colour = GROUND

            dx, dy = x - centre, y - centre
            distance = math.hypot(dx, dy)
            angle = math.atan2(dy, dx) % (2 * math.pi)

            if abs(distance - radius) <= thickness / 2:
                sweep = _sweep_position(angle, start, end)
                if sweep is not None:
                    # The last fifth of the sweep is the warning zone, which is
                    # what makes the mark read as a gauge rather than a ring.
                    colour = WARN if sweep > 0.8 else ACCENT

            if _on_needle(dx, dy, needle_angle, needle_length, needle_width):
                colour = NEEDLE

            canvas[y][x] = (*colour, 255)

    return _downsample(canvas, size)


def _inside_ground(x: int, y: int, big: int, corner: float, rounded: bool) -> bool:
    if not rounded:
        return True
    # A rounded square: only the four corner arcs need testing.
    for cx, cy in ((corner, corner), (big - corner, corner),
                   (corner, big - corner), (big - corner, big - corner)):
        if ((x < corner or x > big - corner) and (y < corner or y > big - corner)
                and abs(x - cx) < corner and abs(y - cy) < corner):
            return math.hypot(x - cx, y - cy) <= corner
    return True


def _sweep_position(angle: float, start: float, end: float) -> float | None:
    """Where ``angle`` sits along the arc, 0..1, or None if outside it."""
    span = end - start
    offset = (angle - start) % (2 * math.pi)
    if offset > span:
        return None
    return offset / span


def _on_needle(dx: float, dy: float, angle: float,
               length: float, width: float) -> bool:
    along = dx * math.cos(angle) + dy * math.sin(angle)
    across = -dx * math.sin(angle) + dy * math.cos(angle)
    return 0 <= along <= length and abs(across) <= width / 2


def _downsample(canvas: list[list[tuple]], size: int) -> list[list[tuple]]:
    out = []
    for y in range(size):
        row = []
        for x in range(size):
            red = green = blue = alpha = 0
            for sy in range(SUPERSAMPLE):
                for sx in range(SUPERSAMPLE):
                    pr, pg, pb, pa = canvas[y * SUPERSAMPLE + sy][x * SUPERSAMPLE + sx]
                    red += pr * pa
                    green += pg * pa
                    blue += pb * pa
                    alpha += pa
            if alpha:
                row.append((red // alpha, green // alpha, blue // alpha,
                            alpha // (SUPERSAMPLE ** 2)))
            else:
                row.append((0, 0, 0, 0))
        out.append(row)
    return out


def main() -> int:
    STATIC.mkdir(parents=True, exist_ok=True)

    targets = [
        ("icon-192.png", 192, 0.06, True),
        ("icon-512.png", 512, 0.06, True),
        # Maskable icons get cropped to whatever shape the OS uses, so the mark
        # sits inside the safe zone and the ground runs to the edges.
        ("icon-maskable-512.png", 512, 0.20, False),
        ("apple-touch-icon.png", 180, 0.06, False),
    ]
    for name, size, padding, rounded in targets:
        write_png(STATIC / name, size, size, draw(size, padding, rounded))
        print(f"wrote {name} ({size}x{size})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
