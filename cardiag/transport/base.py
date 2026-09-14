"""Common transport interface."""

from __future__ import annotations

import time

PROMPT = b">"


class TransportError(Exception):
    """Raised when the link to the adapter fails."""


class Transport:
    """A byte pipe to an ELM327-compatible adapter.

    Subclasses implement :meth:`open`, :meth:`close`, :meth:`_write_raw` and
    :meth:`_read_raw`; the prompt-framing logic lives here so every transport
    behaves identically to the driver above it.
    """

    #: Human readable description, filled in by subclasses.
    description: str = "transport"

    def __init__(self, timeout: float = 5.0) -> None:
        self.timeout = timeout
        self._buffer = bytearray()

    # -- lifecycle ---------------------------------------------------------
    def open(self) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def close(self) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def __enter__(self) -> "Transport":
        self.open()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- raw I/O to be provided by subclasses ------------------------------
    def _write_raw(self, data: bytes) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def _read_raw(self, timeout: float) -> bytes:  # pragma: no cover - interface
        """Return whatever bytes are available, blocking at most ``timeout``."""
        raise NotImplementedError

    # -- framing -----------------------------------------------------------
    def flush_input(self) -> None:
        """Drop anything the adapter left in the pipe from a previous command."""
        self._buffer.clear()
        deadline = time.monotonic() + 0.1
        while time.monotonic() < deadline:
            if not self._read_raw(0.02):
                break

    def write_line(self, line: str) -> None:
        self._write_raw(line.encode("ascii", "ignore") + b"\r")

    def read_until_prompt(self, timeout: float | None = None) -> str:
        """Read until the ELM327 prompt ``>``; return the text before it.

        Raises :class:`TransportError` on timeout, since a missing prompt means
        the adapter is wedged rather than merely slow to answer.
        """
        limit = self.timeout if timeout is None else timeout
        deadline = time.monotonic() + limit

        while True:
            index = self._buffer.find(PROMPT)
            if index >= 0:
                payload = bytes(self._buffer[:index])
                del self._buffer[: index + 1]
                return payload.decode("ascii", "replace")

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                partial = self._buffer.decode("ascii", "replace").strip()
                self._buffer.clear()
                raise TransportError(
                    "adapter did not respond within "
                    f"{limit:.1f}s"
                    + (f" (partial response: {partial!r})" if partial else "")
                )

            chunk = self._read_raw(min(remaining, 0.2))
            if chunk:
                self._buffer.extend(chunk)

    def command(self, line: str, timeout: float | None = None) -> str:
        """Send one line and return the adapter's reply."""
        self.write_line(line)
        return self.read_until_prompt(timeout)
