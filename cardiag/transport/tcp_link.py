"""TCP transport - WiFi ELM327 adapters (typically 192.168.0.10:35000)."""

from __future__ import annotations

import socket

from .base import Transport, TransportError


class TcpTransport(Transport):
    def __init__(self, host: str, port: int = 35000, timeout: float = 5.0) -> None:
        super().__init__(timeout=timeout)
        self.host = host
        self.port = port
        self._socket: socket.socket | None = None
        self.description = f"tcp {host}:{port}"

    def open(self) -> None:
        try:
            self._socket = socket.create_connection((self.host, self.port), self.timeout)
        except OSError as exc:
            raise TransportError(
                f"cannot reach the adapter at {self.host}:{self.port}: {exc}\n"
                "Make sure you are joined to the adapter's WiFi network."
            ) from exc
        self._socket.setblocking(False)

    def close(self) -> None:
        if self._socket is not None:
            try:
                self._socket.close()
            finally:
                self._socket = None

    def _write_raw(self, data: bytes) -> None:
        if self._socket is None:
            raise TransportError("socket is not open")
        self._socket.setblocking(True)
        try:
            self._socket.sendall(data)
        finally:
            self._socket.setblocking(False)

    def _read_raw(self, timeout: float) -> bytes:
        if self._socket is None:
            raise TransportError("socket is not open")
        import select

        ready, _, _ = select.select([self._socket], [], [], timeout)
        if not ready:
            return b""
        try:
            return self._socket.recv(4096)
        except BlockingIOError:
            return b""
