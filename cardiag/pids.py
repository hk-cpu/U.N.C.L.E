"""OBD-II Mode 01/02 parameter IDs and their decoders.

Every entry knows how many data bytes the ECU returns and how to turn those
bytes into an engineering value. Formulas follow SAE J1979.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

Decoder = Callable[[bytes], Any]


@dataclass(frozen=True)
class PID:
    pid: int
    name: str
    description: str
    num_bytes: int
    decode: Decoder
    unit: str = ""
    minimum: float | None = None
    maximum: float | None = None
    #: Larger numbers sort earlier on the live dashboard.
    priority: int = 0

    @property
    def key(self) -> str:
        return self.name

    @property
    def code(self) -> str:
        return f"{self.pid:02X}"

    def format(self, value: Any) -> str:
        """Render a decoded value for display."""
        if value is None:
            return "--"
        if isinstance(value, bool):
            return "yes" if value else "no"
        if isinstance(value, float):
            text = f"{value:.2f}".rstrip("0").rstrip(".")
            return f"{text} {self.unit}".strip()
        if isinstance(value, int):
            return f"{value} {self.unit}".strip()
        if isinstance(value, dict):
            # Structured readings (an oxygen sensor's voltage plus its trim, a
            # fuel system's two banks) render as "key value, key value".
            parts = [
                f"{key.replace('_', ' ')} {_format_scalar(item)}"
                for key, item in value.items()
                if item is not None
            ]
            return ", ".join(parts) if parts else "--"
        if isinstance(value, (list, tuple, set)):
            return ", ".join(str(item) for item in sorted(value)) or "none"
        return str(value)


# ---------------------------------------------------------------------------
# Primitive decoders
# ---------------------------------------------------------------------------

def _format_scalar(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.3g}"
    return str(value)


def _u8(data: bytes) -> int:
    return data[0]


def _u16(data: bytes) -> int:
    return (data[0] << 8) | data[1]


def _percent(data: bytes) -> float:
    return data[0] * 100.0 / 255.0


def _percent_signed(data: bytes) -> float:
    """Fuel trims and EGR error: -100 % .. +99.2 %."""
    return (data[0] - 128) * 100.0 / 128.0


def _temperature(data: bytes) -> int:
    return data[0] - 40


def _rpm(data: bytes) -> float:
    return _u16(data) / 4.0


def _timing_advance(data: bytes) -> float:
    return data[0] / 2.0 - 64.0


def _maf(data: bytes) -> float:
    return _u16(data) / 100.0


def _fuel_pressure(data: bytes) -> int:
    return data[0] * 3


def _fuel_rail_pressure_relative(data: bytes) -> float:
    return _u16(data) * 0.079


def _fuel_rail_gauge_pressure(data: bytes) -> int:
    return _u16(data) * 10


def _evap_vapor_pressure(data: bytes) -> float:
    raw = int.from_bytes(data[:2], "big", signed=True)
    return raw / 4.0


def _evap_vapor_pressure_wide(data: bytes) -> int:
    return _u16(data) - 32767


def _catalyst_temperature(data: bytes) -> float:
    return _u16(data) / 10.0 - 40.0


def _control_module_voltage(data: bytes) -> float:
    return _u16(data) / 1000.0


def _absolute_load(data: bytes) -> float:
    return _u16(data) * 100.0 / 255.0


def _equivalence_ratio(data: bytes) -> float:
    return _u16(data) * 2.0 / 65536.0


def _engine_fuel_rate(data: bytes) -> float:
    return _u16(data) / 20.0


def _oxygen_sensor_voltage(data: bytes) -> dict[str, Any]:
    """Legacy narrow-band sensor: voltage plus the associated short fuel trim."""
    trim = None if data[1] == 0xFF else (data[1] - 128) * 100.0 / 128.0
    return {"voltage": data[0] / 200.0, "short_trim_pct": trim}


def _oxygen_sensor_lambda_voltage(data: bytes) -> dict[str, Any]:
    return {
        "lambda": _u16(data[0:2]) * 2.0 / 65536.0,
        "voltage": _u16(data[2:4]) * 8.0 / 65536.0,
    }


def _oxygen_sensor_lambda_current(data: bytes) -> dict[str, Any]:
    return {
        "lambda": _u16(data[0:2]) * 2.0 / 65536.0,
        "current_ma": (_u16(data[2:4]) / 256.0) - 128.0,
    }


FUEL_SYSTEM_STATES = {
    0x00: "off",
    0x01: "open loop - engine not yet warm",
    0x02: "closed loop - using oxygen sensor",
    0x04: "open loop - load or deceleration",
    0x08: "open loop - system fault detected",
    0x10: "closed loop - but a sensor is faulted",
}


def _fuel_system_status(data: bytes) -> dict[str, str]:
    result = {}
    for index, raw in enumerate(data[:2], start=1):
        if raw:
            result[f"bank{index}"] = FUEL_SYSTEM_STATES.get(raw, f"unknown (0x{raw:02X})")
    return result


SECONDARY_AIR_STATES = {
    0x01: "upstream of catalyst",
    0x02: "downstream of catalyst",
    0x04: "vented to atmosphere or off",
    0x08: "pump commanded on for diagnostics",
}


def _secondary_air_status(data: bytes) -> str:
    return SECONDARY_AIR_STATES.get(data[0], f"unknown (0x{data[0]:02X})")


FUEL_TYPES = {
    0: "not available", 1: "petrol", 2: "methanol", 3: "ethanol", 4: "diesel",
    5: "LPG", 6: "CNG", 7: "propane", 8: "electric", 9: "bifuel petrol",
    10: "bifuel methanol", 11: "bifuel ethanol", 12: "bifuel LPG",
    13: "bifuel CNG", 14: "bifuel propane", 15: "bifuel electric",
    16: "bifuel electric and combustion", 17: "hybrid petrol",
    18: "hybrid ethanol", 19: "hybrid diesel", 20: "hybrid electric",
    21: "hybrid", 22: "hybrid regenerative", 23: "bifuel diesel",
}


def _fuel_type(data: bytes) -> str:
    return FUEL_TYPES.get(data[0], f"unknown ({data[0]})")


def _oxygen_sensors_present(data: bytes) -> list[str]:
    """Bitmap of which of the four sensors on each of two banks are fitted."""
    present = []
    for bit in range(8):
        if data[0] & (1 << bit):
            bank = 1 if bit < 4 else 2
            sensor = (bit % 4) + 1
            present.append(f"B{bank}S{sensor}")
    return present


# ---------------------------------------------------------------------------
# Readiness monitors (PID 01) - what the ECU has finished self-testing
# ---------------------------------------------------------------------------

#: Tests present on every engine, reported in byte B.
CONTINUOUS_MONITORS = ("misfire", "fuel_system", "components")

#: Byte C/D tests, named differently for spark and compression ignition.
SPARK_MONITORS = (
    "catalyst", "heated_catalyst", "evaporative_system", "secondary_air_system",
    "ac_refrigerant", "oxygen_sensor", "oxygen_sensor_heater", "egr_system",
)
COMPRESSION_MONITORS = (
    "nmhc_catalyst", "nox_scr_monitor", "reserved", "boost_pressure",
    "reserved2", "exhaust_gas_sensor", "pm_filter", "egr_vvt_system",
)


@dataclass
class MonitorStatus:
    """Decoded PID 01: the check-engine light, the DTC count and readiness."""

    mil_on: bool
    dtc_count: int
    compression_ignition: bool
    #: monitor name -> "ready", "not ready" or "not supported"
    monitors: dict[str, str] = field(default_factory=dict)

    @property
    def not_ready(self) -> list[str]:
        return [name for name, state in self.monitors.items() if state == "not ready"]

    @property
    def emissions_ready(self) -> bool:
        """True when the car would pass an emissions-readiness pre-check.

        Regulators generally allow one incomplete monitor (two on older cars);
        this uses the stricter modern rule.
        """
        return len(self.not_ready) <= 1


def _monitor_status(data: bytes) -> MonitorStatus:
    a, b, c, d = data[0], data[1], data[2], data[3]
    compression = bool(b & 0x08)

    monitors: dict[str, str] = {}
    for index, name in enumerate(CONTINUOUS_MONITORS):
        supported = bool(b & (1 << index))
        incomplete = bool(b & (1 << (index + 4)))
        monitors[name] = _readiness(supported, incomplete)

    names = COMPRESSION_MONITORS if compression else SPARK_MONITORS
    for index, name in enumerate(names):
        if name.startswith("reserved"):
            continue
        supported = bool(c & (1 << index))
        incomplete = bool(d & (1 << index))
        monitors[name] = _readiness(supported, incomplete)

    return MonitorStatus(
        mil_on=bool(a & 0x80),
        dtc_count=a & 0x7F,
        compression_ignition=compression,
        monitors=monitors,
    )


def _readiness(supported: bool, incomplete: bool) -> str:
    if not supported:
        return "not supported"
    return "not ready" if incomplete else "ready"


# ---------------------------------------------------------------------------
# Supported-PID bitmaps
# ---------------------------------------------------------------------------

def _supported_bitmap(base: int) -> Decoder:
    """Decode a 4-byte bitmap into the set of PIDs it advertises."""

    def decode(data: bytes) -> set[int]:
        bits = int.from_bytes(data[:4], "big")
        return {
            base + offset
            for offset in range(1, 33)
            if bits & (1 << (32 - offset))
        }

    return decode


# ---------------------------------------------------------------------------
# The table
# ---------------------------------------------------------------------------

def _pid(pid: int, name: str, description: str, num_bytes: int, decode: Decoder,
         unit: str = "", minimum: float | None = None, maximum: float | None = None,
         priority: int = 0) -> PID:
    return PID(pid, name, description, num_bytes, decode, unit, minimum, maximum, priority)


_TABLE: list[PID] = [
    _pid(0x00, "PIDS_A", "Supported PIDs 01-20", 4, _supported_bitmap(0x00)),
    _pid(0x01, "STATUS", "Monitor status since codes cleared", 4, _monitor_status),
    _pid(0x03, "FUEL_STATUS", "Fuel system status", 2, _fuel_system_status),
    _pid(0x04, "ENGINE_LOAD", "Calculated engine load", 1, _percent, "%", 0, 100, priority=70),
    _pid(0x05, "COOLANT_TEMP", "Engine coolant temperature", 1, _temperature, "degC", -40, 215, priority=90),
    _pid(0x06, "SHORT_FUEL_TRIM_1", "Short term fuel trim, bank 1", 1, _percent_signed, "%", -100, 99.2, priority=60),
    _pid(0x07, "LONG_FUEL_TRIM_1", "Long term fuel trim, bank 1", 1, _percent_signed, "%", -100, 99.2, priority=60),
    _pid(0x08, "SHORT_FUEL_TRIM_2", "Short term fuel trim, bank 2", 1, _percent_signed, "%", -100, 99.2),
    _pid(0x09, "LONG_FUEL_TRIM_2", "Long term fuel trim, bank 2", 1, _percent_signed, "%", -100, 99.2),
    _pid(0x0A, "FUEL_PRESSURE", "Fuel pressure (gauge)", 1, _fuel_pressure, "kPa", 0, 765),
    _pid(0x0B, "INTAKE_PRESSURE", "Intake manifold absolute pressure", 1, _u8, "kPa", 0, 255, priority=50),
    _pid(0x0C, "RPM", "Engine speed", 2, _rpm, "rpm", 0, 16383.75, priority=100),
    _pid(0x0D, "SPEED", "Vehicle speed", 1, _u8, "km/h", 0, 255, priority=95),
    _pid(0x0E, "TIMING_ADVANCE", "Timing advance before top dead centre", 1, _timing_advance, "deg", -64, 63.5, priority=40),
    _pid(0x0F, "INTAKE_TEMP", "Intake air temperature", 1, _temperature, "degC", -40, 215, priority=55),
    _pid(0x10, "MAF", "Mass air flow rate", 2, _maf, "g/s", 0, 655.35, priority=65),
    _pid(0x11, "THROTTLE_POS", "Throttle position", 1, _percent, "%", 0, 100, priority=85),
    _pid(0x12, "AIR_STATUS", "Commanded secondary air status", 1, _secondary_air_status),
    _pid(0x13, "O2_SENSORS", "Oxygen sensors present", 1, _oxygen_sensors_present),
    _pid(0x14, "O2_B1S1", "Oxygen sensor 1 (bank 1)", 2, _oxygen_sensor_voltage, "V"),
    _pid(0x15, "O2_B1S2", "Oxygen sensor 2 (bank 1)", 2, _oxygen_sensor_voltage, "V"),
    _pid(0x16, "O2_B1S3", "Oxygen sensor 3 (bank 1)", 2, _oxygen_sensor_voltage, "V"),
    _pid(0x17, "O2_B1S4", "Oxygen sensor 4 (bank 1)", 2, _oxygen_sensor_voltage, "V"),
    _pid(0x18, "O2_B2S1", "Oxygen sensor 1 (bank 2)", 2, _oxygen_sensor_voltage, "V"),
    _pid(0x19, "O2_B2S2", "Oxygen sensor 2 (bank 2)", 2, _oxygen_sensor_voltage, "V"),
    _pid(0x1A, "O2_B2S3", "Oxygen sensor 3 (bank 2)", 2, _oxygen_sensor_voltage, "V"),
    _pid(0x1B, "O2_B2S4", "Oxygen sensor 4 (bank 2)", 2, _oxygen_sensor_voltage, "V"),
    _pid(0x1F, "RUN_TIME", "Run time since engine start", 2, _u16, "s", 0, 65535, priority=20),
    _pid(0x20, "PIDS_B", "Supported PIDs 21-40", 4, _supported_bitmap(0x20)),
    _pid(0x21, "DISTANCE_W_MIL", "Distance travelled with the warning light on", 2, _u16, "km", 0, 65535, priority=30),
    _pid(0x22, "FUEL_RAIL_PRESSURE_VAC", "Fuel rail pressure relative to manifold vacuum", 2, _fuel_rail_pressure_relative, "kPa"),
    _pid(0x23, "FUEL_RAIL_PRESSURE_DIRECT", "Fuel rail gauge pressure (direct injection)", 2, _fuel_rail_gauge_pressure, "kPa"),
    _pid(0x24, "O2_S1_WR_VOLTAGE", "Wide range oxygen sensor 1 (lambda and voltage)", 4, _oxygen_sensor_lambda_voltage),
    _pid(0x25, "O2_S2_WR_VOLTAGE", "Wide range oxygen sensor 2 (lambda and voltage)", 4, _oxygen_sensor_lambda_voltage),
    _pid(0x26, "O2_S3_WR_VOLTAGE", "Wide range oxygen sensor 3 (lambda and voltage)", 4, _oxygen_sensor_lambda_voltage),
    _pid(0x27, "O2_S4_WR_VOLTAGE", "Wide range oxygen sensor 4 (lambda and voltage)", 4, _oxygen_sensor_lambda_voltage),
    _pid(0x2C, "COMMANDED_EGR", "Commanded exhaust gas recirculation", 1, _percent, "%", 0, 100),
    _pid(0x2D, "EGR_ERROR", "Exhaust gas recirculation error", 1, _percent_signed, "%", -100, 99.2),
    _pid(0x2E, "EVAP_PURGE", "Commanded evaporative purge", 1, _percent, "%", 0, 100),
    _pid(0x2F, "FUEL_LEVEL", "Fuel tank level", 1, _percent, "%", 0, 100, priority=45),
    _pid(0x30, "WARMUPS_SINCE_CLEAR", "Warm-ups since codes were cleared", 1, _u8, "", 0, 255, priority=25),
    _pid(0x31, "DISTANCE_SINCE_CLEAR", "Distance travelled since codes were cleared", 2, _u16, "km", 0, 65535, priority=25),
    _pid(0x32, "EVAP_VAPOR_PRESSURE", "Evaporative system vapour pressure", 2, _evap_vapor_pressure, "Pa"),
    _pid(0x33, "BAROMETRIC_PRESSURE", "Absolute barometric pressure", 1, _u8, "kPa", 0, 255),
    _pid(0x34, "O2_S1_WR_CURRENT", "Wide range oxygen sensor 1 (lambda and current)", 4, _oxygen_sensor_lambda_current),
    _pid(0x35, "O2_S2_WR_CURRENT", "Wide range oxygen sensor 2 (lambda and current)", 4, _oxygen_sensor_lambda_current),
    _pid(0x3C, "CATALYST_TEMP_B1S1", "Catalyst temperature, bank 1 sensor 1", 2, _catalyst_temperature, "degC", -40, 6513.5),
    _pid(0x3D, "CATALYST_TEMP_B2S1", "Catalyst temperature, bank 2 sensor 1", 2, _catalyst_temperature, "degC", -40, 6513.5),
    _pid(0x3E, "CATALYST_TEMP_B1S2", "Catalyst temperature, bank 1 sensor 2", 2, _catalyst_temperature, "degC", -40, 6513.5),
    _pid(0x3F, "CATALYST_TEMP_B2S2", "Catalyst temperature, bank 2 sensor 2", 2, _catalyst_temperature, "degC", -40, 6513.5),
    _pid(0x40, "PIDS_C", "Supported PIDs 41-60", 4, _supported_bitmap(0x40)),
    _pid(0x42, "CONTROL_MODULE_VOLTAGE", "Control module supply voltage", 2, _control_module_voltage, "V", 0, 65.535, priority=80),
    _pid(0x43, "ABSOLUTE_LOAD", "Absolute load value", 2, _absolute_load, "%", 0, 25700),
    _pid(0x44, "COMMANDED_EQUIV_RATIO", "Commanded air-fuel equivalence ratio", 2, _equivalence_ratio, "lambda", 0, 2),
    _pid(0x45, "RELATIVE_THROTTLE_POS", "Relative throttle position", 1, _percent, "%", 0, 100),
    _pid(0x46, "AMBIENT_AIR_TEMP", "Ambient air temperature", 1, _temperature, "degC", -40, 215, priority=35),
    _pid(0x47, "THROTTLE_POS_B", "Absolute throttle position B", 1, _percent, "%", 0, 100),
    _pid(0x48, "THROTTLE_POS_C", "Absolute throttle position C", 1, _percent, "%", 0, 100),
    _pid(0x49, "ACCELERATOR_POS_D", "Accelerator pedal position D", 1, _percent, "%", 0, 100),
    _pid(0x4A, "ACCELERATOR_POS_E", "Accelerator pedal position E", 1, _percent, "%", 0, 100),
    _pid(0x4B, "ACCELERATOR_POS_F", "Accelerator pedal position F", 1, _percent, "%", 0, 100),
    _pid(0x4C, "COMMANDED_THROTTLE", "Commanded throttle actuator", 1, _percent, "%", 0, 100),
    _pid(0x4D, "TIME_RUN_WITH_MIL", "Time run with the warning light on", 2, _u16, "min", 0, 65535, priority=30),
    _pid(0x4E, "TIME_SINCE_CLEAR", "Time since codes were cleared", 2, _u16, "min", 0, 65535, priority=25),
    _pid(0x51, "FUEL_TYPE", "Fuel type", 1, _fuel_type),
    _pid(0x52, "ETHANOL_PERCENT", "Ethanol content of the fuel", 1, _percent, "%", 0, 100),
    _pid(0x53, "EVAP_VAPOR_PRESSURE_ABS", "Absolute evaporative system vapour pressure", 2, lambda d: _u16(d) / 200.0, "kPa"),
    _pid(0x54, "EVAP_VAPOR_PRESSURE_ALT", "Evaporative system vapour pressure", 2, _evap_vapor_pressure_wide, "Pa"),
    _pid(0x5A, "RELATIVE_ACCEL_POS", "Relative accelerator pedal position", 1, _percent, "%", 0, 100),
    _pid(0x5B, "HYBRID_BATTERY_LIFE", "Hybrid battery pack remaining life", 1, _percent, "%", 0, 100),
    _pid(0x5C, "OIL_TEMP", "Engine oil temperature", 1, _temperature, "degC", -40, 210, priority=75),
    _pid(0x5D, "FUEL_INJECT_TIMING", "Fuel injection timing", 2, lambda d: _u16(d) / 128.0 - 210.0, "deg"),
    _pid(0x5E, "FUEL_RATE", "Engine fuel rate", 2, _engine_fuel_rate, "L/h", 0, 3212.75, priority=45),
    _pid(0x60, "PIDS_D", "Supported PIDs 61-80", 4, _supported_bitmap(0x60)),
    _pid(0x61, "DEMANDED_TORQUE", "Driver demanded engine torque", 1, lambda d: d[0] - 125, "%", -125, 130),
    _pid(0x62, "ACTUAL_TORQUE", "Actual engine torque", 1, lambda d: d[0] - 125, "%", -125, 130),
    _pid(0x63, "REFERENCE_TORQUE", "Engine reference torque", 2, _u16, "Nm", 0, 65535),
]

BY_PID: dict[int, PID] = {entry.pid: entry for entry in _TABLE}
BY_NAME: dict[str, PID] = {entry.name: entry for entry in _TABLE}

#: PIDs whose only job is to advertise other PIDs.
BITMAP_PIDS = (0x00, 0x20, 0x40, 0x60, 0x80, 0xA0, 0xC0)


def get(identifier: int | str) -> PID | None:
    """Look a PID up by number (``0x0C``) or by name (``"RPM"``)."""
    if isinstance(identifier, int):
        return BY_PID.get(identifier)
    name = identifier.strip().upper()
    if name in BY_NAME:
        return BY_NAME[name]
    try:
        return BY_PID.get(int(name, 16))
    except ValueError:
        return None


def dashboard_order(available: set[int]) -> list[PID]:
    """The supported PIDs worth showing live, most interesting first."""
    candidates = [
        BY_PID[pid]
        for pid in available
        if pid in BY_PID and BY_PID[pid].priority > 0
    ]
    return sorted(candidates, key=lambda entry: (-entry.priority, entry.pid))
