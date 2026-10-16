"""Run Margin in the background on macOS with launchd (the Mac's built-in service manager).

Two LaunchAgents are installed in ~/Library/LaunchAgents:
  com.margin.server  starts the web app at login and restarts it if it stops
  com.margin.daily   runs `margin daily` every morning: import from Apple Books, then email the digest
LaunchAgents run as you, only while you're logged in, and need no admin rights.
"""

import os
import plistlib
import subprocess
import sys
from pathlib import Path

SERVER_LABEL = "com.margin.server"
DAILY_LABEL = "com.margin.daily"
AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
PACKAGE_ROOT = Path(__file__).resolve().parent.parent  # the folder that contains margin/


def build_plists(python: str, db: str, port: int, hour: int, minute: int, log_dir: Path) -> dict[str, dict]:
    """launchd job definitions, keyed by label. Pure function so it can be tested anywhere."""
    base = [python, "-m", "margin", "--db", db]
    common = {
        "WorkingDirectory": str(PACKAGE_ROOT),
        # launchd starts jobs with a bare environment; give Python a sane PATH and UTF-8 output.
        "EnvironmentVariables": {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin:/opt/homebrew/bin",
                                 "PYTHONIOENCODING": "utf-8"},
    }
    return {
        SERVER_LABEL: {
            "Label": SERVER_LABEL,
            "ProgramArguments": base + ["serve", "--port", str(port)],
            "RunAtLoad": True,
            "KeepAlive": True,
            "ThrottleInterval": 30,  # if it keeps crashing, wait between restarts
            "StandardOutPath": str(log_dir / "server.log"),
            "StandardErrorPath": str(log_dir / "server.log"),
            **common,
        },
        DAILY_LABEL: {
            "Label": DAILY_LABEL,
            "ProgramArguments": base + ["daily"],
            # If the Mac is asleep at this time, launchd runs the job when it wakes.
            "StartCalendarInterval": {"Hour": hour, "Minute": minute},
            "StandardOutPath": str(log_dir / "daily.log"),
            "StandardErrorPath": str(log_dir / "daily.log"),
            **common,
        },
    }


def parse_time(value: str) -> tuple[int, int]:
    try:
        hour, minute = (int(x) for x in value.split(":"))
    except ValueError:
        raise ValueError(f"time must look like 06:00, got {value!r}")
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise ValueError(f"time must be between 00:00 and 23:59, got {value!r}")
    return hour, minute


def _launchctl(*args) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True)


def _require_mac():
    if sys.platform != "darwin":
        raise RuntimeError("background running uses launchd, which is only available on macOS")


def install(db: str, port: int, time: str) -> list[str]:
    """Write and (re)load both agents. Safe to re-run: existing agents are replaced."""
    _require_mac()
    hour, minute = parse_time(time)
    log_dir = Path(db).expanduser().resolve().parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    domain = f"gui/{os.getuid()}"
    installed = []
    for label, plist in build_plists(sys.executable, str(Path(db).expanduser().resolve()),
                                     port, hour, minute, log_dir).items():
        path = AGENTS_DIR / f"{label}.plist"
        _launchctl("bootout", f"{domain}/{label}")  # ignore "not loaded"
        with open(path, "wb") as f:
            plistlib.dump(plist, f)
        result = _launchctl("bootstrap", domain, str(path))
        if result.returncode != 0:
            raise RuntimeError(f"launchctl couldn't load {label}: {result.stderr.strip() or result.stdout.strip()}")
        installed.append(str(path))
    return installed


def uninstall() -> list[str]:
    _require_mac()
    domain = f"gui/{os.getuid()}"
    removed = []
    for label in (SERVER_LABEL, DAILY_LABEL):
        _launchctl("bootout", f"{domain}/{label}")
        path = AGENTS_DIR / f"{label}.plist"
        if path.exists():
            path.unlink()
            removed.append(str(path))
    return removed


def python_app_path() -> str:
    """The binary macOS checks for Full Disk Access when Python runs in the background.

    python.org installs re-launch a Python.app inside the framework; Homebrew and others run the
    interpreter file itself.
    """
    app = Path(sys.base_prefix) / "Resources" / "Python.app"
    return str(app) if app.exists() else os.path.realpath(sys.executable)
