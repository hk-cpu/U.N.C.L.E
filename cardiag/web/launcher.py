"""Launching cardiag as an application rather than a command.

Two things make a local web app feel like a real one: opening in a window with
no browser chrome, and having something to click that is not a terminal. This
module does both, and degrades to an ordinary browser tab wherever it cannot.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

#: Chromium-family browsers support ``--app=URL``, which opens a frameless
#: window. Ordered by how likely they are to already be the user's browser.
APP_MODE_BROWSERS = {
    "darwin": [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    ],
    "win32": [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ],
    "linux": [
        "google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
        "microsoft-edge", "brave-browser", "vivaldi",
    ],
}


def find_app_browser() -> str | None:
    """A browser that can open a frameless window, if one is installed."""
    platform = "darwin" if sys.platform == "darwin" else (
        "win32" if os.name == "nt" else "linux"
    )
    for candidate in APP_MODE_BROWSERS[platform]:
        if os.path.isabs(candidate):
            if Path(candidate).exists():
                return candidate
        else:
            found = shutil.which(candidate)
            if found:
                return found
    return None


def open_app_window(url: str) -> bool:
    """Open ``url`` in a chromeless window. False if that was not possible."""
    browser = find_app_browser()
    if browser is None:
        return False

    try:
        subprocess.Popen(
            [browser, f"--app={url}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=(os.name != "nt"),
        )
    except OSError:
        return False
    return True


def open_browser(url: str, app_window: bool = True) -> str:
    """Open the UI. Returns how it was opened, for the caller to report."""
    if app_window and open_app_window(url):
        return "app window"

    import webbrowser

    try:
        if webbrowser.open(url):
            return "browser tab"
    except Exception:                        # headless box, no browser
        pass
    return "not opened"


# ---------------------------------------------------------------------------
# Desktop shortcuts
# ---------------------------------------------------------------------------

def _command() -> list[str]:
    """The command a shortcut should run, using this same interpreter."""
    return [sys.executable, "-m", "cardiag", "app"]


def install_launcher() -> tuple[Path, str]:
    """Create something clickable that starts cardiag.

    Returns the path written and a sentence describing where it will show up.
    """
    if sys.platform == "darwin":
        return _install_macos()
    if os.name == "nt":
        return _install_windows()
    return _install_linux()


def _install_linux() -> tuple[Path, str]:
    applications = Path.home() / ".local" / "share" / "applications"
    applications.mkdir(parents=True, exist_ok=True)

    icon = Path(__file__).parent / "static" / "icon-512.png"
    executable = " ".join(_quote(part) for part in _command())

    entry = applications / "cardiag.desktop"
    entry.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=cardiag\n"
        "GenericName=Car diagnostics\n"
        "Comment=Read and interpret your car's on-board diagnostics\n"
        f"Exec={executable}\n"
        f"Icon={icon}\n"
        "Terminal=false\n"
        "Categories=Utility;\n"
        "Keywords=obd;obd2;car;diagnostics;\n",
        encoding="utf-8",
    )
    entry.chmod(0o755)
    return entry, "It will appear in your applications menu."


def _install_macos() -> tuple[Path, str]:
    # A full .app bundle needs an .icns, which cannot be generated here without
    # extra tooling. A double-clickable command is honest and works; installing
    # the page as a web app gives the nicer Dock icon.
    target = Path.home() / "Applications"
    target.mkdir(parents=True, exist_ok=True)

    script = target / "cardiag.command"
    executable = " ".join(_quote(part) for part in _command())
    script.write_text(f"#!/bin/sh\nexec {executable}\n", encoding="utf-8")
    script.chmod(0o755)
    return script, (
        "Double-click it in ~/Applications. For a proper Dock icon, open the "
        "app and use the browser's Install option."
    )


def _install_windows() -> tuple[Path, str]:
    start_menu = (Path(os.environ.get("APPDATA", Path.home()))
                  / "Microsoft" / "Windows" / "Start Menu" / "Programs")
    start_menu.mkdir(parents=True, exist_ok=True)

    batch = start_menu / "cardiag.bat"
    executable = " ".join(f'"{part}"' for part in _command())
    # start "" ... detaches so the console window closes straight away.
    batch.write_text(f'@echo off\r\nstart "" {executable}\r\n', encoding="utf-8")
    return batch, "It will appear in the Start menu."


def _quote(part: str) -> str:
    return f'"{part}"' if " " in part else part
