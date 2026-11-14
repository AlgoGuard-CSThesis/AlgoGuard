"""Logic of the AlgoGuard launcher window: status, settings, commands. Standard library only.

The window (algoguard_launcher.pyw) runs before the venv folder exists, so nothing here may
import a project module or a third-party package. The settings file is judged by the
installation's own ``config.py``; this module only reads and writes its two cloud lines.
"""
from __future__ import annotations

import os
import platform
import re
import socket
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

PYTHON_VERSION = "3.14.6"
URL_NAME = "NEXT_PUBLIC_SUPABASE_URL"
KEY_NAME = "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY"
DEFAULT_PORT = 5000


class SettingsError(ValueError):
    """A value the settings file must not hold; the message is for the person typing it."""


def _lines(path: Path) -> list[str]:
    return path.read_bytes().decode("utf-8-sig").splitlines(keepends=True) if path.exists() else []


def _value(lines: list[str], name: str) -> str:
    for line in lines:
        match = re.match(rf"\s*{re.escape(name)}\s*=(.*)$", line.rstrip("\r\n"))
        if match:
            return match.group(1).strip()
    return ""


def read_settings(folder) -> tuple[str, bool]:
    """The saved project URL and whether a key is saved. The key itself is never returned."""
    lines = _lines(Path(folder) / ".env")
    return _value(lines, URL_NAME), bool(_value(lines, KEY_NAME))


def configured_port(folder) -> int:
    try:
        return int(_value(_lines(Path(folder) / ".env"), "ALGOGUARD_PORT"))
    except ValueError:
        return DEFAULT_PORT


def write_settings(folder, url: str, key: str) -> None:
    """Save the two cloud settings, keeping every other line of ``.env`` as it is."""
    folder, url, key = Path(folder), url.strip(), key.strip()
    if key.startswith("sb_secret_"):
        raise SettingsError("That is a secret key. Enter the publishable key, which starts with "
                            "sb_publishable_.")
    if not url.startswith("https://"):
        raise SettingsError("The project URL starts with https://.")
    if not key.startswith("sb_publishable_"):
        raise SettingsError("The publishable key starts with sb_publishable_.")
    env = folder / ".env"
    lines = _lines(env if env.exists() else folder / ".env.example")
    newline = "\r\n" if any(line.endswith("\r\n") for line in lines) else "\n"
    for name, value in ((URL_NAME, url), (KEY_NAME, key)):
        pattern = re.compile(rf"\s*{re.escape(name)}\s*=")
        for index, line in enumerate(lines):
            if pattern.match(line):
                lines[index] = f"{name}={value}{newline}"
                break
        else:
            if lines and not lines[-1].endswith(("\n", "\r")):
                lines[-1] += newline
            lines.append(f"{name}={value}{newline}")
    temporary = env.with_name(".env.part")
    temporary.write_bytes("".join(lines).encode("utf-8"))
    os.replace(temporary, env)


NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_VERSION_SCRIPT = "import platform; print(platform.python_version())"


@dataclass(frozen=True)
class Check:
    ok: bool
    text: str


@dataclass(frozen=True)
class Status:
    python: Check
    packages: Check
    settings: Check
    npcap: Check
    running: Check

    def rows(self) -> list[tuple[str, Check]]:
        return [("Python", self.python), ("Packages", self.packages),
                ("Settings", self.settings), ("Npcap (live capture)", self.npcap),
                ("AlgoGuard", self.running)]


@dataclass(frozen=True)
class Step:
    label: str
    command: list[str]


def venv_python(folder) -> Path:
    return Path(folder) / "venv" / "Scripts" / "python.exe"


def python_version_of(executable) -> str:
    """The version an interpreter reports, or an empty string if it cannot be run."""
    try:
        result = subprocess.run([str(executable), "-c", _VERSION_SCRIPT], capture_output=True,
                                text=True, timeout=30, creationflags=NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def check_settings(folder) -> tuple[int | None, str]:
    """Run the installation's own settings check: 0 ready, 2 invalid, 3 cloud settings missing."""
    python = venv_python(folder)
    if not python.exists():
        return None, "Run Set up first."
    try:
        result = subprocess.run([str(python), "config.py"], cwd=str(folder), capture_output=True,
                                text=True, timeout=60, creationflags=NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as error:
        return None, f"The settings could not be checked: {error}"
    lines = [line.strip() for line in (result.stdout + result.stderr).splitlines() if line.strip()]
    return result.returncode, lines[-1] if lines else "The settings check gave no message."


def port_in_use(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def status(folder, *, current_version=None, system_root=None) -> Status:
    """What is ready in this AlgoGuard folder, as five checks for the window."""
    folder = Path(folder)
    version = current_version or platform.python_version()
    python = Check(version == PYTHON_VERSION,
                   f"{version} found" if version == PYTHON_VERSION
                   else f"AlgoGuard needs Python {PYTHON_VERSION}; this is {version}")
    installed = python_version_of(venv_python(folder)) if venv_python(folder).exists() else ""
    if installed == PYTHON_VERSION:
        packages = Check(True, "Installed")
        code, message = check_settings(folder)
        settings = Check(code == 0, message)
    elif installed:
        packages = Check(False, f"The venv folder was made with Python {installed}. Delete it, "
                                "then press Set up")
        settings = Check(False, "Checked after Set up")
    else:
        packages = Check(False, "Not installed yet: press Set up")
        url, has_key = read_settings(folder)
        settings = Check(False, "Entered; checked after Set up" if url and has_key
                         else "Not entered yet: press Settings")
    root = system_root or os.environ.get("SystemRoot", r"C:\Windows")
    found = (Path(root) / "System32" / "Npcap" / "wpcap.dll").exists()
    npcap = Check(found, "Found" if found
                  else "Not found: live capture needs it; replays work without it")
    port = configured_port(folder)
    listening = port_in_use(port)
    running = Check(listening,
                    f"Running on http://127.0.0.1:{port}" if listening else "Not running")
    return Status(python, packages, settings, npcap, running)


def setup_steps(folder, base_python=None) -> list[Step]:
    """The commands SETUP_ALGOGUARD.cmd runs, minus the ones already done."""
    base = str(base_python or Path(sys.executable).with_name("python.exe"))
    python = venv_python(folder)
    steps = []
    if not (python.exists() and python_version_of(python) == PYTHON_VERSION):
        steps.append(Step("Creating the Python environment", [base, "-m", "venv", "venv"]))
    steps.append(Step("Installing AlgoGuard's Python packages (a few minutes the first time)",
                      [str(python), "-m", "pip", "install", "--disable-pip-version-check",
                       "-r", "requirements.txt"]))
    return steps


def start_command(folder) -> list[str]:
    return [str(venv_python(folder)), "app.py"]


def running_url(line: str) -> str | None:
    match = re.search(r"Running on (http://[\w.\-:\[\]]+)", line)
    return match.group(1) if match else None
