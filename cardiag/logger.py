"""Recording live data to disk, as CSV or SQLite."""

from __future__ import annotations

import csv
import sqlite3
import time
from pathlib import Path
from typing import Iterable

from .session import Reading


class DataLogger:
    """Common interface: open, write a sample, close."""

    def write(self, readings: dict[str, Reading]) -> None:  # pragma: no cover
        raise NotImplementedError

    def close(self) -> None:  # pragma: no cover
        raise NotImplementedError

    def __enter__(self) -> "DataLogger":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


class CsvLogger(DataLogger):
    """One row per sample; columns are fixed at open time.

    Values that arrive as structured data (an oxygen sensor's voltage plus its
    trim, for instance) are flattened to their primary number so the file stays
    loadable by a spreadsheet.
    """

    def __init__(self, path: str | Path, columns: Iterable[str]) -> None:
        self.path = Path(path)
        self.columns = list(columns)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("w", newline="", encoding="utf-8")
        self._writer = csv.writer(self._handle)
        self._writer.writerow(["timestamp", "elapsed_s"] + self.columns)
        self._start = time.time()
        self.rows = 0

    def write(self, readings: dict[str, Reading]) -> None:
        now = time.time()
        row = [f"{now:.3f}", f"{now - self._start:.2f}"]
        for name in self.columns:
            reading = readings.get(name)
            row.append("" if reading is None else _numeric(reading))
        self._writer.writerow(row)
        self._handle.flush()
        self.rows += 1

    def close(self) -> None:
        if not self._handle.closed:
            self._handle.close()


class SqliteLogger(DataLogger):
    """Long-form storage: one row per reading, easy to query after the fact."""

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS readings (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        session   TEXT    NOT NULL,
        timestamp REAL    NOT NULL,
        pid       INTEGER NOT NULL,
        name      TEXT    NOT NULL,
        value     REAL,
        text      TEXT,
        unit      TEXT
    );
    CREATE INDEX IF NOT EXISTS readings_session_name
        ON readings (session, name);
    CREATE INDEX IF NOT EXISTS readings_timestamp
        ON readings (timestamp);
    """

    def __init__(self, path: str | Path, session_id: str | None = None) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.session_id = session_id or time.strftime("%Y%m%d-%H%M%S")
        self._connection = sqlite3.connect(str(self.path))
        self._connection.executescript(self.SCHEMA)
        self._connection.commit()
        self.rows = 0

    def write(self, readings: dict[str, Reading]) -> None:
        now = time.time()
        rows = []
        for reading in readings.values():
            numeric = _as_float(reading.value)
            rows.append((
                self.session_id,
                now,
                reading.pid.pid,
                reading.name,
                numeric,
                None if numeric is not None else str(reading.value),
                reading.pid.unit,
            ))
        if rows:
            self._connection.executemany(
                "INSERT INTO readings (session, timestamp, pid, name, value, text, unit)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            self._connection.commit()
            self.rows += len(rows)

    def close(self) -> None:
        self._connection.close()


def open_logger(path: str | Path, columns: Iterable[str]) -> DataLogger:
    """Pick a logger from the file extension: ``.db``/``.sqlite`` or CSV."""
    suffix = Path(path).suffix.lower()
    if suffix in (".db", ".sqlite", ".sqlite3"):
        return SqliteLogger(path)
    return CsvLogger(path, columns)


def _numeric(reading: Reading) -> str:
    value = _as_float(reading.value)
    if value is not None:
        return f"{value:.4g}"
    return str(reading.value)


def _as_float(value: object) -> float | None:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        # Structured readings: take the first numeric member as the headline.
        for item in value.values():
            if isinstance(item, (int, float)) and not isinstance(item, bool):
                return float(item)
    return None
