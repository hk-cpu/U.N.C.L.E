"""cardiag - read and interpret your car's on-board diagnostics.

Typical use from Python::

    from cardiag import Session

    with Session("/dev/ttyUSB0") as car:
        for code in car.read_dtcs():
            print(code)
        print(car.read("RPM"))
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "Session",
    "Reading",
    "Dtc",
    "ObdError",
    "NoDataError",
    "TransportError",
]


def __getattr__(name: str):
    # Imported lazily so that `import cardiag` stays cheap and does not pull in
    # pyserial before the caller has chosen a transport.
    if name in ("Session", "Reading"):
        from . import session

        return getattr(session, name)
    if name in ("ObdError", "NoDataError"):
        from . import elm327

        return getattr(elm327, name)
    if name == "TransportError":
        from .transport import TransportError

        return TransportError
    if name == "Dtc":
        from .dtc import Dtc

        return Dtc
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
