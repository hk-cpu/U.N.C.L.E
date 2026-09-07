"""Serial transport - USB cables (Vgate, FTDI, CH340) and Bluetooth SPP."""

from __future__ import annotations

import glob
import sys

from .base import Transport, TransportError

#: Baud rates worth trying, most likely first. Vgate USB and most clone
#: ELM327 cables land on 38400; genuine ELM327 chips default to 9600.
CANDIDATE_BAUDRATES = (38400, 9600, 115200, 500000, 230400, 57600)


def _pyserial():
    try:
        import serial  # type: ignore
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise TransportError(
            "pyserial is required to talk to a real adapter.\n"
            "Install it with:  pip install pyserial\n"
            "(or use sim:// to run against the built-in simulator)"
        ) from exc
    return serial


def list_ports() -> list[tuple[str, str]]:
    """Return ``(device, description)`` for every serial port we can see.

    Ports that look like an OBD adapter are listed first.
    """
    found: list[tuple[str, str]] = []
    try:
        from serial.tools import list_ports as _lp  # type: ignore

        for port in _lp.comports():
            found.append((port.device, port.description or "unknown device"))
    except ImportError:
        # Fall back to globbing the usual device nodes.
        patterns = ["/dev/ttyUSB*", "/dev/ttyACM*", "/dev/rfcomm*", "/dev/cu.*"]
        for pattern in patterns:
            for device in sorted(glob.glob(pattern)):
                found.append((device, "detected by device path"))

    found.sort(key=lambda item: (0 if _looks_like_obd(item) else 1, item[0]))
    return found


def _looks_like_obd(entry: tuple[str, str]) -> bool:
    haystack = f"{entry[0]} {entry[1]}".lower()
    hints = ("obd", "elm", "vgate", "vlinker", "ch340", "ch341", "ftdi",
             "usb serial", "cp210", "pl2303", "rfcomm")
    return any(hint in haystack for hint in hints)


def autodetect_port() -> str:
    """Pick the most plausible adapter port, or raise if there is no clear pick."""
    ports = list_ports()
    if not ports:
        raise TransportError(
            "no serial ports found.\n"
            "Plug in the adapter (or pair it over Bluetooth), then run:  cardiag ports"
        )
    if _looks_like_obd(ports[0]):
        return ports[0][0]
    if len(ports) == 1:
        return ports[0][0]
    listing = "\n".join(f"  {device}  ({desc})" for device, desc in ports)
    raise TransportError(
        "could not tell which port is the adapter. Pick one with --port:\n" + listing
    )


class SerialTransport(Transport):
    """Bytes over a serial port, with optional baud-rate probing."""

    def __init__(
        self,
        port: str | None = None,
        baudrate: int | None = None,
        timeout: float = 5.0,
    ) -> None:
        super().__init__(timeout=timeout)
        self.port = port
        self.baudrate = baudrate
        self._serial = None
        self.description = f"serial {port or '(autodetect)'}"

    def open(self) -> None:
        serial = _pyserial()

        if not self.port:
            self.port = autodetect_port()

        explicit_baud = self.baudrate is not None
        baudrates = [self.baudrate] if explicit_baud else list(CANDIDATE_BAUDRATES)
        errors: list[str] = []

        for baud in baudrates:
            try:
                handle = serial.Serial(
                    port=self.port,
                    baudrate=baud,
                    timeout=0,               # non-blocking; framing is ours
                    write_timeout=self.timeout,
                    bytesize=serial.EIGHTBITS,
                    parity=serial.PARITY_NONE,
                    stopbits=serial.STOPBITS_ONE,
                )
            except Exception as exc:  # serial.SerialException and friends
                raise TransportError(f"cannot open {self.port}: {exc}{_permission_hint()}") from exc

            self._serial = handle
            self.baudrate = baud
            if explicit_baud:
                # The user named a baud rate; trust it rather than probing.
                self.description = f"serial {self.port} @ {baud} baud"
                return
            if self._responds():
                self.description = f"serial {self.port} @ {baud} baud"
                return

            errors.append(f"{baud} baud: no reply")
            handle.close()
            self._serial = None

        detail = ", ".join(errors)
        raise TransportError(
            f"{self.port} opened but the adapter never answered ({detail}).\n"
            "Check the cable is in the OBD port and the ignition is on."
        )

    def _responds(self) -> bool:
        """Probe with ``ATI``; a live ELM327 answers with its version banner."""
        try:
            self.flush_input()
            self.write_line("ATI")
            reply = self.read_until_prompt(timeout=1.5)
        except TransportError:
            return False
        return bool(reply.strip())

    def close(self) -> None:
        if self._serial is not None:
            try:
                self._serial.close()
            finally:
                self._serial = None

    def _write_raw(self, data: bytes) -> None:
        if self._serial is None:
            raise TransportError("serial port is not open")
        self._serial.write(data)
        self._serial.flush()

    def _read_raw(self, timeout: float) -> bytes:
        if self._serial is None:
            raise TransportError("serial port is not open")
        self._serial.timeout = timeout
        waiting = self._serial.in_waiting
        if waiting:
            return self._serial.read(waiting)
        return self._serial.read(1)


def _permission_hint() -> str:
    if sys.platform.startswith("linux"):
        return (
            "\nOn Linux the port usually needs group access:"
            "\n  sudo usermod -a -G dialout $USER   (then log out and back in)"
        )
    return ""
