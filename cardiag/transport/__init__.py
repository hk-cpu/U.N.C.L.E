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

__all__ = ["Transport", "TransportError", "open_transport", "describe_url",
           "link_kind", "link_label", "LINK_LABELS"]

#: How each link kind is named in the interface.
LINK_LABELS = {
    "usb": "USB",
    "bluetooth": "Bluetooth",
    "wifi": "WiFi",
    "serial": "Serial",
    "simulated": "Simulated",
}

#: Device paths and driver names that mean a cabled adapter. The chip names are
#: the USB-serial bridges the common ELM327 cables are built on.
_USB_HINTS = ("ttyusb", "ttyacm", "usbserial", "usbmodem", "ch340", "ch341",
              "cp210", "ftdi", "pl2303", "usb")

#: Bluetooth serial port profile shows up as rfcomm on Linux and as a plain
#: named device on macOS - the giveaway is the absence of "usb", handled below.
_BLUETOOTH_HINTS = ("rfcomm", "bluetooth", "bth", " spp", "-spp", "obdii")


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


def link_kind(url: str, description: str = "") -> str:
    """Classify how the adapter is attached: usb, bluetooth, wifi, or serial.

    A Bluetooth ELM327 reaches the computer as a serial port, so the URL alone
    often cannot tell the two apart. Pass the port ``description`` from
    ``list_ports`` when there is one - that is where the driver names itself.
    """
    scheme, target, _ = _parse(url)
    if scheme == "sim":
        return "simulated"
    if scheme == "tcp":
        return "wifi"

    haystack = f"{target} {description}".lower()
    if any(hint in haystack for hint in _BLUETOOTH_HINTS):
        return "bluetooth"
    if any(hint in haystack for hint in _USB_HINTS):
        return "usb"
    # macOS names a Bluetooth port after the device itself (/dev/cu.OBDLink),
    # while a cable always carries "usb" - which the check above would have
    # caught. So an unqualified cu./tty. device here is a paired adapter.
    if re.match(r"^/dev/(cu|tty)\..+", target):
        return "bluetooth"
    return "serial"


def link_label(url: str, description: str = "") -> str:
    return LINK_LABELS[link_kind(url, description)]


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
