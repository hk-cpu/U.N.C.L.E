"""Transport layer: how bytes get to and from the OBD-II adapter.

Three transports are supported, selected by a connection URL:

    /dev/ttyUSB0            a serial port (USB or Bluetooth SPP)
    COM3                    same, on Windows
    serial:///dev/ttyUSB0?baud=38400
    tcp://192.168.0.10:35000    a WiFi ELM327
    sim://                  the built-in car simulator, no hardware needed
"""

from __future__ import annotations

import re
from urllib.parse import urlparse, parse_qs

from .base import Transport, TransportError

__all__ = ["Transport", "TransportError", "open_transport", "describe_url"]


def open_transport(url: str, timeout: float = 5.0) -> Transport:
    """Build (but do not connect) a transport from a connection URL."""
    scheme, target, params = _parse(url)

    if scheme == "sim":
        from .simulator import SimulatorTransport

        profile = params.get("profile", ["default"])[0]
        return SimulatorTransport(profile=profile, timeout=timeout)

    if scheme == "tcp":
        from .tcp_link import TcpTransport

        host, _, port = target.partition(":")
        return TcpTransport(host=host, port=int(port or 35000), timeout=timeout)

    if scheme == "serial":
        from .serial_link import SerialTransport

        baud = params.get("baud", [None])[0]
        return SerialTransport(
            port=target,
            baudrate=int(baud) if baud else None,
            timeout=timeout,
        )

    raise TransportError(f"unsupported connection scheme: {scheme!r}")


def describe_url(url: str) -> str:
    """A short human-readable description of a connection URL."""
    scheme, target, params = _parse(url)
    if scheme == "sim":
        return f"simulated vehicle (profile: {params.get('profile', ['default'])[0]})"
    if scheme == "tcp":
        return f"WiFi adapter at {target}"
    baud = params.get("baud", [None])[0]
    return f"serial adapter on {target}" + (f" @ {baud} baud" if baud else "")


def _parse(url: str) -> tuple[str, str, dict[str, list[str]]]:
    url = url.strip()

    # Bare device paths and Windows COM ports are treated as serial.
    if url.startswith("/") or url.startswith("./") or re.match(r"(?i)^COM\d+$", url):
        return "serial", url, {}

    parsed = urlparse(url)
    if not parsed.scheme:
        return "serial", url, {}

    params = parse_qs(parsed.query)
    scheme = parsed.scheme.lower()

    if scheme == "serial":
        # serial:///dev/ttyUSB0 -> path; serial://COM3 -> netloc
        target = parsed.path or parsed.netloc
        return scheme, target, params
    if scheme == "tcp":
        return scheme, parsed.netloc, params
    return scheme, parsed.netloc + parsed.path, params
