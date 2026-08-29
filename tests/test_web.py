"""Web UI tests: the service layer and the HTTP API, against the simulator."""

import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from cardiag.web.server import serve
from cardiag.web.service import ServiceError, VehicleService

CHARGER = "sim://?profile=charger-misfire"


# ---------------------------------------------------------------------------
# Service layer
# ---------------------------------------------------------------------------

@pytest.fixture
def service():
    svc = VehicleService()
    yield svc
    svc.disconnect()


def test_starts_disconnected(service):
    status = service.status()
    assert status["connected"] is False
    assert "charger-rt-2006" in status["profiles"]


def test_operations_before_connecting_are_refused(service):
    for call in (service.scan, service.codes, service.freeze, service.monitors):
        with pytest.raises(ServiceError, match="not connected"):
            call()


def test_connect_detects_the_profile_from_the_vin(service):
    status = service.connect(CHARGER)
    assert status["connected"] is True
    assert status["profile_key"] == "charger-rt-2006"
    assert "CAN" in status["protocol"]


def test_connect_can_be_forced_generic(service):
    status = service.connect(CHARGER, vehicle="none")
    assert status["profile"] is None


def test_connect_rejects_an_unknown_profile(service):
    with pytest.raises(ServiceError, match="unknown vehicle profile"):
        service.connect(CHARGER, vehicle="delorean")


def test_connect_reports_a_bad_url(service):
    with pytest.raises(ServiceError):
        service.connect("sim://?profile=nonsense")


def test_scan_carries_profile_aware_findings(service):
    service.connect(CHARGER)
    report = service.scan()
    assert report["headline"] == "Check-engine light is ON"
    assert report["profile"]["engine"]["total_plugs"] == 16
    assert any("MDS lifter" in f["title"] for f in report["findings"])


def test_codes_include_model_notes(service):
    service.connect(CHARGER)
    payload = service.codes()
    assert {c["code"] for c in payload["codes"]} == {"P0300", "P0304", "P0174", "P0430"}

    notes = payload["profile_notes"]["P0304"]
    assert "bank 2" in notes["located"]
    assert "MDS lifter or camshaft lobe failure" in notes["issues"]


def test_codes_have_no_notes_without_a_profile(service):
    service.connect(CHARGER, vehicle="none")
    assert service.codes()["profile_notes"] == {}


def test_freeze_frame_is_labelled(service):
    service.connect(CHARGER)
    frame = service.freeze()["frame"]
    assert frame["RPM"]["description"] == "Engine speed"
    assert frame["trigger_code"]["formatted"] == "P0300"


def test_clear_codes(service):
    service.connect(CHARGER)
    assert service.codes()["codes"]
    service.clear_codes()
    assert service.codes()["codes"] == []


def test_live_sampling_collects_history(service):
    service.connect(CHARGER)
    started = service.start_live()
    assert "RPM" in started["channels"]

    # Give the sampler a few passes.
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        snapshot = service.live_snapshot()
        if snapshot["samples"] >= 3:
            break
        time.sleep(0.1)

    snapshot = service.live_snapshot()
    assert snapshot["sampling"] is True
    assert snapshot["samples"] >= 3

    rpm = next(c for c in snapshot["channels"] if c["name"] == "RPM")
    assert rpm["value"] is not None
    assert len(rpm["history"]) >= 3
    assert rpm["range_high"] == 8000        # display range, not the protocol's

    service.stop_live()
    assert service.live_snapshot()["sampling"] is False


def test_live_flags_a_lean_bank(service):
    service.connect(CHARGER)
    service.start_live(["LONG_FUEL_TRIM_2"])
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        channels = service.live_snapshot()["channels"]
        if channels and channels[0]["value"] is not None:
            break
        time.sleep(0.1)

    channel = service.live_snapshot()["channels"][0]
    assert channel["value"] > 15
    assert channel["severity"] == "warning"
    service.stop_live()


def test_gauges_start_only_the_cluster_channels(service):
    service.connect(CHARGER)
    started = service.start_gauges()

    # The build guide's gauge v1 list: rpm, speed, coolant, oil, volts, intake.
    assert started["channels"][:2] == ["RPM", "SPEED"]
    assert "COOLANT_TEMP" in started["channels"]
    # Nothing a dial does not show - a cluster polls fewer channels, faster.
    assert "LONG_FUEL_TRIM_2" not in started["channels"]
    service.stop_live()


def test_gauges_sample_faster_than_the_live_view(service):
    from cardiag.web.service import GAUGE_INTERVAL, SAMPLE_INTERVAL

    service.connect(CHARGER)
    service.start_gauges()
    assert service._interval == GAUGE_INTERVAL
    assert GAUGE_INTERVAL < SAMPLE_INTERVAL
    service.stop_live()


def test_gauges_carry_the_engine_limits_the_dial_needs(service):
    service.connect(CHARGER)
    engine = service.start_gauges()["engine"]

    assert engine["redline_rpm"] == 5800
    assert engine["shift_rpm"] < engine["redline_rpm"]
    # The dial paints the coolant readout from these.
    assert engine["coolant_warning"] == 110.0
    assert engine["coolant_critical"] == 118.0
    service.stop_live()


def test_gauge_limits_are_empty_without_a_profile(service):
    service.connect(CHARGER, vehicle="none")
    engine = service.start_gauges()["engine"]

    # No profile means no invented redline - the dial falls back on its own.
    assert engine["redline_rpm"] is None
    service.stop_live()


def test_gauges_skip_channels_the_car_does_not_answer(service):
    """A car without an oil temperature sensor still gets a cluster."""
    service.connect("sim://?profile=default")
    started = service.start_gauges()

    assert "RPM" in started["channels"]
    supported = {entry["name"] for entry in service.available_channels()}
    assert set(started["channels"]) <= supported
    service.stop_live()


def test_live_rejects_channels_the_car_does_not_have(service):
    service.connect(CHARGER)
    with pytest.raises(ServiceError, match="none of the requested channels"):
        service.start_live(["HYBRID_BATTERY_LIFE"])


def test_disconnect_stops_sampling(service):
    service.connect(CHARGER)
    service.start_live()
    service.disconnect()
    assert service.status()["connected"] is False
    assert service.live_snapshot()["sampling"] is False


def test_vehicle_includes_decoded_vin(service):
    service.connect(CHARGER)
    payload = service.vehicle()
    assert payload["vin"]["model_year"] == 2006
    assert payload["vin"]["check_digit_ok"] is True
    assert payload["profile"]["engine"]["deactivated_cylinders"] == [1, 4, 6, 7]


# ---------------------------------------------------------------------------
# HTTP API
# ---------------------------------------------------------------------------

@pytest.fixture
def http():
    server, url = serve(host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def call(path, body=None, method=None):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            base + path, data=data, method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    call.base = base
    yield call

    server.service.disconnect()
    server.shutdown()
    server.server_close()


def test_serves_the_page_and_its_assets(http):
    for path, marker in (("/", b"<title>cardiag"), ("/app.css", b"--critical"),
                         ("/app.js", b"cardiag UI")):
        with urllib.request.urlopen(http.base + path, timeout=10) as response:
            assert response.status == 200
            assert marker in response.read()


def test_unknown_paths_are_not_found(http):
    assert http("/nope")[0] == 404
    assert http("/api/nope")[0] == 404


def test_path_traversal_is_refused(http):
    # Whether the client or the server normalises it, the file must not be served.
    status, _ = http("/../pyproject.toml")
    assert status == 404


def test_api_reports_not_connected(http):
    status, payload = http("/api/status")
    assert status == 200
    assert payload["connected"] is False

    status, payload = http("/api/scan")
    assert status == 400
    assert "not connected" in payload["error"]


def test_connect_and_scan_over_http(http):
    status, payload = http("/api/connect", {"url": CHARGER})
    assert status == 200
    assert payload["profile_key"] == "charger-rt-2006"

    status, report = http("/api/scan")
    assert status == 200
    assert report["headline"] == "Check-engine light is ON"


def test_connect_needs_a_url(http):
    status, payload = http("/api/connect", {})
    assert status == 400
    assert "choose an adapter" in payload["error"]


def test_clear_requires_confirmation(http):
    http("/api/connect", {"url": CHARGER})

    status, payload = http("/api/clear", {})
    assert status == 400
    assert "confirmation" in payload["error"]

    status, _ = http("/api/clear", {"confirm": "clear"})
    assert status == 200
    assert http("/api/codes")[1]["codes"] == []


def test_live_endpoints_over_http(http):
    http("/api/connect", {"url": CHARGER})
    status, payload = http("/api/live/start", {})
    assert status == 200
    assert payload["channels"]

    time.sleep(1.0)
    status, snapshot = http("/api/live")
    assert snapshot["samples"] >= 1

    assert http("/api/live/stop", {})[0] == 200


def test_gauges_endpoint_over_http(http):
    http("/api/connect", {"url": CHARGER})
    status, payload = http("/api/gauges/start", {})

    assert status == 200
    assert payload["channels"][0] == "RPM"
    assert payload["engine"]["redline_rpm"] == 5800
    http("/api/live/stop", {})


def test_gauges_endpoint_needs_a_car(http):
    status, payload = http("/api/gauges/start", {})
    assert status == 400
    assert "not connected" in payload["error"]


def test_a_browser_that_hangs_up_does_not_raise():
    """Closing the window mid-poll must not print a traceback at the user."""
    from cardiag.web.server import Handler

    handler = Handler.__new__(Handler)
    handler.close_connection = False

    def explode(*args, **kwargs):
        raise BrokenPipeError(32, "Broken pipe")

    handler._send_now = explode
    handler._send(200, b"{}", "application/json")     # must not raise
    assert handler.close_connection is True


def test_other_write_failures_still_surface():
    from cardiag.web.server import Handler

    handler = Handler.__new__(Handler)
    handler.close_connection = False

    def explode(*args, **kwargs):
        raise OSError("the disk is on fire")

    handler._send_now = explode
    with pytest.raises(OSError):
        handler._send(200, b"{}", "application/json")


def test_ports_endpoint_always_answers(http):
    status, payload = http("/api/ports")
    assert status == 200
    assert isinstance(payload["ports"], list)


def test_remote_binding_requires_a_token():
    # Binding beyond localhost enables the token; the API can clear codes.
    server, url = serve(host="0.0.0.0", port=0)
    try:
        assert server.token is not None
        assert f"token={server.token}" in url
    finally:
        server.server_close()


def test_local_binding_needs_no_token():
    server, url = serve(host="127.0.0.1", port=0)
    try:
        assert server.token is None
        assert "token=" not in url
    finally:
        server.server_close()


# ---------------------------------------------------------------------------
# Mode 06, procedures and baselines over the API
# ---------------------------------------------------------------------------

def test_service_monitor_tests(service):
    service.connect("sim://?profile=charger-wear")
    payload = service.monitor_tests()

    assert payload["deactivated_cylinders"] == [1, 4, 6, 7]
    assert payload["misfire_counts"]["4"] == 28
    assert any("Catalyst" in t["monitor"] for t in payload["tests"])


def test_service_calibration(service):
    service.connect(CHARGER)
    assert service.calibration()["calibration_ids"] == ["68RT0057AA"]


def test_service_procedures(service):
    service.connect(CHARGER)
    payload = service.procedures()
    keys = {issue["key"] for issue in payload["issues"]}
    assert {"mds-lifter", "manifold-bolts", "oil-pressure"} <= keys
    assert all(issue["procedure"] for issue in payload["issues"])


def test_service_procedures_without_a_profile(service):
    service.connect(CHARGER, vehicle="none")
    assert service.procedures()["issues"] == []


def test_service_baselines_round_trip(service, tmp_path):
    service.baseline_path = tmp_path / "b.db"
    service.connect("sim://?profile=charger")

    saved = service.save_baseline("before")
    assert saved["label"] == "before"
    assert [s["label"] for s in service.list_baselines()["snapshots"]] == ["before"]

    comparison = service.compare_baseline(saved["id"])
    assert comparison["changes"] == []

    service.delete_baseline(saved["id"])
    assert service.list_baselines()["snapshots"] == []


def test_service_compare_detects_degradation(service, tmp_path):
    service.baseline_path = tmp_path / "b.db"
    service.connect("sim://?profile=charger")
    saved = service.save_baseline("healthy")

    # Reconnect as the worn car; the baseline store persists across it.
    service.connect("sim://?profile=charger-wear")
    comparison = service.compare_baseline(saved["id"])

    labels = [c["label"] for c in comparison["changes"]]
    assert any("cylinder 4" in label for label in labels)
    assert comparison["changes"][0]["direction"] == "worse"


def test_service_compare_rejects_a_missing_snapshot(service, tmp_path):
    service.baseline_path = tmp_path / "b.db"
    service.connect(CHARGER)
    with pytest.raises(ServiceError, match="no snapshot"):
        service.compare_baseline(999)


def test_http_tests_endpoint(http):
    http("/api/connect", {"url": "sim://?profile=charger-wear"})
    status, payload = http("/api/tests")
    assert status == 200
    assert payload["misfire_counts"]["4"] == 28


def test_http_calibration_endpoint(http):
    http("/api/connect", {"url": CHARGER})
    status, payload = http("/api/calibration")
    assert status == 200
    assert payload["calibration"]["calibration_ids"] == ["68RT0057AA"]


def test_http_procedures_endpoint(http):
    http("/api/connect", {"url": CHARGER})
    status, payload = http("/api/procedures")
    assert status == 200
    assert any(i["key"] == "mds-lifter" for i in payload["issues"])


def test_http_baseline_save_needs_a_label(http):
    http("/api/connect", {"url": CHARGER})
    status, payload = http("/api/baselines/save", {})
    assert status == 400
    assert "name" in payload["error"]


def test_http_baseline_compare_needs_an_id(http):
    http("/api/connect", {"url": CHARGER})
    status, _ = http("/api/baselines/compare", {})
    assert status == 400


# ---------------------------------------------------------------------------
# Installable app: manifest, icons, service worker
# ---------------------------------------------------------------------------

def test_manifest_is_served_with_the_right_type(http):
    request = urllib.request.Request(http.base + "/manifest.webmanifest")
    with urllib.request.urlopen(request, timeout=10) as response:
        assert response.status == 200
        assert response.headers["Content-Type"] == "application/manifest+json"
        manifest = json.loads(response.read())

    assert manifest["display"] == "standalone"
    assert manifest["start_url"] == "/"
    purposes = {icon["purpose"] for icon in manifest["icons"]}
    assert "maskable" in purposes and "any" in purposes


def test_manifest_shortcuts_point_at_real_views(http):
    with urllib.request.urlopen(http.base + "/manifest.webmanifest", timeout=10) as r:
        manifest = json.loads(r.read())

    for shortcut in manifest["shortcuts"]:
        assert shortcut["url"].startswith("/?view=")


def test_icons_are_valid_pngs(http):
    for name in ("icon-192.png", "icon-512.png", "icon-maskable-512.png",
                 "apple-touch-icon.png"):
        with urllib.request.urlopen(f"{http.base}/{name}", timeout=10) as response:
            assert response.status == 200
            assert response.headers["Content-Type"] == "image/png"
            assert response.read(8) == b"\x89PNG\r\n\x1a\n"


def test_service_worker_is_served_uncached_and_root_scoped(http):
    with urllib.request.urlopen(http.base + "/sw.js", timeout=10) as response:
        assert response.status == 200
        assert response.headers["Service-Worker-Allowed"] == "/"
        # A cached service worker can pin an old app shell in place.
        assert response.headers["Cache-Control"] == "no-store"


def test_the_page_links_the_manifest_and_apple_icon(http):
    with urllib.request.urlopen(http.base + "/", timeout=10) as response:
        body = response.read().decode()

    assert 'rel="manifest"' in body
    assert 'rel="apple-touch-icon"' in body
    assert 'name="theme-color"' in body


def test_static_assets_may_be_cached_but_vehicle_data_never_is(http):
    with urllib.request.urlopen(http.base + "/icon-192.png", timeout=10) as response:
        assert "max-age" in response.headers["Cache-Control"]

    with urllib.request.urlopen(http.base + "/api/status", timeout=10) as response:
        assert response.headers["Cache-Control"] == "no-store"


# ---------------------------------------------------------------------------
# Launcher
# ---------------------------------------------------------------------------

def test_launcher_writes_a_desktop_entry(tmp_path, monkeypatch):
    from cardiag.web import launcher

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(launcher.Path, "home", classmethod(lambda cls: tmp_path))

    path, note = launcher.install_launcher()
    assert path.exists()
    assert note

    content = path.read_text()
    # Whatever the platform, the shortcut has to name the app command.
    assert "cardiag" in content
    assert "app" in content


def test_app_browser_detection_does_not_raise():
    from cardiag.web.launcher import find_app_browser

    # Either a browser is present or it is not; neither may explode.
    assert find_app_browser() is None or isinstance(find_app_browser(), str)


def test_open_browser_reports_how_it_opened(monkeypatch):
    from cardiag.web import launcher

    monkeypatch.setattr(launcher, "open_app_window", lambda url: True)
    assert launcher.open_browser("http://x", app_window=True) == "app window"

    monkeypatch.setattr(launcher, "open_app_window", lambda url: False)
    monkeypatch.setitem(__import__("sys").modules, "webbrowser",
                        type("W", (), {"open": staticmethod(lambda url: True)}))
    assert launcher.open_browser("http://x", app_window=True) == "browser tab"
