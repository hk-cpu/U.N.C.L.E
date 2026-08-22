"""A small local web server for the cardiag UI.

Standard library only, so installing cardiag still pulls in nothing but
pyserial. It binds to localhost by default; binding anywhere else requires a
token, because the API can clear trouble codes and nothing else authenticates it.
"""

from __future__ import annotations

import json
import secrets
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .service import ServiceError, VehicleService

STATIC_ROOT = Path(__file__).parent / "static"

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".json": "application/json",
}

LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}


class CardiagServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, service: VehicleService, token: str | None):
        super().__init__(address, handler)
        self.service = service
        self.token = token


class Handler(BaseHTTPRequestHandler):
    server_version = "cardiag"
    protocol_version = "HTTP/1.1"

    # -- plumbing ----------------------------------------------------------
    def log_message(self, fmt, *args):  # noqa: A002 - stdlib signature
        # The default logs every request to stderr, which buries the one line
        # the user actually needs (the URL to open).
        pass

    @property
    def service(self) -> VehicleService:
        return self.server.service        # type: ignore[attr-defined]

    def _authorised(self, query: dict) -> bool:
        token = self.server.token          # type: ignore[attr-defined]
        if token is None:
            return True
        if self.client_address[0] in LOCAL_HOSTS:
            return True
        supplied = self.headers.get("X-Cardiag-Token") or (query.get("token") or [None])[0]
        return bool(supplied) and secrets.compare_digest(supplied, token)

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        # The UI is served from this same origin; nothing else needs access.
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, payload, status: int = HTTPStatus.OK) -> None:
        self._send(status, json.dumps(payload).encode("utf-8"), "application/json")

    def _error(self, message: str, status: int = HTTPStatus.BAD_REQUEST) -> None:
        self._json({"error": message}, status=status)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, UnicodeDecodeError):
            return {}

    # -- routing -----------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802 - stdlib signature
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)

        if not self._authorised(query):
            self._error("missing or invalid token", HTTPStatus.FORBIDDEN)
            return

        if parsed.path.startswith("/api/"):
            self._api_get(parsed.path, query)
            return
        self._static(parsed.path)

    do_HEAD = do_GET

    def do_POST(self) -> None:  # noqa: N802 - stdlib signature
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)

        if not self._authorised(query):
            self._error("missing or invalid token", HTTPStatus.FORBIDDEN)
            return

        if not parsed.path.startswith("/api/"):
            self._error("not found", HTTPStatus.NOT_FOUND)
            return

        body = self._body()
        try:
            self._api_post(parsed.path, body)
        except ServiceError as exc:
            self._error(str(exc))

    def _api_get(self, path: str, query: dict) -> None:
        try:
            if path == "/api/status":
                self._json(self.service.status())
            elif path == "/api/ports":
                self._json({"ports": _list_ports()})
            elif path == "/api/scan":
                self._json(self.service.scan())
            elif path == "/api/codes":
                self._json(self.service.codes())
            elif path == "/api/freeze":
                self._json(self.service.freeze())
            elif path == "/api/monitors":
                self._json(self.service.monitors())
            elif path == "/api/vehicle":
                self._json(self.service.vehicle())
            elif path == "/api/channels":
                self._json({"channels": self.service.available_channels()})
            elif path == "/api/live":
                self._json(self.service.live_snapshot())
            else:
                self._error("no such endpoint", HTTPStatus.NOT_FOUND)
        except ServiceError as exc:
            self._error(str(exc))
        except Exception as exc:                      # keep the UI alive
            self._error(f"{type(exc).__name__}: {exc}", HTTPStatus.INTERNAL_SERVER_ERROR)

    def _api_post(self, path: str, body: dict) -> None:
        if path == "/api/connect":
            url = (body.get("url") or "").strip()
            if not url:
                raise ServiceError("choose an adapter to connect to")
            self._json(self.service.connect(
                url,
                timeout=float(body.get("timeout") or 5.0),
                vehicle=body.get("vehicle"),
            ))
        elif path == "/api/disconnect":
            self._json(self.service.disconnect())
        elif path == "/api/live/start":
            self._json(self.service.start_live(body.get("channels")))
        elif path == "/api/live/stop":
            self._json(self.service.stop_live())
        elif path == "/api/clear":
            if body.get("confirm") != "clear":
                raise ServiceError("clearing codes needs an explicit confirmation")
            self._json(self.service.clear_codes())
        else:
            self._error("no such endpoint", HTTPStatus.NOT_FOUND)

    # -- static files ------------------------------------------------------
    def _static(self, path: str) -> None:
        relative = "index.html" if path in ("/", "") else path.lstrip("/")
        target = (STATIC_ROOT / relative).resolve()

        # Never serve anything outside the static directory.
        if not target.is_file() or STATIC_ROOT.resolve() not in target.parents:
            self._error("not found", HTTPStatus.NOT_FOUND)
            return

        content_type = CONTENT_TYPES.get(target.suffix, "application/octet-stream")
        self._send(HTTPStatus.OK, target.read_bytes(), content_type)


def _list_ports() -> list[dict]:
    from ..transport.serial_link import _looks_like_obd, list_ports

    try:
        found = list_ports()
    except Exception:                                  # pyserial missing, etc.
        found = []
    return [
        {"device": device, "description": desc, "likely": _looks_like_obd((device, desc))}
        for device, desc in found
    ]


def serve(host: str = "127.0.0.1", port: int = 8765,
          service: VehicleService | None = None) -> tuple[CardiagServer, str]:
    """Create the server. Returns it plus the URL to open.

    The caller starts it; that keeps the tests free to run it on a thread.
    """
    service = service or VehicleService()
    # A token is only enforced for non-local clients, so the common case stays
    # a plain click-through URL.
    token = None if host in LOCAL_HOSTS else secrets.token_urlsafe(16)

    server = CardiagServer((host, port), Handler, service, token)
    shown_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    url = f"http://{shown_host}:{server.server_address[1]}/"
    if token:
        url += f"?token={token}"
    return server, url


def run(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    """Blocking entry point used by ``cardiag ui``."""
    server, url = serve(host=host, port=port)

    print(f"cardiag UI running at {url}")
    if server.token:
        print("Reachable from other devices on this network; the token above is "
              "required. Anyone with it can clear your trouble codes.")
    print("Press Ctrl-C to stop.")

    if open_browser:
        threading.Timer(0.4, _open, args=(url,)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        server.service.disconnect()
        server.shutdown()
        server.server_close()


def _open(url: str) -> None:
    import webbrowser

    try:
        webbrowser.open(url)
    except Exception:                                  # headless box, no browser
        pass
