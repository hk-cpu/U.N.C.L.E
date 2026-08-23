"""Command line interface for cardiag."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

from . import __version__, baseline as baseline_module, console, dtc, mode06
from . import pids, vehicles, vin as vin_module
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
    parser.set_defaults(no_freeze=False, no_monitors=False)

    sub = parser.add_subparsers(dest="command")

    sub.add_parser("ports", help="list serial ports that might be an adapter")
    sub.add_parser("info", help="show adapter, protocol and vehicle identity")
    sub.add_parser("monitors", help="show emissions readiness monitors")
    sub.add_parser("freeze", help="show the stored freeze frame")

    scan = sub.add_parser("scan", help="full health check with findings (default)")
    scan.add_argument(
        "--no-freeze", action="store_true", help="skip reading the freeze frame",
    )
    scan.add_argument(
        "--no-monitors", action="store_true",
        help="skip mode 06; faster, but misses faults that have not set a code yet",
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

    ui = sub.add_parser("ui", help="open the browser interface")
    ui.add_argument("--host", default="127.0.0.1",
                    help="address to bind (default: 127.0.0.1, this machine only)")
    ui.add_argument("--ui-port", type=int, default=8765, dest="ui_port",
                    help="port to listen on (default: 8765)")
    ui.add_argument("--no-browser", action="store_true",
                    help="do not open a browser window")

    monitors06 = sub.add_parser(
        "tests", help="mode 06 monitor results, including per-cylinder misfires")
    monitors06.add_argument("--all", action="store_true", dest="all_tests",
                            help="show every monitor, not just the notable ones")

    sub.add_parser("misfires", help="per-cylinder misfire counters")
    sub.add_parser("calibration", help="ECU calibration ID and verification number")

    save = sub.add_parser("baseline", help="save a snapshot to compare against later")
    save.add_argument("label", nargs="?", default=None,
                      help="a name for this snapshot, e.g. 'before-plugs'")
    save.add_argument("--list", action="store_true", dest="list_baselines",
                      help="list saved snapshots instead of taking one")
    save.add_argument("--delete", type=int, metavar="ID",
                      help="delete a saved snapshot")
    save.add_argument("--store", metavar="FILE", help="use a specific database file")

    diff = sub.add_parser("compare", help="compare two snapshots, or one against now")
    diff.add_argument("before", nargs="?", default="latest",
                      help="snapshot id, label, 'latest' or 'first' (default: latest)")
    diff.add_argument("after", nargs="?", default=None,
                      help="the second snapshot; omit to compare against the car now")
    diff.add_argument("--store", metavar="FILE", help="use a specific database file")

    fix = sub.add_parser("fix", help="a guided procedure for a known issue")
    fix.add_argument("issue", nargs="?",
                     help="which issue, e.g. mds-lifter; omit to list them")

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
    if command == "ui":
        # The UI manages its own connection, so it does not take one from here.
        return _command_ui(args)
    if command == "fix" and args.profile_object is not None:
        return _command_fix(args, session=None)

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
        "tests": _command_tests,
        "misfires": _command_misfires,
        "calibration": _command_calibration,
        "baseline": _command_baseline,
        "compare": _command_compare,
        "fix": _command_fix,
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
        include_monitors=not args.no_monitors,
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


def _command_fix(args: argparse.Namespace, session: Session | None) -> int:
    profile = args.profile_object
    if profile is None and session is not None:
        vin = session.read_vin()
        profile = vehicles.match_vin(vin) if vin else None

    if profile is None:
        return _fail(
            "no vehicle profile applies, so there are no model-specific "
            f"procedures. Pick one with --vehicle ({vehicles.describe_choices()})."
        )

    issues = [i for i in profile.known_issues if i.procedure]
    if not args.issue:
        if args.json:
            print(json.dumps([
                {"key": i.key, "title": i.title, "steps": len(i.procedure)}
                for i in issues
            ], indent=2))
            return 0

        print(console.heading(f"Guided procedures for the {profile.name}"))
        print(console.table(
            [(i.key, i.title, f"{len(i.procedure)} steps") for i in issues],
            headers=("issue", "what it is", ""),
        ))
        print()
        print("Walk one through with:  cardiag fix mds-lifter")
        return 0

    wanted = args.issue.strip().lower()
    issue = next((i for i in profile.known_issues if i.key == wanted), None)
    if issue is None:
        known = ", ".join(i.key for i in issues)
        return _fail(f"no procedure named {args.issue!r}. Known: {known}")

    if not issue.procedure:
        return _fail(
            f"{issue.key} has no step-by-step procedure yet; "
            f"'cardiag vehicle' shows what is known about it."
        )

    if args.json:
        print(json.dumps({
            "key": issue.key,
            "title": issue.title,
            "detail": issue.detail,
            "severity": issue.severity,
            "procedure": [step.to_dict() for step in issue.procedure],
        }, indent=2))
        return 0

    print(console.paint(issue.title, "bold"))
    print(console.wrap(issue.detail, indent="  "))
    print()

    for number, step in enumerate(issue.procedure, 1):
        # Hang the wrapped action text under the step text, not the number.
        wrapped = console.wrap(step.action, indent=" " * 4).lstrip()
        print(f" {console.paint(f'{number}.', 'bold')} {wrapped}")
        if step.expect:
            print(console.wrap(console.paint(f"Expect: {step.expect}", "dim"),
                               indent="    "))
        if step.watch:
            names = " ".join(step.watch)
            print(console.wrap(
                console.paint(f"Watch live:  cardiag live {names}", "cyan"),
                indent="    "))
        if step.caution:
            print(console.wrap(console.severity(f"Caution: {step.caution}",
                                                "serious"), indent="    "))
        print()

    print(console.paint(
        "Take a baseline before you change anything, so you can prove whether "
        "it helped:  cardiag baseline before-" + issue.key, "dim"))
    return 0


def _command_tests(args: argparse.Namespace, session: Session) -> int:
    tests = session.monitor_tests()

    if args.json:
        print(json.dumps([test.to_dict() for test in tests], indent=2))
        return 0

    if not tests:
        print("This ECU returned no mode 06 results.")
        print(console.paint(
            "Some cars only answer mode 06 after a drive cycle has run the "
            "monitors. Try again after a mixed drive.", "dim"))
        return 0

    notable = [t for t in tests if _is_notable(t)]
    shown = tests if args.all_tests else (notable or tests)

    print(console.heading(f"{len(shown)} of {len(tests)} monitor results"))
    rows = []
    for test in shown:
        verdict = {True: "pass", False: "FAIL", None: "-"}[test.passed]
        headroom = "" if test.headroom is None else f"{test.headroom * 100:.0f} %"
        rows.append((test.monitor, test.format_value(), test.format_limits(),
                     verdict, headroom))
    print(console.table(rows, headers=("monitor", "measured", "limits",
                                       "result", "of limit")))

    if not args.all_tests and notable and len(notable) < len(tests):
        print()
        print(console.paint(
            f"{len(tests) - len(notable)} monitors with plenty of margin hidden; "
            "use --all to see them.", "dim"))
    return 0


def _is_notable(test) -> bool:
    """Worth showing by default: failing, close to failing, or a misfire count."""
    if test.passed is False:
        return True
    if mode06.misfire_cylinder(test.mid) is not None:
        return True
    headroom = test.headroom
    return headroom is not None and headroom >= 0.6


def _command_misfires(args: argparse.Namespace, session: Session) -> int:
    counts = session.misfire_counts()
    profile = args.profile_object
    if profile is None:
        vin = session.read_vin()
        profile = vehicles.match_vin(vin) if vin else None

    if args.json:
        print(json.dumps({
            "counts": {str(k): v for k, v in counts.items()},
            "deactivated_cylinders": (
                list(profile.engine.deactivated_cylinders) if profile else []
            ),
        }, indent=2))
        return 0

    if not counts:
        print("This ECU did not report per-cylinder misfire counters.")
        return 0

    deactivated = set(profile.engine.deactivated_cylinders) if profile else set()
    highest = max(counts.values()) or 1
    width = 28

    print(console.heading("Misfire counts by cylinder"))
    for cylinder in sorted(counts):
        count = counts[cylinder]
        bar = console.bar(count, 0, highest, width)
        marker = "  (deactivated at cruise)" if cylinder in deactivated else ""
        colour = "yellow" if cylinder in deactivated else "cyan"
        print(f"  cylinder {cylinder}  {str(count).rjust(6)}  "
              f"{console.paint(bar, colour)}{console.paint(marker, 'dim')}")

    print()
    print(console.wrap(
        "Counts are only meaningful against each other. One cylinder well above "
        "the rest is a fault on that cylinder; a whole group standing out points "
        "at what that group shares.", indent="  "))
    return 0


def _command_calibration(args: argparse.Namespace, session: Session) -> int:
    calibration = session.calibration()

    if args.json:
        print(json.dumps(calibration, indent=2))
        return 0

    ids = calibration.get("calibration_ids") or []
    cvns = calibration.get("verification_numbers") or []

    if not ids and not cvns:
        print("This ECU did not report a calibration ID.")
        return 0

    print(console.heading("ECU calibration"))
    rows = []
    for index, value in enumerate(ids, 1):
        rows.append((f"calibration ID {index}" if len(ids) > 1 else "calibration ID",
                     value))
    for index, value in enumerate(cvns, 1):
        rows.append((f"verification number {index}" if len(cvns) > 1
                     else "verification number", value))
    print(console.table(rows))

    print()
    print(console.wrap(
        "This identifies the software running in the ECU. Save it with "
        "'cardiag baseline' before you change anything: if it differs later, "
        "the module was reflashed.", indent="  "))
    return 0


def _open_store(args: argparse.Namespace):
    return baseline_module.BaselineStore(getattr(args, "store", None))


def _command_baseline(args: argparse.Namespace, session: Session) -> int:
    with _open_store(args) as store:
        if args.delete is not None:
            if store.delete(args.delete):
                print(f"Deleted snapshot #{args.delete}.")
                return 0
            return _fail(f"no snapshot with id {args.delete}")

        if args.list_baselines:
            return _list_baselines(args, store)

        label = args.label or time.strftime("%Y-%m-%d %H:%M")
        if not args.json:
            print(console.paint("Taking a full snapshot...", "dim"))

        report = report_module.build(
            session,
            profile=args.profile_object,
            detect_profile=not getattr(args, "vehicle_disabled", False),
        )
        snapshot = store.save(report.to_dict(), label)

        if args.json:
            print(json.dumps(snapshot.to_dict(), indent=2))
            return 0

        print(console.paint(f"Saved snapshot #{snapshot.id} as {label!r}.", "green"))
        print(console.paint(f"Stored in {store.path}", "dim"))
        print()
        print(f"Compare against it later with:  cardiag compare {snapshot.id}")
        return 0


def _list_baselines(args: argparse.Namespace, store) -> int:
    snapshots = store.list()

    if args.json:
        print(json.dumps([s.to_dict() for s in snapshots], indent=2))
        return 0

    if not snapshots:
        print("No snapshots saved yet. Take one with:  cardiag baseline before-work")
        return 0

    print(console.heading(f"{len(snapshots)} saved snapshots"))
    print(console.table([
        (str(s.id), s.when, s.label, s.vin or "-", s.headline or "-")
        for s in snapshots
    ], headers=("id", "taken", "label", "vin", "state")))
    return 0


def _command_compare(args: argparse.Namespace, session: Session) -> int:
    with _open_store(args) as store:
        try:
            before = store.resolve(args.before)
        except KeyError as exc:
            return _fail(str(exc.args[0]))

        if args.after:
            try:
                after = store.resolve(args.after)
            except KeyError as exc:
                return _fail(str(exc.args[0]))
            after_label = f"snapshot #{after.id} ({after.label})"
            after_payload = after
        else:
            if not args.json:
                print(console.paint("Reading the car now...", "dim"))
            live = report_module.build(
                session,
                profile=args.profile_object,
                detect_profile=not getattr(args, "vehicle_disabled", False),
            )
            after_payload = baseline_module.Snapshot(
                id=0, taken_at=live.generated_at, label="now",
                vin=live.vehicle.vin,
                profile=live.profile.name if live.profile else None,
                headline=live.headline, worst_severity=live.worst_severity,
                payload=live.to_dict(),
            )
            after_label = "the car right now"

        changes = baseline_module.compare(before, after_payload)

    if args.json:
        print(json.dumps({
            "before": before.to_dict(),
            "after": after_payload.to_dict(),
            "summary": baseline_module.summarise(changes),
            "changes": [change.to_dict() for change in changes],
        }, indent=2))
        return 1 if any(c.direction == "worse" for c in changes) else 0

    print(console.paint(
        f"snapshot #{before.id} ({before.label}, {before.when})  ->  {after_label}",
        "bold"))
    print(console.paint(baseline_module.summarise(changes), "dim"))
    print()

    if not changes:
        return 0

    colours = {"worse": "red", "better": "green", "neutral": "cyan"}
    marks = {"worse": " ↑", "better": " ↓", "neutral": " ·"}
    for change in changes:
        mark = console.paint(marks[change.direction], colours[change.direction])
        print(f" {mark} {console.paint(change.label, 'bold')}")
        print(console.wrap(f"{change.before}  ->  {change.after}"))
        if change.note:
            print(console.wrap(console.paint(change.note, "dim")))
        print()

    return 1 if any(c.direction == "worse" for c in changes) else 0


def _command_ui(args: argparse.Namespace) -> int:
    from .web.server import run

    try:
        run(host=args.host, port=args.ui_port, open_browser=not args.no_browser)
    except OSError as exc:
        return _fail(
            f"could not start the UI on {args.host}:{args.ui_port}: {exc}\n"
            "Another copy may already be running; try --ui-port 8766."
        )
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
