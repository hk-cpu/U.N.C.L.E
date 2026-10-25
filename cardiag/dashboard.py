"""The live dashboard: a self-refreshing terminal view of engine data."""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field

from . import console
from .elm327 import ObdError
from .logger import DataLogger
from .pids import PID
from .session import Reading, Session
from .transport import TransportError

CLEAR_SCREEN = "\033[2J\033[H"
HIDE_CURSOR = "\033[?25l"
SHOW_CURSOR = "\033[?25h"
CLEAR_TO_END = "\033[0J"

#: Meter ranges for display only. The PID table carries each parameter's true
#: protocol range, but scaling a tachometer to 16,383 rpm makes the bar useless,
#: so these are the ranges a driver actually cares about.
DISPLAY_RANGE: dict[str, tuple[float, float]] = {
    "RPM": (0, 8000),
    "SPEED": (0, 180),
    "COOLANT_TEMP": (0, 120),
    "OIL_TEMP": (0, 150),
    "INTAKE_TEMP": (-10, 80),
    "AMBIENT_AIR_TEMP": (-10, 50),
    "MAF": (0, 120),
    "TIMING_ADVANCE": (-20, 50),
    "CONTROL_MODULE_VOLTAGE": (10, 16),
    "SHORT_FUEL_TRIM_1": (-25, 25),
    "LONG_FUEL_TRIM_1": (-25, 25),
    "SHORT_FUEL_TRIM_2": (-25, 25),
    "LONG_FUEL_TRIM_2": (-25, 25),
    "FUEL_RATE": (0, 40),
    "RUN_TIME": (0, 3600),
    "CATALYST_TEMP_B1S1": (0, 900),
    "CATALYST_TEMP_B2S1": (0, 900),
}


@dataclass
class Channel:
    """One row of the dashboard, with the running extremes we have seen."""

    pid: PID
    value: object = None
    minimum: float | None = None
    maximum: float | None = None
    updates: int = 0

    def record(self, reading: Reading | None) -> None:
        if reading is None:
            return
        self.value = reading.value
        self.updates += 1
        if isinstance(reading.value, (int, float)) and not isinstance(reading.value, bool):
            number = float(reading.value)
            self.minimum = number if self.minimum is None else min(self.minimum, number)
            self.maximum = number if self.maximum is None else max(self.maximum, number)


@dataclass
class Dashboard:
    session: Session
    channels: list[Channel] = field(default_factory=list)
    logger: DataLogger | None = None
    refresh: float = 0.2
    samples: int = 0
    errors: int = 0
    started: float = field(default_factory=time.monotonic)

    @classmethod
    def for_session(cls, session: Session, names: list[str] | None = None,
                    logger: DataLogger | None = None,
                    refresh: float = 0.2) -> "Dashboard":
        from . import pids as pid_module

        if names:
            selected = []
            for name in names:
                entry = pid_module.get(name)
                if entry is None:
                    raise KeyError(f"unknown PID: {name}")
                selected.append(entry)
        else:
            selected = session.live_pids()

        if not selected:
            raise ObdError(
                "the vehicle did not advertise any live data PIDs. "
                "Check the ignition is on."
            )

        return cls(
            session=session,
            channels=[Channel(pid=entry) for entry in selected],
            logger=logger,
            refresh=refresh,
        )

    # -- main loop ---------------------------------------------------------
    def run(self, duration: float | None = None) -> None:
        """Poll and redraw until Ctrl-C, or until ``duration`` seconds pass."""
        deadline = None if duration is None else time.monotonic() + duration
        interactive = sys.stdout.isatty()

        if interactive:
            sys.stdout.write(HIDE_CURSOR + CLEAR_SCREEN)
        try:
            while deadline is None or time.monotonic() < deadline:
                cycle_started = time.monotonic()
                readings = self._poll()
                self.samples += 1

                if self.logger is not None and readings:
                    self.logger.write(readings)

                if interactive:
                    sys.stdout.write("\033[H" + self.render() + CLEAR_TO_END)
                    sys.stdout.flush()

                elapsed = time.monotonic() - cycle_started
                if elapsed < self.refresh:
                    time.sleep(self.refresh - elapsed)
        except KeyboardInterrupt:
            pass
        finally:
            if interactive:
                sys.stdout.write(SHOW_CURSOR + "\n")
                sys.stdout.flush()

    def _poll(self) -> dict[str, Reading]:
        readings: dict[str, Reading] = {}
        for channel in self.channels:
            try:
                reading = self.session.read(channel.pid.pid)
            except TransportError:
                # A dropped link is worth surfacing, but not worth crashing the
                # dashboard over: the adapter often recovers on the next pass.
                self.errors += 1
                continue
            except ObdError:
                self.errors += 1
                continue
            channel.record(reading)
            if reading is not None:
                readings[reading.name] = reading
        return readings

    # -- drawing -----------------------------------------------------------
    def render(self) -> str:
        columns = console.width()
        lines = [self._header(), ""]

        layout = self._layout(columns)
        for channel in self.channels:
            lines.append(self._row(channel, layout))

        lines.append("")
        lines.append(console.paint("  Ctrl-C to stop", "dim"))
        return "\n".join(lines)

    def _layout(self, columns: int) -> dict[str, int]:
        """Share the terminal width between label, value, meter and extremes.

        Columns are dropped from the right as the terminal narrows, so a small
        window still shows the numbers that matter instead of a wrapped mess.
        """
        value_width = 16
        longest = max((len(c.pid.description) for c in self.channels), default=20)
        label_width = min(longest, max(16, columns - value_width - 34))

        # 2 leading spaces, then two-space gaps between each column.
        spare = columns - 2 - label_width - 2 - value_width

        extremes_width = 20 if spare >= 34 else 0
        bar_size = spare - (2 + extremes_width if extremes_width else 0) - 2
        bar_size = max(0, min(24, bar_size))
        if bar_size < 8:
            bar_size = 0

        return {
            "label": label_width,
            "value": value_width,
            "bar": bar_size,
            "extremes": extremes_width,
        }

    def _header(self) -> str:
        elapsed = time.monotonic() - self.started
        rate = self.samples / elapsed if elapsed > 0 else 0.0
        adapter = self.session.adapter

        left = console.paint("cardiag live", "bold", "cyan")
        middle = adapter.protocol if adapter else "connected"
        right = f"{rate:.1f} refresh/s  {len(self.channels)} channels"
        if self.errors:
            right += console.paint(f"  {self.errors} read errors", "yellow")
        if self.logger is not None:
            right += console.paint(f"  logging {getattr(self.logger, 'rows', 0)} rows", "green")

        return f"{left}  {console.paint(middle, 'dim')}\n  {console.paint(right, 'dim')}"

    def _row(self, channel: Channel, layout: dict[str, int]) -> str:
        label_width = layout["label"]
        label = channel.pid.description[:label_width].ljust(label_width)

        if channel.value is None:
            return f"  {console.paint(label, 'dim')}  {console.paint('waiting', 'dim')}"

        value_text = channel.pid.format(channel.value)[: layout["value"]].rjust(layout["value"])
        parts = [f"  {label}  {console.paint(value_text, 'bold')}"]

        numeric = (isinstance(channel.value, (int, float))
                   and not isinstance(channel.value, bool))

        if layout["bar"]:
            low, high = self._meter_range(channel)
            if numeric and low is not None and high is not None:
                meter = console.bar(float(channel.value), low, high, layout["bar"])
                parts.append(f"  {console.paint(meter, self._meter_colour(channel))}")
            else:
                parts.append("  " + " " * layout["bar"])

        if layout["extremes"]:
            text = ""
            if (numeric and channel.minimum is not None
                    and channel.maximum is not None
                    and channel.maximum > channel.minimum):
                text = f"[{channel.minimum:.4g} .. {channel.maximum:.4g}]"
            parts.append("  " + console.paint(text[: layout["extremes"]], "grey"))

        return "".join(parts).rstrip()

    @staticmethod
    def _meter_range(channel: Channel) -> tuple[float | None, float | None]:
        override = DISPLAY_RANGE.get(channel.pid.name)
        if override:
            return override
        return channel.pid.minimum, channel.pid.maximum

    def _meter_colour(self, channel: Channel) -> str:
        """Warn on the handful of channels where a high reading matters."""
        value = channel.value
        if not isinstance(value, (int, float)):
            return "cyan"
        name = channel.pid.name
        if name == "COOLANT_TEMP" and value > 103:
            return "red"
        if name == "RPM" and value > 5500:
            return "yellow"
        if name in ("LONG_FUEL_TRIM_1", "LONG_FUEL_TRIM_2") and abs(value) > 15:
            return "yellow"
        if name == "CONTROL_MODULE_VOLTAGE" and (value < 13.0 or value > 15.0):
            return "yellow"
        return "cyan"
