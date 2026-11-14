# Launcher Window and Default Capture Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give non-technical users one double-click window that sets up, configures, starts and stops AlgoGuard, and make "Default interface" capture the adapter that carries the PC's internet traffic (DEF-28).

**Architecture:** A standard-library-only logic module (`launcher_core.py`) does every check and command; a thin Tkinter window (`algoguard_launcher.pyw`) draws and calls it; `ALGOGUARD.cmd` is the double-click entry. Separately, `services/traffic_source_service.py` gains `default_capture_interface()`, which follows scapy's route to the internet instead of scapy's own default adapter.

**Tech Stack:** Python 3.14.6, Tkinter 8.6 (standard library), scapy 2.8.0, pytest, Ruff (line length 100, rules E, F, W, I), Pyright, Windows `cmd`.

**Spec:** `docs/superpowers/specs/2026-11-14-launcher-window-design.md`

## Global Constraints

- `launcher_core.py` and `algoguard_launcher.pyw` import the standard library only, and no project module: they run before the `venv` folder exists.
- The user installs Python 3.14.6 first. Nothing is bundled or installed for them.
- `SETUP_ALGOGUARD.cmd` and `START_ALGOGUARD.cmd` are not changed or removed.
- Release stays `0.6.0` (`app_version.py` is not edited).
- The publishable key is never returned by a read function, never shown after saving, never logged. No password is handled anywhere.
- A key starting `sb_secret_` is refused before anything is written.
- No IP address of real traffic, e-mail address, key or `.env` content goes into any committed file or test.
- Lines are at most 100 characters; `venv\Scripts\python.exe -m ruff check .` must pass.
- Commits: on `main`, **no `Co-Authored-By` or any AI attribution**, and only after the Lead Architect has seen the batch and said yes. Each task's last step names its commit; show it, wait, then commit.
- Run commands from `C:\Users\sanmi\AlgoGuard` in PowerShell. Tests: `venv\Scripts\python.exe -m pytest -q -p no:cacheprovider <path>`.
- Do not run CPU-heavy commands while the Lead Architect is doing a timed capture on PC A.

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `services/traffic_source_service.py` | Modify | `default_capture_interface()`; `LiveCaptureSource` uses it; interface list puts it first |
| `services/live_monitor_service.py` | Modify | `get_options()` reports the default adapter's label; default `source_name` is the resolved adapter |
| `cloud_monitor.py` | Modify | default `source_name` is the resolved adapter |
| `templates/monitor.html` | Modify | first option names the default adapter |
| `tests/test_traffic_sources.py` | Modify | tests for the four changes above |
| `launcher_core.py` | Create | status, settings, commands, parsing; no Tkinter |
| `algoguard_launcher.pyw` | Create | the window |
| `ALGOGUARD.cmd` | Create | double-click entry |
| `tests/test_launcher.py` | Create | tests for the core and a window smoke test |
| `scripts/build_analyst_package.py` | Modify | ship the three new files |
| `tests/test_analyst_package.py` | Modify | the three files are in the zip |
| `docs/ANALYST_GUIDE.md`, `ALGOGUARD_USER_MANUAL.txt`, `README.md`, `docs/MAINTAINER_GUIDE.md` | Modify | the window is the main route |

---

### Task 1: DEF-28, the default capture adapter follows the internet route

**Files:**
- Modify: `services/traffic_source_service.py` (add a function above `class LiveCaptureSource`; one line in `LiveCaptureSource.__init__`; the sort in `list_capture_interfaces`)
- Test: `tests/test_traffic_sources.py`

**Interfaces:**
- Produces: `default_capture_interface() -> str | None` in `services.traffic_source_service`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_traffic_sources.py`. The file already imports the module as `traffic_sources`; if the names below are not imported at the top, add them to the existing import from `services.traffic_source_service`.

```python
def _fake_route(monkeypatch, device, interfaces):
    """Replace scapy's route to the internet and its interface table."""
    from types import SimpleNamespace

    from scapy.config import conf

    table = {name: SimpleNamespace(name=name, description=description, ip=ip)
             for name, description, ip in interfaces}
    monkeypatch.setattr(conf.route, "route",
                        lambda *args, **kwargs: (device, "192.0.2.5", "192.0.2.1"))
    monkeypatch.setattr(conf.ifaces, "dev_from_networkname", lambda name: table[name])
    monkeypatch.setattr(conf.ifaces, "values", lambda: list(table.values()))


def test_default_capture_interface_follows_the_route_to_the_internet(monkeypatch):
    _fake_route(monkeypatch, "Wi-Fi", [("VPN", "A VPN adapter", "198.51.100.7"),
                                         ("Wi-Fi", "Wireless adapter", "192.0.2.5")])
    assert traffic_sources.default_capture_interface() == "Wi-Fi"


def test_default_capture_interface_is_none_when_it_cannot_be_resolved(monkeypatch):
    from scapy.config import conf

    def broken(*args, **kwargs):
        raise OSError("no route table")

    monkeypatch.setattr(conf.route, "route", broken)
    assert traffic_sources.default_capture_interface() is None


def test_default_capture_interface_ignores_the_loopback_adapter(monkeypatch):
    _fake_route(monkeypatch, "Loopback", [("Loopback", "Software Loopback", "127.0.0.1")])
    assert traffic_sources.default_capture_interface() is None


def test_live_capture_without_a_choice_uses_the_default_adapter(monkeypatch):
    monkeypatch.setattr(traffic_sources, "default_capture_interface", lambda: "Wi-Fi")
    assert traffic_sources.LiveCaptureSource("")._interface == "Wi-Fi"
    assert traffic_sources.LiveCaptureSource(None)._interface == "Wi-Fi"
    assert traffic_sources.LiveCaptureSource("Ethernet")._interface == "Ethernet"


def test_live_capture_keeps_no_interface_when_the_default_is_unknown(monkeypatch):
    monkeypatch.setattr(traffic_sources, "default_capture_interface", lambda: None)
    assert traffic_sources.LiveCaptureSource("")._interface is None


def test_the_interface_list_puts_the_default_adapter_first(monkeypatch):
    _fake_route(monkeypatch, "Wi-Fi", [("VPN", "A VPN adapter", "198.51.100.7"),
                                         ("Wi-Fi", "Wireless adapter", "192.0.2.5")])
    monkeypatch.setattr(traffic_sources, "live_capture_available", lambda: (True, None))
    values = [item["value"] for item in traffic_sources.list_capture_interfaces()]
    assert values == ["Wi-Fi", "VPN"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests\test_traffic_sources.py -k "default_capture or default_adapter or default_is_unknown"`
Expected: FAIL, `AttributeError: module 'services.traffic_source_service' has no attribute 'default_capture_interface'`.

- [ ] **Step 3: Implement**

In `services/traffic_source_service.py`, add this function directly above `class LiveCaptureSource`:

```python
def default_capture_interface():
    """The adapter that carries the PC's route to the internet, or ``None`` (DEF-28).

    Scapy's own default is its first working adapter, which on a PC with a VPN adapter
    installed is often the VPN: a capture on it sees local discovery packets and none of
    the PC's traffic. The route to 0.0.0.0 names the adapter the traffic really leaves by.
    """
    try:
        from scapy.config import conf

        device = conf.route.route("0.0.0.0")[0]
        iface = conf.ifaces.dev_from_networkname(device) if isinstance(device, str) else device
        name = getattr(iface, "name", None)
        if not name or getattr(iface, "ip", "") == "127.0.0.1":
            return None
        return str(name)
    except Exception:
        return None
```

In `LiveCaptureSource.__init__`, replace `self._interface = interface` with:

```python
        self._interface = interface or default_capture_interface()
```

In `list_capture_interfaces`, replace the `entries.sort(...)` line with:

```python
        default = default_capture_interface()
        entries.sort(key=lambda item: (item["value"] != default,
                                       item["ip"] in ("", "127.0.0.1"), item["label"]))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests\test_traffic_sources.py`
Expected: all pass. If `LiveCaptureSource(...)` needs a fixture for its state folder, give the two `LiveCaptureSource` tests the same fixture arguments the nearest existing `LiveCaptureSource` test in this file uses.

- [ ] **Step 5: Lint**

Run: `venv\Scripts\python.exe -m ruff check services\traffic_source_service.py tests\test_traffic_sources.py`
Expected: `All checks passed!`

- [ ] **Step 6: Commit (after the Lead Architect's yes)**

```
git add services/traffic_source_service.py tests/test_traffic_sources.py
git commit -m "fix: Default interface captures the adapter that carries the internet route (DEF-28)"
```

---

### Task 2: DEF-28, the page and the log name the adapter

**Files:**
- Modify: `services/live_monitor_service.py` (`get_options`, and the default `source_name` near line 775)
- Modify: `cloud_monitor.py` (the default `source_name` near line 212)
- Modify: `templates/monitor.html:72`
- Test: `tests/test_traffic_sources.py`

**Interfaces:**
- Consumes: `default_capture_interface() -> str | None` from Task 1.
- Produces: `get_options()["default_interface"]`, a label string or `None`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_traffic_sources.py` (the file already imports `services.live_monitor_service` as `monitor`):

```python
def test_options_name_the_default_adapter(monkeypatch):
    monkeypatch.setattr(monitor, "live_capture_available", lambda: (True, None))
    monkeypatch.setattr(monitor, "list_capture_interfaces", lambda: [
        {"value": "Wi-Fi", "label": "Wireless adapter"}, {"value": "VPN", "label": "A VPN adapter"}])
    monkeypatch.setattr(monitor, "default_capture_interface", lambda: "Wi-Fi")
    assert monitor.get_options()["default_interface"] == "Wireless adapter"
    monkeypatch.setattr(monitor, "default_capture_interface", lambda: None)
    assert monitor.get_options()["default_interface"] is None
```

- [ ] **Step 2: Run it to verify it fails**

Run: `venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests\test_traffic_sources.py -k options_name_the_default`
Expected: FAIL, `AttributeError` for `default_capture_interface` on the monitor module.

- [ ] **Step 3: Implement**

`services/live_monitor_service.py`: add `default_capture_interface` to the existing import from `services.traffic_source_service` (the one that already lists `list_capture_interfaces`). In `get_options`, replace the first lines and the `"interfaces"` entry:

```python
    live_ok, live_reason = live_capture_available()
    interfaces = list_capture_interfaces() if live_ok else []
    default_name = default_capture_interface() if live_ok else None
    default_label = next((item["label"] for item in interfaces if item["value"] == default_name),
                         default_name)
    return {
        "sources": [{"value": value, "label": label} for value, label in SOURCE_CHOICES.items()],
        "datasets": [{"value": value, "label": label} for value, label in DATASET_CHOICES.items()],
        "captures": list_capture_files(),
        "interfaces": interfaces,
        "default_interface": default_label,
```

(the rest of the returned dictionary is unchanged).

In the same file, near line 775, replace
`source_name = str(interface).strip() if interface else None` with:

```python
            source_name = str(interface).strip() if interface else default_capture_interface()
```

`cloud_monitor.py`: add `default_capture_interface` to its import from `services.traffic_source_service` (the one that provides `live_capture_available`), and near line 212 make the same replacement:

```python
            source_name = str(interface).strip() if interface else default_capture_interface()
```

`templates/monitor.html`, line 72, replace the option with:

```html
                <option value="">Default interface{% if options.default_interface %} ({{ options.default_interface }}){% endif %}</option>
```

- [ ] **Step 4: Run the monitor tests**

Run: `venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests\test_traffic_sources.py tests\test_live_monitor.py tests\test_cloud_app.py`
Expected: all pass. A test that asserted `source_name is None` for a default live capture must now expect the resolved name; monkeypatch `default_capture_interface` to return `None` in that test to keep its meaning.

- [ ] **Step 5: Lint and type check**

Run: `venv\Scripts\python.exe -m ruff check .` then `npm run typecheck`
Expected: both clean.

- [ ] **Step 6: Commit (after the Lead Architect's yes)**

```
git add services/live_monitor_service.py cloud_monitor.py templates/monitor.html tests/test_traffic_sources.py
git commit -m "fix: the Live Monitor and its log name the default capture adapter (DEF-28)"
```

---

### Task 3: `launcher_core`, settings

**Files:**
- Create: `launcher_core.py`
- Test: `tests/test_launcher.py`

**Interfaces:**
- Produces: `SettingsError(ValueError)`; `read_settings(folder) -> tuple[str, bool]`; `write_settings(folder, url, key) -> None`; `configured_port(folder) -> int`; constants `URL_NAME`, `KEY_NAME`, `DEFAULT_PORT`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_launcher.py`:

```python
"""The launcher window's logic (launcher_core): settings, status, commands."""

import pytest

import launcher_core as core

EXAMPLE = ("# AlgoGuard settings\r\n"
           "NEXT_PUBLIC_SUPABASE_URL=\r\n"
           "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=\r\n"
           "\r\n"
           "ALGOGUARD_HOST=127.0.0.1\r\n"
           "ALGOGUARD_PORT=5000\r\n"
           "# ALGOGUARD_DB_MODE=sqlite\r\n")
URL = "https://project.supabase.co"
KEY = "sb_publishable_abc123"


@pytest.fixture()
def folder(tmp_path):
    (tmp_path / ".env.example").write_bytes(EXAMPLE.encode())
    return tmp_path


def test_settings_are_empty_before_setup(folder):
    assert core.read_settings(folder) == ("", False)


def test_saving_creates_env_from_the_example_and_changes_only_two_lines(folder):
    core.write_settings(folder, f"  {URL} ", f"{KEY}\n")
    saved = (folder / ".env").read_bytes().decode()
    assert saved == EXAMPLE.replace("URL=\r\n", f"URL={URL}\r\n").replace(
        "KEY=\r\n", f"KEY={KEY}\r\n")
    assert core.read_settings(folder) == (URL, True)


def test_saving_again_keeps_other_edits(folder):
    core.write_settings(folder, URL, KEY)
    env = folder / ".env"
    env.write_bytes(env.read_bytes().replace(b"ALGOGUARD_PORT=5000", b"ALGOGUARD_PORT=5001"))
    core.write_settings(folder, "https://other.supabase.co", "sb_publishable_new")
    text = env.read_bytes().decode()
    assert "ALGOGUARD_PORT=5001" in text and "URL=https://other.supabase.co\r\n" in text
    assert text.count("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=") == 1
    assert core.configured_port(folder) == 5001


def test_missing_lines_are_added(tmp_path):
    (tmp_path / ".env").write_text("ALGOGUARD_PORT=5000\n", encoding="utf-8")
    core.write_settings(tmp_path, URL, KEY)
    assert core.read_settings(tmp_path) == (URL, True)


@pytest.mark.parametrize("url, key, problem", [
    (URL, "sb_secret_abcdefgh", "secret key"),
    ("http://project.supabase.co", KEY, "https://"),
    ("", KEY, "https://"),
    (URL, "eyJhbGciOi", "sb_publishable_"),
    (URL, "", "sb_publishable_"),
])
def test_bad_values_are_refused_and_nothing_is_written(folder, url, key, problem):
    with pytest.raises(core.SettingsError, match=problem):
        core.write_settings(folder, url, key)
    assert not (folder / ".env").exists()


def test_the_key_is_never_returned(folder):
    core.write_settings(folder, URL, KEY)
    assert KEY not in repr(core.read_settings(folder))


def test_port_defaults_when_unset_or_wrong(tmp_path):
    assert core.configured_port(tmp_path) == 5000
    (tmp_path / ".env").write_text("ALGOGUARD_PORT=abc\n", encoding="utf-8")
    assert core.configured_port(tmp_path) == 5000
```

- [ ] **Step 2: Run them to verify they fail**

Run: `venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests\test_launcher.py`
Expected: FAIL, `ModuleNotFoundError: No module named 'launcher_core'`.

- [ ] **Step 3: Implement**

Create `launcher_core.py`:

```python
"""Logic of the AlgoGuard launcher window: status, settings, commands. Standard library only.

The window (algoguard_launcher.pyw) runs before the venv folder exists, so nothing here may
import a project module or a third-party package. The settings file is judged by the
installation's own ``config.py``; this module only reads and writes its two cloud lines.
"""
from __future__ import annotations

import os
import re
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests\test_launcher.py`
Expected: all pass.

- [ ] **Step 5: Lint**

Run: `venv\Scripts\python.exe -m ruff check launcher_core.py tests\test_launcher.py`
Expected: `All checks passed!`

- [ ] **Step 6: Commit (after the Lead Architect's yes)**

```
git add launcher_core.py tests/test_launcher.py
git commit -m "feat: launcher settings: save the two cloud settings without touching the rest of .env"
```

---

### Task 4: `launcher_core`, status and commands

**Files:**
- Modify: `launcher_core.py`
- Test: `tests/test_launcher.py`

**Interfaces:**
- Consumes: `read_settings`, `configured_port`, `PYTHON_VERSION` from Task 3.
- Produces:
  - `Check(ok: bool, text: str)` and `Status(python, packages, settings, npcap, running)`, both dataclasses; `Status.rows() -> list[tuple[str, Check]]` in display order.
  - `venv_python(folder) -> Path`
  - `python_version_of(executable) -> str` (empty string when it cannot run)
  - `check_settings(folder) -> tuple[int | None, str]`
  - `port_in_use(port: int) -> bool`
  - `status(folder, *, current_version=None, system_root=None) -> Status`
  - `Step(label: str, command: list[str])`; `setup_steps(folder, base_python=None) -> list[Step]`
  - `start_command(folder) -> list[str]`
  - `running_url(line: str) -> str | None`
  - `NO_WINDOW: int` (subprocess creation flag)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_launcher.py`:

```python
import socket
import sys


def test_venv_python_path(tmp_path):
    assert core.venv_python(tmp_path) == tmp_path / "venv" / "Scripts" / "python.exe"


def test_python_version_of_reports_this_interpreter_and_blank_for_a_missing_one(tmp_path):
    import platform

    assert core.python_version_of(sys.executable) == platform.python_version()
    assert core.python_version_of(tmp_path / "missing.exe") == ""


def test_status_of_a_fresh_folder(folder, monkeypatch):
    monkeypatch.setattr(core, "port_in_use", lambda port: False)
    state = core.status(folder, current_version="3.14.6", system_root=str(folder))
    assert state.python.ok and "3.14.6" in state.python.text
    assert not state.packages.ok and "Set up" in state.packages.text
    assert not state.settings.ok
    assert not state.npcap.ok and "replay" in state.npcap.text.lower()
    assert not state.running.ok
    assert [name for name, _ in state.rows()] == [
        "Python", "Packages", "Settings", "Npcap (live capture)", "AlgoGuard"]


def test_status_reports_a_wrong_python_and_a_running_app(folder, monkeypatch):
    monkeypatch.setattr(core, "port_in_use", lambda port: port == 5000)
    state = core.status(folder, current_version="3.13.1", system_root=str(folder))
    assert not state.python.ok and "3.14.6" in state.python.text
    assert state.running.ok and "5000" in state.running.text


def test_status_uses_the_settings_check_once_packages_exist(folder, monkeypatch):
    monkeypatch.setattr(core, "port_in_use", lambda port: False)
    monkeypatch.setattr(core, "python_version_of", lambda executable: "3.14.6")
    monkeypatch.setattr(core, "check_settings", lambda where: (0, "Settings OK."))
    core.venv_python(folder).parent.mkdir(parents=True)
    core.venv_python(folder).write_bytes(b"")
    npcap = folder / "System32" / "Npcap"
    npcap.mkdir(parents=True)
    (npcap / "wpcap.dll").write_bytes(b"")
    state = core.status(folder, current_version="3.14.6", system_root=str(folder))
    assert state.packages.ok and state.settings.ok and state.settings.text == "Settings OK."
    assert state.npcap.ok


def test_check_settings_without_packages_asks_for_setup(folder):
    assert core.check_settings(folder) == (None, "Run Set up first.")


def test_setup_steps_create_the_environment_then_install(folder, monkeypatch):
    steps = core.setup_steps(folder, base_python="C:\\Python\\python.exe")
    assert [step.command for step in steps] == [
        ["C:\\Python\\python.exe", "-m", "venv", "venv"],
        [str(core.venv_python(folder)), "-m", "pip", "install", "--disable-pip-version-check",
         "-r", "requirements.txt"]]
    monkeypatch.setattr(core, "python_version_of", lambda executable: "3.14.6")
    core.venv_python(folder).parent.mkdir(parents=True)
    core.venv_python(folder).write_bytes(b"")
    assert len(core.setup_steps(folder, base_python="C:\\Python\\python.exe")) == 1


def test_start_command(folder):
    assert core.start_command(folder) == [str(core.venv_python(folder)), "app.py"]


@pytest.mark.parametrize("line, url", [
    (" * Running on http://127.0.0.1:5000", "http://127.0.0.1:5000"),
    ("Running on http://127.0.0.1:5001 (Press CTRL+C to quit)", "http://127.0.0.1:5001"),
    ("Starting AlgoGuard.", None),
])
def test_running_url(line, url):
    assert core.running_url(line) == url


def test_port_in_use():
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        assert core.port_in_use(port) is True
    assert core.port_in_use(port) is False
```

- [ ] **Step 2: Run them to verify they fail**

Run: `venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests\test_launcher.py`
Expected: the Task 3 tests pass; the new ones FAIL with `AttributeError` on `launcher_core`.

- [ ] **Step 3: Implement**

In `launcher_core.py`, extend the imports to:

```python
import os
import platform
import re
import socket
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
```

and add below `write_settings`:

```python
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
    running = Check(listening, f"Running on http://127.0.0.1:{port}" if listening else "Not running")
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests\test_launcher.py`
Expected: all pass.

- [ ] **Step 5: Lint and type check**

Run: `venv\Scripts\python.exe -m ruff check launcher_core.py tests\test_launcher.py` then `npm run typecheck`
Expected: both clean.

- [ ] **Step 6: Commit (after the Lead Architect's yes)**

```
git add launcher_core.py tests/test_launcher.py
git commit -m "feat: launcher status checks and the setup and start commands"
```

---

### Task 5: the window and its entry file

**Files:**
- Create: `algoguard_launcher.pyw`
- Create: `ALGOGUARD.cmd`
- Test: `tests/test_launcher.py`

**Interfaces:**
- Consumes: everything `launcher_core` produces in Tasks 3 and 4.
- Produces: `LauncherWindow(root, folder)` with attributes `status_labels: dict[str, tkinter.Label]` and `buttons: dict[str, tkinter.Button]` (keys `"setup"`, `"settings"`, `"start"`, `"stop"`, `"open"`, `"admin"`), and `main()`.

- [ ] **Step 1: Write the failing smoke test**

Append to `tests/test_launcher.py`:

```python
def test_the_window_builds_shows_five_checks_and_closes(folder, monkeypatch):
    import importlib.machinery
    import importlib.util
    from pathlib import Path

    tkinter = pytest.importorskip("tkinter")
    path = Path(core.__file__).with_name("algoguard_launcher.pyw")
    loader = importlib.machinery.SourceFileLoader("algoguard_launcher", str(path))
    spec = importlib.util.spec_from_loader("algoguard_launcher", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    monkeypatch.setattr(core, "port_in_use", lambda port: False)
    try:
        root = tkinter.Tk()
    except tkinter.TclError:
        pytest.skip("no display for Tkinter")
    try:
        root.withdraw()
        window = module.LauncherWindow(root, folder)
        window.refresh()
        root.update()
        assert list(window.status_labels) == [
            "Python", "Packages", "Settings", "Npcap (live capture)", "AlgoGuard"]
        assert set(window.buttons) == {"setup", "settings", "start", "stop", "open", "admin"}
        assert str(window.buttons["start"]["state"]) == "disabled"
    finally:
        root.destroy()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests\test_launcher.py -k window_builds`
Expected: FAIL, `FileNotFoundError` for `algoguard_launcher.pyw`.

- [ ] **Step 3: Write the window**

Create `algoguard_launcher.pyw`:

```python
"""AlgoGuard launcher window: set up, enter the settings, start and stop AlgoGuard.

Opened by ALGOGUARD.cmd. Standard library only; all logic is in launcher_core.py.
"""
import ctypes
import queue
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk

import launcher_core as core

OK, NOT_OK = "#1f9d55", "#c0392b"


class LauncherWindow:
    def __init__(self, root, folder):
        self.root, self.folder = root, Path(folder)
        self.process = None
        self.url = None
        self.busy = False
        self.messages = queue.Queue()
        root.title("AlgoGuard")
        root.resizable(False, False)
        root.protocol("WM_DELETE_WINDOW", self.close)
        frame = ttk.Frame(root, padding=14)
        frame.grid()
        ttk.Label(frame, text="AlgoGuard", font=("Segoe UI", 14, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))
        self.status_labels = {}
        names = [name for name, _ in core.Status(*[core.Check(False, "")] * 5).rows()]
        for row, name in enumerate(names, start=1):
            ttk.Label(frame, text=name).grid(row=row, column=0, sticky="w", padx=(0, 12))
            label = tk.Label(frame, text="...", anchor="w", justify="left", wraplength=360)
            label.grid(row=row, column=1, columnspan=2, sticky="w")
            self.status_labels[name] = label
        actions = ttk.Frame(frame)
        actions.grid(row=7, column=0, columnspan=3, sticky="w", pady=(12, 4))
        specs = [("setup", "Set up", self.set_up), ("settings", "Settings...", self.settings),
                 ("start", "Start", self.start), ("stop", "Stop", self.stop),
                 ("open", "Open AlgoGuard", self.open_browser)]
        self.buttons = {}
        for column, (key, text, command) in enumerate(specs):
            self.buttons[key] = tk.Button(actions, text=text, width=14, command=command)
            self.buttons[key].grid(row=0, column=column, padx=(0, 6))
        self.buttons["admin"] = tk.Button(
            frame, text="Restart as administrator (for live capture)", command=self.elevate)
        self.buttons["admin"].grid(row=8, column=0, columnspan=3, sticky="w", pady=(4, 8))
        self.progress = ttk.Progressbar(frame, mode="indeterminate", length=520)
        self.progress.grid(row=9, column=0, columnspan=3, sticky="we")
        self.log = scrolledtext.ScrolledText(frame, width=74, height=12, state="disabled",
                                             font=("Consolas", 9))
        self.log.grid(row=10, column=0, columnspan=3, pady=(8, 0))
        self.refresh()
        root.after(200, self.pump)

    # -- display ---------------------------------------------------------------------
    def say(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text.rstrip() + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def refresh(self):
        state = core.status(self.folder)
        for name, check in state.rows():
            self.status_labels[name].configure(text=check.text, fg=OK if check.ok else NOT_OK)
        ours = self.process is not None and self.process.poll() is None
        ready = state.python.ok and state.packages.ok and state.settings.ok
        enabled = {
            "setup": state.python.ok and not self.busy and not ours,
            "settings": not self.busy and not ours,
            "start": ready and not self.busy and not ours and not state.running.ok,
            "stop": ours,
            "open": state.running.ok,
            "admin": not self.busy and not ours,
        }
        for key, on in enabled.items():
            self.buttons[key].configure(state="normal" if on else "disabled")
        if state.running.ok and not ours and self.url is None:
            self.url = f"http://127.0.0.1:{core.configured_port(self.folder)}"
        return state

    def pump(self):
        """Apply what the worker threads reported; runs on the window's own thread."""
        try:
            while True:
                kind, value = self.messages.get_nowait()
                if kind == "log":
                    self.say(value)
                elif kind == "ready":
                    self.url = value
                    self.say("AlgoGuard is ready. Opening your browser...")
                    webbrowser.open(value)
                    self.refresh()
                elif kind == "done":
                    self.busy = False
                    self.progress.stop()
                    self.refresh()
        except queue.Empty:
            pass
        self.root.after(200, self.pump)

    # -- actions ---------------------------------------------------------------------
    def set_up(self):
        self.busy = True
        self.progress.start(12)
        self.refresh()
        threading.Thread(target=self._run_setup, daemon=True).start()

    def _run_setup(self):
        try:
            for step in core.setup_steps(self.folder):
                self.messages.put(("log", step.label + "..."))
                process = subprocess.Popen(step.command, cwd=str(self.folder),
                                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                           text=True, creationflags=core.NO_WINDOW)
                for line in process.stdout:
                    self.messages.put(("log", line))
                if process.wait() != 0:
                    self.messages.put(("log", "Setup did not finish. Check the internet "
                                              "connection and press Set up again."))
                    return
            self.messages.put(("log", "Setup finished. Enter the settings, then press Start."))
        except OSError as error:
            self.messages.put(("log", f"Setup could not run: {error}"))
        finally:
            self.messages.put(("done", None))

    def settings(self):
        url, has_key = core.read_settings(self.folder)
        dialog = tk.Toplevel(self.root)
        dialog.title("AlgoGuard settings")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        body = ttk.Frame(dialog, padding=14)
        body.grid()
        ttk.Label(body, text="Enter the two values your administrator gave you.").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        ttk.Label(body, text="Project URL").grid(row=1, column=0, sticky="w")
        url_box = ttk.Entry(body, width=58)
        url_box.insert(0, url)
        url_box.grid(row=1, column=1, pady=2)
        ttk.Label(body, text="Publishable key").grid(row=2, column=0, sticky="w")
        key_box = ttk.Entry(body, width=58)
        key_box.grid(row=2, column=1, pady=2)
        ttk.Label(body, text="A key is saved. Leave the box empty to keep it." if has_key
                  else "No key saved yet. It starts with sb_publishable_.").grid(
            row=3, column=1, sticky="w")
        result = tk.Label(body, text="", anchor="w", justify="left", wraplength=420)
        result.grid(row=4, column=0, columnspan=2, sticky="w", pady=(8, 0))

        def save():
            key = key_box.get().strip()
            if not key and has_key and url_box.get().strip() == url:
                dialog.destroy()
                return
            if not key:
                result.configure(text="Enter the publishable key as well.", fg=NOT_OK)
                return
            try:
                core.write_settings(self.folder, url_box.get(), key)
            except (core.SettingsError, OSError) as error:
                result.configure(text=str(error), fg=NOT_OK)
                return
            code, message = core.check_settings(self.folder)
            result.configure(text="Saved. " + message, fg=OK if code in (0, None) else NOT_OK)
            self.say("Settings saved. " + message)
            self.refresh()

        buttons = ttk.Frame(body)
        buttons.grid(row=5, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(buttons, text="Save", command=save).grid(row=0, column=0, padx=(0, 6))
        ttk.Button(buttons, text="Close", command=dialog.destroy).grid(row=0, column=1)
        url_box.focus_set()

    def start(self):
        try:
            self.process = subprocess.Popen(
                core.start_command(self.folder), cwd=str(self.folder), stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, creationflags=core.NO_WINDOW)
        except OSError as error:
            self.say(f"AlgoGuard could not start: {error}")
            return
        self.url = None
        self.say("Starting AlgoGuard...")
        threading.Thread(target=self._follow, args=(self.process,), daemon=True).start()
        self.refresh()

    def _follow(self, process):
        announced = False
        for line in process.stdout:
            self.messages.put(("log", line))
            url = core.running_url(line)
            if url and not announced:
                announced = True
                self.messages.put(("ready", url))
        process.wait()
        self.messages.put(("log", "AlgoGuard has stopped."))
        self.messages.put(("done", None))

    def stop(self):
        if self.process is not None and self.process.poll() is None:
            subprocess.run(["taskkill", "/PID", str(self.process.pid), "/T", "/F"],
                           capture_output=True, creationflags=core.NO_WINDOW)
        self.url = None
        self.root.after(600, self.refresh)

    def open_browser(self):
        webbrowser.open(self.url or f"http://127.0.0.1:{core.configured_port(self.folder)}")

    def elevate(self):
        script = str(Path(__file__).resolve())
        result = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, f'"{script}"', str(self.folder), 1)
        if result > 32:
            self.root.destroy()
        else:
            self.say("Windows did not start AlgoGuard as administrator.")

    def close(self):
        if self.process is not None and self.process.poll() is None:
            if not messagebox.askyesno(
                    "AlgoGuard", "AlgoGuard is running. Closing this window stops it.\n"
                    "Records waiting to upload are kept.\n\nStop AlgoGuard and close?"):
                return
            self.stop()
        self.root.destroy()


def main():
    root = tk.Tk()
    LauncherWindow(root, Path(__file__).resolve().parent)
    root.mainloop()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Write the entry file**

Create `ALGOGUARD.cmd`:

```bat
@echo off
REM ---------------------------------------------------------------------
REM AlgoGuard: double-click this file. It opens the AlgoGuard window,
REM where you set up, enter your settings, start and stop AlgoGuard.
REM SETUP_ALGOGUARD.cmd and START_ALGOGUARD.cmd do the same by hand.
REM ---------------------------------------------------------------------
setlocal
cd /d "%~dp0"
title AlgoGuard
set "PYTHON_VERSION=3.14.6"
py -3.14 -c "import platform, sys, tkinter; sys.exit(platform.python_version() != '%PYTHON_VERSION%')" >nul 2>&1
if errorlevel 1 (
    echo AlgoGuard needs Python %PYTHON_VERSION% ^(64-bit^), which was not found.
    echo The download page will open. Install it with the default options,
    echo then double-click this file again.
    start "" "https://www.python.org/downloads/release/python-3146/"
    pause
    exit /b 1
)
start "" pyw -3.14 "%~dp0algoguard_launcher.pyw"
exit /b 0
```

- [ ] **Step 5: Run the tests**

Run: `venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests\test_launcher.py`
Expected: all pass (the window test is skipped only where no display exists; on PC A it runs).

- [ ] **Step 6: Lint**

Run: `venv\Scripts\python.exe -m ruff check launcher_core.py algoguard_launcher.pyw tests\test_launcher.py`
Expected: `All checks passed!`

- [ ] **Step 7: Try it by hand in the repository folder**

Run: `cmd /c ALGOGUARD.cmd`
Expected: the window opens with no console left behind; Python, Packages and Settings are green (this clone is set up); AlgoGuard shows whether a copy is already running. Close the window. Do not press Start if AlgoGuard is already running for the Lead Architect.

- [ ] **Step 8: Commit (after the Lead Architect's yes)**

```
git add algoguard_launcher.pyw ALGOGUARD.cmd tests/test_launcher.py
git commit -m "feat: AlgoGuard launcher window (ALGOGUARD.cmd) for setup, settings, start and stop"
```

---

### Task 6: package and documents

**Files:**
- Modify: `scripts/build_analyst_package.py` (`SUPPORT_FILES`)
- Modify: `tests/test_analyst_package.py`
- Modify: `docs/ANALYST_GUIDE.md`, `ALGOGUARD_USER_MANUAL.txt`, `README.md`, `docs/MAINTAINER_GUIDE.md`

**Interfaces:**
- Consumes: the three files from Tasks 3 to 5.

- [ ] **Step 1: Write the failing package tests**

In `tests/test_analyst_package.py`, in `test_the_package_holds_the_analyst_application_only`, add `"ALGOGUARD.cmd", "launcher_core.py", "algoguard_launcher.pyw",` to the `required` tuple, and change the script loop to:

```python
    for script in ("SETUP_ALGOGUARD.cmd", "START_ALGOGUARD.cmd", "ALGOGUARD.cmd"):
```

Add a new test below it:

```python
def test_the_launcher_needs_nothing_but_the_standard_library(built):
    folder, names = built
    project = {name for name in names if name.endswith(".py")} - {"launcher_core.py"}
    for name in ("launcher_core.py", "algoguard_launcher.pyw"):
        source = (folder / name).read_text(encoding="utf-8")
        imported = package._imports(source, name)
        assert not [m for m in imported if package._module_path(m, project)], name
        assert not imported & {"dotenv", "flask", "scapy", "psutil", "requests"}, name
```

- [ ] **Step 2: Run them to verify they fail**

Run: `venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests\test_analyst_package.py`
Expected: FAIL, `ALGOGUARD.cmd` not in the package.

- [ ] **Step 3: Ship the files**

In `scripts/build_analyst_package.py`, in `SUPPORT_FILES`, after `"START_ALGOGUARD.cmd",` add:

```python
    "ALGOGUARD.cmd",
    "launcher_core.py",
    "algoguard_launcher.pyw",
```

- [ ] **Step 4: Run the package tests**

Run: `venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests\test_analyst_package.py`
Expected: all pass.

- [ ] **Step 5: Update the documents**

`docs/ANALYST_GUIDE.md`, section 2: replace list items 2 and 3 (from "Double-click **`SETUP_ALGOGUARD.cmd`**" to the end of the `.env` paragraph that ends "AlgoGuard refuses to start with one.") with:

```markdown
2. Double-click **`ALGOGUARD.cmd`**. The AlgoGuard window opens. If Python 3.14.6 is missing, it
   says so and opens the download page instead; install it and double-click again.
3. Press **Set up**. It installs AlgoGuard's Python packages into the `venv` folder (2 to 10
   minutes the first time, depending on the connection); the progress shows in the window.
4. Press **Settings**, enter the project **URL** and the **publishable key**, and press **Save**.
   The window answers **Settings OK** when they are right. Never enter a password, a secret key
   (`sb_secret_...`) or a service key; the window refuses a secret key.

The window's five lines show what is ready: Python, Packages, Settings, Npcap and AlgoGuard.
Doing it by hand instead: `SETUP_ALGOGUARD.cmd` installs the packages and opens the settings
file `.env` in Notepad, where the same two values go after `NEXT_PUBLIC_SUPABASE_URL=` and
`NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=`.
```

Section 3: replace list item 1 (the `START_ALGOGUARD.cmd` paragraph) and item 2 with:

```markdown
1. In the AlgoGuard window, press **Start**. When AlgoGuard is ready, your browser opens at
   `http://127.0.0.1:5000`; **Open AlgoGuard** opens it again. AlgoGuard is only reachable from
   this computer. The first prediction or capture after each start takes a few seconds longer
   while the model loads.
2. If the Live Monitor lists live capture as unavailable, press **Stop**, then **Restart as
   administrator**, and start again. By hand: `START_ALGOGUARD.cmd` starts AlgoGuard in its own
   window (right-click, **Run as administrator** for the same effect).
```

and replace "Keep the window open while you work; closing it stops AlgoGuard." with "Keep the AlgoGuard window open while you work; **Stop**, or closing the window, stops AlgoGuard."

Section 5, the "Live interface capture" table row: after "Watches the network card you select, in real time." add "**Default interface** is the adapter that carries this computer's internet connection, named in the list."

Section 9: in the row "Live capture is listed as unavailable", replace "or start AlgoGuard with **Run as administrator**" with "or press **Restart as administrator** in the AlgoGuard window".

`ALGOGUARD_USER_MANUAL.txt`, section 2: replace steps 2 and 3 (from "2. Unzip the package." to the end of step 3) with:

```text
2. Unzip the package. It holds one folder named AlgoGuard. Double-click
   ALGOGUARD.cmd inside it: the AlgoGuard window opens. Press Set up (2 to 10
   minutes the first time), then Settings, and enter the project URL and
   publishable key your administrator gave you.
3. Press Start. Your browser opens when AlgoGuard is ready; keep the AlgoGuard
   window open. If the Live Monitor lists live capture as unavailable, press
   Stop, then "Restart as administrator". (By hand: SETUP_ALGOGUARD.cmd and
   START_ALGOGUARD.cmd do the same steps with Notepad and a console window.)
```

Section 13 step 2: replace "Start START_ALGOGUARD.cmd" with "Double-click ALGOGUARD.cmd and press Start". Section 14: delete item 8 (the launcher window).

`README.md`, "Analyst quick start": replace "run `SETUP_ALGOGUARD.cmd` and enter the project URL and publishable key from your administrator, then run `START_ALGOGUARD.cmd` (as administrator for live capture), open `http://127.0.0.1:5000` and sign in" with "double-click `ALGOGUARD.cmd`, press **Set up**, enter the project URL and publishable key from your administrator under **Settings**, press **Start** and sign in in the browser that opens (`SETUP_ALGOGUARD.cmd` and `START_ALGOGUARD.cmd` do the same by hand)".

`docs/MAINTAINER_GUIDE.md`, section 6: replace "`SETUP_ALGOGUARD.cmd`, `START_ALGOGUARD.cmd`, `.env.example`" with "the launcher window (`ALGOGUARD.cmd`, `algoguard_launcher.pyw`, `launcher_core.py`), `SETUP_ALGOGUARD.cmd`, `START_ALGOGUARD.cmd`, `.env.example`".

- [ ] **Step 6: Check the whole suite**

Run: `venv\Scripts\python.exe -m ruff check .`, `npm run typecheck`, `venv\Scripts\python.exe -m pytest -q -p no:cacheprovider`
Expected: lint and type check clean; the offline suite passes (683 before this plan, plus the new tests).

- [ ] **Step 7: Commit (after the Lead Architect's yes)**

```
git add scripts/build_analyst_package.py tests/test_analyst_package.py docs/ANALYST_GUIDE.md ALGOGUARD_USER_MANUAL.txt README.md docs/MAINTAINER_GUIDE.md
git commit -m "docs: the AlgoGuard window is the main way to install and start; ship it in the analyst package"
```

---

### Task 7: verify on PC A and hand back to the cycle 2 close

**Files:** none changed. Evidence goes to `test-results/cycle2_qa_20261111/`.

- [ ] **Step 1: Push and confirm CI**

After the Lead Architect's yes: `git push origin main`, then `gh run list --limit 1`. Expected: the run for the last commit ends `success`. A timing failure in an unrelated test is re-run once and reported, not edited away.

- [ ] **Step 2: Build the package from the final commit**

Run: `venv\Scripts\python.exe scripts\build_analyst_package.py`
Expected: prints `build 0.6.0+<commit>`; `dist\AlgoGuard-analyst-0.6.0.zip` contains `ALGOGUARD.cmd`.

- [ ] **Step 3: Restart the PC A app on the new code**

Ask the Lead Architect to stop the running AlgoGuard (it still runs the code from before the DEF-28 fix), then start it again from the repository with `ALGOGUARD.cmd` and **Start**.

- [ ] **Step 4: Window check by the Lead Architect (record what each button did)**

1. Unzip `dist\AlgoGuard-analyst-0.6.0.zip` into a new empty folder, for example `C:\AlgoGuard-Window`.
2. Double-click `AlgoGuard\ALGOGUARD.cmd`. Expected: the window; Python green, the rest red.
3. **Set up**. Expected: progress, then "Setup finished"; Packages green.
4. **Settings**: type the URL and key themselves, **Save**. Expected: "Saved. Settings OK ..."; Settings green. Entering a key that starts `sb_secret_` is refused.
5. Stop the repository's AlgoGuard first (one copy per port), then **Start**. Expected: the browser opens; AlgoGuard green. Sign in; the Dashboard shows a new installation awaiting approval (do not approve it; it is a test folder).
6. **Stop**, close the window, delete `C:\AlgoGuard-Window`.

- [ ] **Step 5: Live capture re-test by the Lead Architect (DEF-28, DEF-17, DEF-19)**

With the repository's AlgoGuard running on the new code: Live Monitor, **Live interface capture**. Expected: the first option reads "Default interface (Intel(R) Wi-Fi 6 AX203 ...)". Leave it selected, storage **Save every flow**, paste the recorder (`check-output\recorder_TC-24_PC-A.js` with `MAX_SECONDS = 2700`), **Start Monitoring**, browse for 10 minutes, **Stop**. Meanwhile run `venv\Scripts\python.exe test-results\cycle2_qa_20261111\soak_sampler.py --minutes 10`.

Expected in the recorder file's summary: hundreds of flows, `end_reasons` including `fin`; and on the pilot the log line "Live monitoring started on Live capture - Wi-Fi".

- [ ] **Step 6: Clean-install and integration checks (PC idle)**

With the local Supabase stack running: `set RUN_CLEAN_INSTALL=1` and `venv\Scripts\python.exe -m pytest -m integration -q`.
Expected: all pass; the sustained-load test passes or skips with its busy-PC message.

- [ ] **Step 7: Hand back**

Report to the Lead Architect: test counts, CI run number, what each window button did, the capture's attack share, lag p95 and drop percentage. The cycle 2 close (Test Log v1.0, Completion Report v1.0, board, tag `test-cycle-2-v1.0`) continues from `C:\Users\sanmi\.claude\plans\compiled-roaming-nova.md`, Part 4.
