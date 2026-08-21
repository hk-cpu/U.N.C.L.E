"""Command line interface for cardiag."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

from . import __version__, console, dtc, pids, vehicles, vin as vin_module
from . import report as report_module
from .dashboard import Dashboard
from .elm327 import ObdError
from .logger import open_logger
from .session import Reading, Session
from .transport import TransportError, describe_url
from .transport.simulator import PROFILES as SIM_PROFILES

ENV_PORT = "CARDIAG_PORT"


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cardiag",
        description="Read and interpret your car's on-board diagnostics.",
        epilog=(
            "Connection: pass --port with a device path (/dev/ttyUSB0, COM3), a "
            "tcp:// URL for a WiFi adapter, or use --sim to run against the "
            "built-in simulated car (pick which one with --profile). Without "
            f"--port the adapter is auto-detected, or taken from ${ENV_PORT}."
        ),
    )
    parser.add_argument("--version", action="version", version=f"cardiag {__version__}")

    parser.add_argument(
        "-p", "--port",
        help="adapter connection: device path, serial://, tcp://host:port or sim://",
    )
    parser.add_argument(
        "--sim", action="store_true",
        help="use the built-in simulated car instead of real hardware",
    )
    parser.add_argument(
        "--sim-profile", default="default", choices=sorted(SIM_PROFILES),
        help="which simulated car --sim should present (default: default)",
    )
    parser.add_argument(
        "--baud", type=int,
        help="force a serial baud rate instead of probing for one",
    )
    parser.add_argument(
        "--timeout", type=float, default=5.0,
        help="seconds to wait for the adapter (default: 5)",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="print machine-readable JSON instead of formatted text",
    )
    parser.add_argument(
        "--no-color", action="store_true", help="disable coloured output",
    )
    parser.add_argument(
        "--vehicle", metavar="MODEL",
        help=(
            "apply a vehicle profile for model-specific advice "
            f"(known: {vehicles.describe_choices()}). "
            "Detected from the VIN when not given; 'none' disables it."
        ),
    )

    # Running cardiag with no subcommand falls through to "scan", so scan's own
    # defaults have to exist on the top-level parser as well.
    parser.set_defaults(no_freeze=False)

    sub = parser.add_subparsers(dest="command")

    sub.add_parser("ports", help="list serial ports that might be an adapter")
    sub.add_parser("info", help="show adapter, protocol and vehicle identity")
    sub.add_parser("monitors", help="show emissions readiness monitors")
    sub.add_parser("freeze", help="show the stored freeze frame")

    scan = sub.add_parser("scan", help="full health check with findings (default)")
    scan.add_argument(
        "--no-freeze", action="store_true", help="skip reading the freeze frame",
    )

    codes = sub.add_parser("codes", help="read stored trouble codes")
    codes.add_argument("--stored-only", action="store_true",
                       help="skip pending and permanent codes")

    clear = sub.add_parser("clear", help="erase trouble codes and readiness monitors")
    clear.add_argument("-y", "--yes", action="store_true",
                       help="do not ask for confirmation")

    live = sub.add_parser("live", help="live dashboard of engine data")
    live.add_argument("pids", nargs="*",
                      help="PIDs to watch (names or hex); default is everything useful")
    live.add_argument("--log", metavar="FILE",
                      help="record samples to a .csv or .db file while watching")
    live.add_argument("--duration", type=float,
                      help="stop after this many seconds")
    live.add_argument("--refresh", type=float, default=0.2,
                      help="seconds between refreshes (default: 0.2)")

    read = sub.add_parser("read", help="read specific PIDs once")
    read.add_argument("pids", nargs="+", help="PID names or hex values")

    listing = sub.add_parser("pids", help="list the PIDs this car supports")
    listing.add_argument("--all", action="store_true",
                         help="list every PID cardiag knows, not just supported ones")

    lookup = sub.add_parser("lookup", help="explain a trouble code (no car needed)")
    lookup.add_argument("codes", nargs="+", help="codes such as P0420")

    sub.add_parser("vehicle", help="show what cardiag knows about this model")

    decode = sub.add_parser("vin", help="decode a VIN")
    decode.add_argument("vin", nargs="?",
                        help="the VIN to decode; read from the car when omitted")

    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        return _run(argv)
    except BrokenPipeError:
        # Something downstream (`| head`, a closed pager) stopped reading.
        # Point stdout at /dev/null before the interpreter flushes it on exit,
        # otherwise Python prints its own traceback on the way out.
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
        return 141


def _run(argv: list[str] | None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.no_color:
        os.environ["NO_COLOR"] = "1"

    command = args.command or "scan"

    try:
        args.profile_object = _resolve_profile(args)
    except KeyError as exc:
        return _fail(str(exc.args[0]))

    # These need no vehicle connection.
    if command == "lookup":
        return _command_lookup(args)
    if command == "ports":
        return _command_ports(args)
    if command == "pids" and args.all:
        return _command_pids_all(args)
    if command == "vin" and args.vin:
        return _command_vin(args, session=None)
    if command == "vehicle" and args.profile_object is not None:
        return _command_vehicle(args, session=None)

    try:
        url = _connection_url(args)
    except TransportError as exc:
        return _fail(str(exc))

    handlers = {
        "scan": _command_scan,
        "info": _command_info,
        "codes": _command_codes,
        "clear": _command_clear,
        "live": _command_live,
        "read": _command_read,
        "pids": _command_pids,
        "monitors": _command_monitors,
        "freeze": _command_freeze,
        "vehicle": _command_vehicle,
        "vin": _command_vin,
    }
    handler = handlers.get(command)
    if handler is None:  # pragma: no cover - argparse rejects unknown commands
        parser.error(f"unknown command: {command}")

    try:
        with Session(url, timeout=args.timeout) as session:
            return handler(args, session)
    except TransportError as exc:
        return _fail(str(exc))
    except ObdError as exc:
        return _fail(str(exc))
    except KeyboardInterrupt:
        print()
        return 130


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

def _resolve_profile(args: argparse.Namespace) -> vehicles.VehicleProfile | None:
    """Turn ``--vehicle`` into a profile, or ``None`` to auto-detect by VIN."""
    requested = getattr(args, "vehicle", None)
    if not requested:
        return None
    if requested.strip().lower() in ("none", "off", "generic"):
        # An explicit opt-out: recorded so VIN detection is skipped too.
        args.vehicle_disabled = True
        return None

    profile = vehicles.get(requested)
    if profile is None:
        raise KeyError(
            f"unknown vehicle profile {requested!r}. "
            f"Known profiles: {vehicles.describe_choices()}"
        )
    return profile


def _connection_url(args: argparse.Namespace) -> str:
    if args.sim:
        return f"sim://?profile={args.sim_profile}"
    if args.port:
        url = args.port
    elif os.environ.get(ENV_PORT):
        url = os.environ[ENV_PORT]
    else:
        from .transport.serial_link import autodetect_port

        url = autodetect_port()

    if args.baud and "://" not in url:
        url = f"serial://{url}?baud={args.baud}"
    elif args.baud:
        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}baud={args.baud}"
    return url


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def _command_ports(args: argparse.Namespace) -> int:
    from .transport.serial_link import list_ports

    ports = list_ports()
    if args.json:
        print(json.dumps([{"device": d, "description": desc} for d, desc in ports], indent=2))
        return 0

    if not ports:
        print("No serial ports found.")
        print("Plug the adapter in (or pair it over Bluetooth) and try again.")
        print("You can still explore the tool with:  cardiag --sim scan")
        return 1

    from .transport.serial_link import _looks_like_obd

    print(console.heading("Serial ports"))
    rows = []
    for device, desc in ports:
        likely = _looks_like_obd((device, desc))
        rows.append((
            device,
            desc,
            console.paint("likely adapter", "green") if likely else "",
        ))
    print(console.table(rows, headers=("device", "description", "")))
    print()

    # Only recommend a port when it actually looks like an adapter; a built-in
    # /dev/ttyS0 is not something to send someone at.
    if _looks_like_obd(ports[0]):
        print(f"Connect with:  cardiag --port {ports[0][0]} scan")
    else:
        print("None of these look like an OBD adapter.")
        print("If yours is listed anyway, use it:  cardiag --port <device> scan")
        print(console.paint(
            "Otherwise check the adapter is plugged in and, on Linux, that you "
            "are in the 'dialout' group.", "dim"))
    return 0


def _command_info(args: argparse.Namespace, session: Session) -> int:
    info = session.vehicle_info()

    if args.json:
        print(json.dumps({
            "vin": info.vin,
            "ecu_name": info.ecu_name,
            "fuel_type": info.fuel_type,
            "protocol": info.protocol,
            "adapter": info.adapter,
            "battery_voltage": info.battery_voltage,
            "supported_pids": sorted(session.supported_pids),
        }, indent=2))
        return 0

    print(console.heading("Connection"))
    rows = [
        ("adapter", info.adapter),
        ("protocol", info.protocol),
        ("link", describe_url(session.url)),
    ]
    if info.battery_voltage is not None:
        rows.append(("battery", f"{info.battery_voltage:.1f} V"))
    print(console.table(rows))

    print()
    print(console.heading("Vehicle"))
    print(console.table([
        ("VIN", info.vin or "not reported"),
        ("ECU", info.ecu_name or "not reported"),
        ("fuel", info.fuel_type or "not reported"),
        ("live PIDs", str(len([p for p in session.supported_pids if p in pids.BY_PID]))),
    ]))
    return 0


def _command_scan(args: argparse.Namespace, session: Session) -> int:
    started = time.monotonic()
    progress = not args.json and sys.stdout.isatty()
    if progress:
        print(console.paint("Scanning...", "dim"), end="\r", flush=True)

    result = report_module.build(
        session,
        include_freeze_frame=not args.no_freeze,
        profile=args.profile_object,
        detect_profile=not getattr(args, "vehicle_disabled", False),
    )

    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
        return 1 if result.worst_severity in ("critical", "serious") else 0

    if progress:
        print(" " * 20, end="\r")
    _print_report(result, session, time.monotonic() - started)
    return 1 if result.worst_severity in ("critical", "serious") else 0


def _print_report(result: report_module.HealthReport, session: Session,
                  elapsed: float) -> None:
    headline_colour = {
        "critical": "bright_red", "serious": "red",
        "moderate": "yellow", "advisory": "cyan", "info": "green",
    }[result.worst_severity]

    print(console.paint(result.headline, "bold", headline_colour))
    vehicle = result.vehicle
    subtitle = vehicle.protocol
    if vehicle.vin:
        subtitle = f"VIN {vehicle.vin}  |  {subtitle}"
    if vehicle.battery_voltage is not None:
        subtitle += f"  |  battery {vehicle.battery_voltage:.1f} V"
    print(console.paint(subtitle, "dim"))
    if result.profile is not None:
        print(console.paint(f"Profile: {result.profile.name}", "dim"))
    print()

    print(console.heading("Findings"))
    for finding in result.findings:
        mark = console.SEVERITY_MARK.get(finding.severity, " -")
        print(f" {console.severity(mark, finding.severity)} "
              f"{console.paint(finding.title, 'bold')}")
        print(console.wrap(finding.detail))
        if finding.suggestion:
            print(console.wrap(console.paint(finding.suggestion, "dim")))
        print()

    if result.status is not None:
        ready = sum(1 for state in result.status.monitors.values() if state == "ready")
        total = sum(1 for state in result.status.monitors.values()
                    if state != "not supported")
        lamp = "ON" if result.status.mil_on else "off"
        print(console.heading("Readiness"))
        print(f"  warning light {console.paint(lamp, 'red' if result.status.mil_on else 'green')}"
              f"   monitors complete {ready}/{total}"
              f"   emissions pre-check "
              f"{console.paint('pass', 'green') if result.status.emissions_ready else console.paint('fail', 'yellow')}")
        print()

    if result.readings:
        print(console.heading("Live data"))
        rows = [
            (reading.pid.description, reading.pid.format(reading.value))
            for reading in result.readings.values()
        ]
        print(console.table(rows))
        print()

    if result.freeze_frame:
        print(console.heading("Freeze frame (conditions when the fault was stored)"))
        rows = []
        for key, value in result.freeze_frame.items():
            if isinstance(value, Reading):
                rows.append((value.pid.description, value.pid.format(value.value)))
            else:
                rows.append((key.replace("_", " "), str(value)))
        print(console.table(rows))
        print()

    print(console.paint(f"Scanned in {elapsed:.1f}s", "dim"))


def _command_codes(args: argparse.Namespace, session: Session) -> int:
    codes = session.read_dtcs(
        include_pending=not args.stored_only,
        include_permanent=not args.stored_only,
    )

    if args.json:
        print(json.dumps([item.to_dict() for item in codes], indent=2))
        return 1 if codes else 0

    if not codes:
        print(console.paint("No trouble codes stored.", "green"))
        status = session.monitor_status()
        if status and status.mil_on:
            print(console.paint(
                "The warning light is on even so - the fault is probably stored "
                "in a module that generic OBD-II cannot read.", "yellow"))
        return 0

    print(console.paint(dtc.summarise(codes), "bold"))
    print()
    for item in codes:
        mark = console.SEVERITY_MARK.get(item.severity, " -")
        label = f"{item.code}  {item.description}"
        print(f" {console.severity(mark, item.severity)} {console.paint(label, 'bold')}")
        print(console.wrap(f"{item.status} code | {item.system} | {item.origin}"))
        for cause in item.causes:
            print(console.bullet(cause, indent="       "))
        print()
    return 1


def _command_clear(args: argparse.Namespace, session: Session) -> int:
    codes = session.read_dtcs()
    if codes:
        print("About to erase:")
        for item in codes:
            print(f"  {item.code}  {item.description}  ({item.status})")
    else:
        print("No stored codes, but clearing also resets the readiness monitors.")
    print()
    print(console.paint(
        "Clearing wipes the freeze frame and every readiness monitor. The car "
        "will need a full drive cycle before it can pass an emissions test, and "
        "if the underlying fault is still there the code will come straight back.",
        "yellow"))

    if not args.yes:
        if not sys.stdin.isatty():
            return _fail("refusing to clear codes without --yes when input is not a terminal")
        answer = input("Type 'clear' to continue: ").strip().lower()
        if answer != "clear":
            print("Cancelled.")
            return 1

    session.clear_dtcs()
    print(console.paint("Codes cleared.", "green"))
    return 0


def _command_live(args: argparse.Namespace, session: Session) -> int:
    dashboard = Dashboard.for_session(
        session,
        names=args.pids or None,
        refresh=args.refresh,
    )

    logger = None
    if args.log:
        logger = open_logger(args.log, [channel.pid.name for channel in dashboard.channels])
        dashboard.logger = logger

    try:
        dashboard.run(duration=args.duration)
    finally:
        if logger is not None:
            logger.close()
            print(f"Recorded {getattr(logger, 'rows', 0)} rows to {args.log}")
    return 0


def _command_read(args: argparse.Namespace, session: Session) -> int:
    results: dict[str, Reading | None] = {}
    for name in args.pids:
        entry = pids.get(name)
        if entry is None:
            return _fail(f"unknown PID: {name}. Try 'cardiag pids --all' for the list.")
        results[entry.name] = session.read(entry.pid)

    if args.json:
        print(json.dumps({
            name: None if reading is None else {
                "description": reading.pid.description,
                "value": report_module.plain_value(reading.value),
                "unit": reading.pid.unit,
                "formatted": reading.pid.format(reading.value),
            }
            for name, reading in results.items()
        }, indent=2))
        return 0

    rows = []
    for name, reading in results.items():
        if reading is None:
            rows.append((name, console.paint("not supported by this vehicle", "dim")))
        else:
            rows.append((reading.pid.description, reading.pid.format(reading.value)))
    print(console.table(rows))
    return 0


def _command_pids(args: argparse.Namespace, session: Session) -> int:
    supported = sorted(session.supported_pids)

    if args.json:
        print(json.dumps([
            {
                "pid": f"{pid:02X}",
                "name": pids.BY_PID[pid].name if pid in pids.BY_PID else None,
                "description": pids.BY_PID[pid].description if pid in pids.BY_PID else None,
            }
            for pid in supported
        ], indent=2))
        return 0

    known = [pid for pid in supported if pid in pids.BY_PID]
    unknown = [pid for pid in supported if pid not in pids.BY_PID]

    print(console.heading(f"{len(known)} supported PIDs"))
    print(console.table([
        (pids.BY_PID[pid].code, pids.BY_PID[pid].name, pids.BY_PID[pid].description)
        for pid in known
    ], headers=("pid", "name", "description")))

    if unknown:
        print()
        print(console.paint(
            "Also advertised but not decoded by cardiag: "
            + ", ".join(f"{pid:02X}" for pid in unknown), "dim"))
    return 0


def _command_pids_all(args: argparse.Namespace) -> int:
    entries = sorted(pids.BY_PID.values(), key=lambda entry: entry.pid)
    if args.json:
        print(json.dumps([
            {"pid": entry.code, "name": entry.name,
             "description": entry.description, "unit": entry.unit}
            for entry in entries
        ], indent=2))
        return 0

    print(console.heading(f"{len(entries)} PIDs known to cardiag"))
    print(console.table(
        [(entry.code, entry.name, entry.description, entry.unit) for entry in entries],
        headers=("pid", "name", "description", "unit"),
    ))
    return 0


def _command_monitors(args: argparse.Namespace, session: Session) -> int:
    status = session.monitor_status()
    if status is None:
        return _fail("the vehicle did not report monitor status (mode 01 PID 01)")

    if args.json:
        print(json.dumps(report_module.status_dict(status), indent=2))
        return 0

    lamp = "ON" if status.mil_on else "off"
    print(console.heading("Readiness monitors"))
    print(f"  warning light: {console.paint(lamp, 'red' if status.mil_on else 'green')}"
          f"   stored codes: {status.dtc_count}"
          f"   engine: {'compression (diesel)' if status.compression_ignition else 'spark (petrol)'}")
    print()

    rows = []
    for name, state in status.monitors.items():
        colour = {"ready": "green", "not ready": "yellow"}.get(state, "dim")
        rows.append((name.replace("_", " "), console.paint(state, colour)))
    print(console.table(rows))

    print()
    if status.emissions_ready:
        print(console.paint("Emissions pre-check: pass", "green"))
    else:
        print(console.paint(
            f"Emissions pre-check: fail - {len(status.not_ready)} monitors incomplete. "
            "Drive a full cycle and re-check.", "yellow"))
    return 0


def _command_freeze(args: argparse.Namespace, session: Session) -> int:
    frame = session.freeze_frame()

    if args.json:
        print(json.dumps({
            key: (value.pid.format(value.value) if isinstance(value, Reading) else value)
            for key, value in frame.items()
        }, indent=2))
        return 0

    if not frame:
        print("No freeze frame stored. The ECU only records one when it confirms a fault.")
        return 0

    trigger = frame.pop("trigger_code", None)
    print(console.heading("Freeze frame"))
    if trigger:
        described = dtc.describe(str(trigger))
        print(f"  stored when {console.paint(described.code, 'bold')} was confirmed: "
              f"{described.description}")
        print()
    print(console.table([
        (value.pid.description, value.pid.format(value.value))
        for value in frame.values() if isinstance(value, Reading)
    ]))
    return 0


def _command_vehicle(args: argparse.Namespace, session: Session | None) -> int:
    profile = args.profile_object
    detected_from = "--vehicle" if profile else None

    if profile is None and session is not None:
        vin = session.read_vin()
        if vin:
            profile = vehicles.match_vin(vin)
            detected_from = f"VIN {vin}"

    if profile is None:
        if args.json:
            print(json.dumps({"profile": None,
                              "known": sorted(vehicles.PROFILES)}, indent=2))
            return 0
        print("No vehicle profile applies to this car.")
        print(f"cardiag has profiles for: {vehicles.describe_choices()}")
        print("Pick one explicitly with --vehicle if yours is listed.")
        return 0

    if args.json:
        print(json.dumps(profile.to_dict(), indent=2))
        return 0

    engine = profile.engine
    print(console.paint(profile.name, "bold"))
    print(console.paint(f"{profile.years}  |  matched from {detected_from}", "dim"))
    print()

    print(console.heading("Engine"))
    rows = [
        ("engine", engine.name),
        ("cylinders", str(engine.cylinders)),
        ("spark plugs", f"{engine.total_plugs} ({engine.plugs_per_cylinder} per cylinder)"),
    ]
    if engine.firing_order:
        rows.append(("firing order", "-".join(str(c) for c in engine.firing_order)))
    for bank, side in sorted(engine.bank_side.items()):
        members = sorted(c for c, b in engine.cylinder_bank.items() if b == bank)
        rows.append((f"bank {bank}", f"{side}: cylinders {', '.join(map(str, members))}"))
    if engine.deactivated_cylinders:
        rows.append(("deactivated at cruise",
                     ", ".join(str(c) for c in engine.deactivated_cylinders)))
    if profile.transmission:
        rows.append(("transmission", profile.transmission))
    if profile.expected_protocol:
        rows.append(("OBD protocol", profile.expected_protocol))
    print(console.table(rows))

    if profile.notes:
        print()
        print(console.heading("Worth knowing"))
        for note in profile.notes:
            print(console.bullet(note))
            print()

    if profile.known_issues:
        print(console.heading("Known issues on this model"))
        for issue in profile.known_issues:
            mark = console.SEVERITY_MARK.get(issue.severity, " -")
            print(f" {console.severity(mark, issue.severity)} "
                  f"{console.paint(issue.title, 'bold')}")
            print(console.wrap(issue.detail))
            triggers = list(issue.codes) + [f"{p}xx" for p in issue.code_prefixes]
            if triggers:
                print(console.wrap(console.paint(
                    "Codes: " + ", ".join(triggers), "dim")))
            print()
    return 0


def _command_vin(args: argparse.Namespace, session: Session | None) -> int:
    raw = args.vin
    if not raw and session is not None:
        raw = session.read_vin()
    if not raw:
        return _fail(
            "the car did not report a VIN (cars built before roughly 2005 often "
            "do not). Pass one directly:  cardiag vin 2B3KA53H66H123456"
        )

    info = vin_module.decode(raw)
    profile = vehicles.match_vin(raw)

    if args.json:
        payload = info.to_dict()
        payload["profile"] = profile.key if profile else None
        print(json.dumps(payload, indent=2))
        return 0 if info.trustworthy else 1

    print(console.heading("VIN"))
    rows = [("vin", info.vin)]
    if info.manufacturer:
        rows.append(("manufacturer", info.manufacturer))
    else:
        rows.append(("manufacturer", f"unknown (world code {info.wmi})"))
    if info.model_year:
        rows.append(("model year", str(info.model_year)))
    if info.engine:
        rows.append(("engine", info.engine))
    if info.plant:
        rows.append(("built at", info.plant))
    if info.serial:
        rows.append(("serial", info.serial))
    rows.append((
        "check digit",
        console.paint("valid", "green") if info.check_digit_ok
        else console.paint("INVALID", "red"),
    ))
    print(console.table(rows))

    if info.problems:
        print()
        for problem in info.problems:
            print(console.bullet(console.paint(problem, "yellow")))

    if profile:
        print()
        print(console.paint(f"Matches profile: {profile.name}", "green"))
        print(console.paint("Run 'cardiag vehicle' to see what that adds.", "dim"))
    return 0 if info.trustworthy else 1


def _command_lookup(args: argparse.Namespace) -> int:
    described = []
    for code in args.codes:
        cleaned = code.strip().upper()
        if not dtc.CODE_PATTERN.match(cleaned):
            return _fail(f"{code!r} is not a valid trouble code (expected something like P0420)")
        described.append(dtc.describe(cleaned))

    if args.json:
        print(json.dumps([item.to_dict() for item in described], indent=2))
        return 0

    for item in described:
        headline = f"{item.code}  {item.description}"
        if len(headline) > console.width():
            print(console.paint(item.code, "bold"))
            print(console.wrap(item.description, indent="  "))
        else:
            print(console.paint(headline, "bold"))
        print(console.wrap(f"{item.system} | {item.origin} | severity: {item.severity}",
                           indent="  "))

        profile = args.profile_object
        if profile is not None:
            located = report_module.locate_code(item.code, profile)
            note = profile.code_notes.get(item.code)
            if located or note:
                print()
                print(f"  On a {profile.name}:")
                for line in (located, note):
                    if line:
                        print(console.wrap(line, indent="    "))

        if item.causes:
            print()
            print("  Likely causes, most common first:")
            for cause in item.causes:
                print(console.bullet(cause, indent="    "))

        if profile is not None:
            issues = profile.issues_for([item.code])
            if issues:
                print()
                print("  Known issues on this model that fit:")
                for issue in issues:
                    print(console.bullet(issue.title, indent="    "))
        print()
    return 0


def _fail(message: str) -> int:
    print(console.paint(f"error: {message}", "red"), file=sys.stderr)
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
