"""The launcher window's logic (launcher_core): settings, status, commands, first run, downloads."""

import hashlib
import io
import os
import re
import shutil
import socket
import subprocess
import sys
import urllib.error
from pathlib import Path

import pytest

import launcher_core as core

ROOT = Path(core.__file__).resolve().parent

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


# --- status and commands ---------------------------------------------------------------------


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


@pytest.mark.parametrize("code, message, ok, expected", [
    (3, "Cloud settings missing: enter NEXT_PUBLIC_SUPABASE_URL and "
        "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY in C:\\AlgoGuard\\.env.", False,
     "Not entered yet: press Settings"),
    (2, "Settings problem: the URL must start with https://.", False,
     "Settings problem: the URL must start with https://. Press Settings to correct it."),
    (0, "Settings OK. Cloud project: https://project.supabase.co.", True,
     "Settings OK. Cloud project: https://project.supabase.co."),
])
def test_the_settings_line_points_to_the_settings_button_not_the_file(
        folder, monkeypatch, code, message, ok, expected):
    monkeypatch.setattr(core, "port_in_use", lambda port: False)
    monkeypatch.setattr(core, "python_version_of", lambda executable: "3.14.6")
    monkeypatch.setattr(core, "check_settings", lambda where: (code, message))
    core.venv_python(folder).parent.mkdir(parents=True)
    core.venv_python(folder).write_bytes(b"")
    state = core.status(folder, current_version="3.14.6", system_root=str(folder))
    assert state.settings == core.Check(ok, expected)


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


def _window_module():
    import importlib.machinery
    import importlib.util

    path = ROOT / "algoguard_launcher.pyw"
    loader = importlib.machinery.SourceFileLoader("algoguard_launcher", str(path))
    spec = importlib.util.spec_from_loader("algoguard_launcher", loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


@pytest.fixture()
def tk_root():
    tkinter = pytest.importorskip("tkinter")
    try:
        root = tkinter.Tk()
    except tkinter.TclError as error:
        pytest.skip(f"no display for Tkinter ({error})")
    root.withdraw()
    yield root
    root.destroy()


def test_the_window_builds_shows_five_checks_and_closes(folder, monkeypatch, tk_root):
    module = _window_module()
    monkeypatch.setattr(core, "port_in_use", lambda port: False)
    window = module.LauncherWindow(tk_root, folder)
    window.refresh()
    tk_root.update()
    assert list(window.status_labels) == [
        "Python", "Packages", "Settings", "Npcap (live capture)", "AlgoGuard"]
    assert set(window.buttons) == {"setup", "settings", "start", "stop", "open", "admin", "npcap"}
    assert str(window.buttons["start"]["state"]) == "disabled"
    # The progress bar shows only while Set up works; a full bar left behind looks stuck.
    assert window.progress.grid_info() == {}
    window.busy = True
    window.show_progress(True)
    assert window.progress.grid_info() != {}
    window.messages.put(("done", None))
    window.pump()
    assert window.progress.grid_info() == {} and window.busy is False


# --- the first run -----------------------------------------------------------------------------


def _state(python=True, packages=True, settings=True, npcap=True, running=False):
    return core.Status(core.Check(python, ""), core.Check(packages, ""),
                       core.Check(settings, ""), core.Check(npcap, ""), core.Check(running, ""))


@pytest.mark.parametrize("state, step", [
    (_state(python=False, packages=False, settings=False), None),
    (_state(packages=False, settings=False), "set_up"),
    (_state(packages=False), "set_up"),
    (_state(settings=False), "settings"),
    (_state(npcap=False), "start"),
    (_state(running=True), None),
])
def test_the_first_run_goes_set_up_then_settings_then_start(state, step):
    assert core.next_step(state) == step


@pytest.fixture()
def guided(folder, monkeypatch, tk_root):
    """A window whose status and actions are recorded instead of run."""
    module = _window_module()
    current = {"state": _state(packages=False, settings=False)}
    monkeypatch.setattr(core, "status", lambda where: current["state"])
    window = module.LauncherWindow(tk_root, folder)
    calls = []
    for action in ("set_up", "settings", "start"):
        monkeypatch.setattr(window, action, lambda name=action: calls.append(name))
    return module, window, current, calls


def test_the_window_asks_once_then_sets_up_enters_settings_and_starts(guided, monkeypatch):
    module, window, current, calls = guided
    questions = []
    monkeypatch.setattr(module.messagebox, "askyesno",
                        lambda title, text: questions.append(text) or True)
    window.begin_guide(set_up_now=False)
    assert calls == ["set_up"] and len(questions) == 1 and "not set up" in questions[0]
    current["state"] = _state(settings=False)
    window.messages.put(("set_up_done", True))  # what the Set up worker reports at the end
    window.pump()
    assert calls == ["set_up", "settings"]
    current["state"] = _state()
    window.continue_guide()  # what a successful Save in the Settings form does
    assert calls == ["set_up", "settings", "start"] and window.guided is False


def test_after_installing_python_the_window_sets_up_without_asking(guided, monkeypatch):
    module, window, _, calls = guided

    def no_question(*args):
        raise AssertionError("asked although Python was just installed for AlgoGuard")
    monkeypatch.setattr(module.messagebox, "askyesno", no_question)
    window.begin_guide(set_up_now=True)
    assert calls == ["set_up"]


def test_a_declined_or_failed_set_up_ends_the_first_run(guided, monkeypatch):
    module, window, current, calls = guided
    monkeypatch.setattr(module.messagebox, "askyesno", lambda title, text: False)
    window.begin_guide(set_up_now=False)
    assert calls == [] and window.guided is False
    window.guided = True
    window.messages.put(("set_up_done", False))
    window.pump()
    assert calls == [] and window.guided is False


def test_a_set_up_installation_opens_without_starting_by_itself(guided, monkeypatch):
    module, window, current, calls = guided
    current["state"] = _state()
    monkeypatch.setattr(module.messagebox, "askyesno", lambda title, text: True)
    window.begin_guide(set_up_now=False)
    assert calls == []
    current["state"] = _state(settings=False)
    window.begin_guide(set_up_now=False)
    assert calls == ["settings"]


def test_install_npcap_is_offered_only_while_npcap_is_missing(guided):
    _, window, current, _ = guided
    current["state"] = _state(npcap=False)
    window.refresh()
    assert str(window.buttons["npcap"]["state"]) == "normal"
    current["state"] = _state()
    window.refresh()
    assert str(window.buttons["npcap"]["state"]) == "disabled"


def test_a_downloaded_npcap_installer_is_started_as_administrator(guided, monkeypatch, tmp_path):
    _, window, _, _ = guided
    started = []
    monkeypatch.setattr(core, "run_as_administrator",
                        lambda program, parameters="", folder=None: started.append(program) or True)
    window.messages.put(("npcap_ready", str(tmp_path / "npcap.exe")))
    window.pump()
    assert started == [str(tmp_path / "npcap.exe")]


# --- downloads ---------------------------------------------------------------------------------


PAYLOAD = b"installer bytes " * 1000
PAYLOAD_SHA256 = hashlib.sha256(PAYLOAD).hexdigest()
ADDRESS = "https://example.org/setup.exe"


class FakeOpener:
    """Stands in for urllib's urlopen, so no test downloads anything."""

    def __init__(self, payload=b"", error=None):
        self.payload, self.error, self.urls = payload, error, []

    def __call__(self, url, timeout):
        self.urls.append(url)
        if self.error is not None:
            raise self.error
        return io.BytesIO(self.payload)


def test_a_download_is_kept_when_its_fingerprint_matches(tmp_path):
    target = tmp_path / "setup.exe"
    opener = FakeOpener(PAYLOAD)
    assert core.download_verified(ADDRESS, PAYLOAD_SHA256.upper(), target, opener=opener) == target
    assert target.read_bytes() == PAYLOAD and opener.urls == [ADDRESS]
    assert list(tmp_path.iterdir()) == [target]


def test_a_download_with_another_fingerprint_is_deleted(tmp_path):
    with pytest.raises(core.DownloadError, match="not the expected"):
        core.download_verified(ADDRESS, "0" * 64, tmp_path / "setup.exe",
                               opener=FakeOpener(PAYLOAD))
    assert list(tmp_path.iterdir()) == []


def test_only_https_downloads_are_made(tmp_path):
    opener = FakeOpener(PAYLOAD)
    with pytest.raises(core.DownloadError, match="https"):
        core.download_verified("http://example.org/setup.exe", PAYLOAD_SHA256,
                               tmp_path / "setup.exe", opener=opener)
    assert opener.urls == []


def test_a_download_larger_than_expected_is_stopped(tmp_path):
    with pytest.raises(core.DownloadError, match="larger"):
        core.download_verified(ADDRESS, PAYLOAD_SHA256, tmp_path / "setup.exe",
                               opener=FakeOpener(PAYLOAD), max_bytes=1000)
    assert list(tmp_path.iterdir()) == []


def test_a_failed_download_says_so_plainly(tmp_path):
    opener = FakeOpener(error=urllib.error.URLError("no route to host"))
    with pytest.raises(core.DownloadError, match="internet connection"):
        core.download_verified(ADDRESS, PAYLOAD_SHA256, tmp_path / "setup.exe", opener=opener)
    assert list(tmp_path.iterdir()) == []


def test_npcap_comes_from_its_pinned_address_with_its_pinned_fingerprint(tmp_path, monkeypatch):
    seen = {}

    def fake(url, sha256, destination, **options):
        seen.update(url=url, sha256=sha256, destination=destination)
        return destination
    monkeypatch.setattr(core, "download_verified", fake)
    target = tmp_path / f"AlgoGuard-npcap-{core.NPCAP_VERSION}.exe"
    assert core.download_npcap(tmp_path) == target
    assert seen == {"url": core.NPCAP_URL, "sha256": core.NPCAP_SHA256, "destination": target}
    assert core.NPCAP_URL == f"https://npcap.com/dist/npcap-{core.NPCAP_VERSION}.exe"
    assert re.fullmatch(r"[0-9a-f]{64}", core.NPCAP_SHA256)


def test_a_failed_npcap_download_points_to_the_npcap_website(tmp_path, monkeypatch):
    def fail(*args, **options):
        raise core.DownloadError("The download did not finish.")
    monkeypatch.setattr(core, "download_verified", fail)
    with pytest.raises(core.DownloadError, match="npcap.com"):
        core.download_npcap(tmp_path)


def test_npcap_is_found_by_its_library(tmp_path):
    assert core.npcap_found(str(tmp_path)) is False
    (tmp_path / "System32" / "Npcap").mkdir(parents=True)
    (tmp_path / "System32" / "Npcap" / "wpcap.dll").write_bytes(b"")
    assert core.npcap_found(str(tmp_path)) is True


# --- the batch files that find and install Python ----------------------------------------------


def _batch_value(name, file):
    text = (ROOT / file).read_text(encoding="ascii")
    match = re.search(rf'^set "{name}=(.*)"\r?$', text, re.MULTILINE)
    assert match, f"{name} is not set in {file}"
    return match.group(1)


def test_python_is_pinned_once_and_installed_for_this_account_only():
    version = _batch_value("PYTHON_VERSION", "find_python.cmd")
    assert version == core.PYTHON_VERSION
    url = _batch_value("PYTHON_URL", "ALGOGUARD.cmd")
    assert url == f"https://www.python.org/ftp/python/{version}/python-{version}-amd64.exe"
    assert re.fullmatch(r"[0-9a-f]{64}", _batch_value("PYTHON_SHA256", "ALGOGUARD.cmd"))
    launcher = (ROOT / "ALGOGUARD.cmd").read_text(encoding="ascii")
    install = [line for line in launcher.splitlines() if "/passive" in line]
    assert len(install) == 1
    for switch in ("InstallAllUsers=0", "Include_launcher=0", "PrependPath=0"):
        assert switch in install[0]
    assert "runas" not in launcher.lower()  # never asks for administrator rights
    for file in ("ALGOGUARD.cmd", "SETUP_ALGOGUARD.cmd"):
        text = (ROOT / file).read_text(encoding="ascii")
        assert 'call "%~dp0find_python.cmd"' in text, file
        assert "PYTHON_VERSION=" not in text, f"{file} must take the version from find_python.cmd"


windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Windows batch file")


def _find_python(**changes):
    """Run find_python.cmd as ALGOGUARD.cmd does and return what it set."""
    env = {key: value for key, value in os.environ.items() if key != "VIRTUAL_ENV"}
    env.update(changes)
    result = subprocess.run(
        ["cmd", "/d", "/c", "call", str(ROOT / "find_python.cmd"), "&", "set", "PYTHON", "&",
         "set", "OTHER_PYTHON"], capture_output=True, text=True, env=env, timeout=180)
    return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)


def _system32_only():
    return os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")


def _usable(python):
    probe = subprocess.run([python, "-c", "import platform, tkinter; "
                            "print(platform.python_version())"], capture_output=True, text=True,
                           timeout=60)
    return probe.returncode == 0 and probe.stdout.strip() == core.PYTHON_VERSION


@windows_only
def test_find_python_finds_the_folder_algoguard_installs_into(tmp_path):
    """No Python launcher on PATH: the per-user folder of ALGOGUARD.cmd's install is found."""
    base = Path(sys.base_prefix)
    if not _usable(str(base / "python.exe")):
        pytest.skip(f"this interpreter is not Python {core.PYTHON_VERSION} with Tcl/Tk")
    local = tmp_path / "Local App (Data) & more"
    folder = local / "Programs" / "Python" / "Python314"
    folder.parent.mkdir(parents=True)
    subprocess.run(f'mklink /J "{folder}" "{base}"', shell=True, check=True,
                   capture_output=True)
    try:
        found = _find_python(PATH=_system32_only(), LocalAppData=str(local))
        assert found.get("PYTHON") == str(folder / "python.exe")
        assert found.get("PYTHONW") == str(folder / "pythonw.exe")
        assert found.get("PYTHON_VERSION") == core.PYTHON_VERSION
        assert "OTHER_PYTHON" not in found
    finally:
        os.rmdir(folder)  # removes the junction only, never the Python it points to


@windows_only
def test_find_python_falls_back_to_the_registry(tmp_path):
    import winreg

    expected = None
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, r"Software\Python\PythonCore\3.14\InstallPath") as key:
                registered = winreg.QueryValueEx(key, "ExecutablePath")[0]
        except OSError:
            continue
        if Path(registered).exists() and _usable(registered):
            expected = registered
            break
    found = _find_python(PATH=_system32_only(), LocalAppData=str(tmp_path))
    assert found.get("PYTHON") == expected


@windows_only
def test_find_python_asks_the_python_launcher_first():
    launcher = shutil.which("py")
    if launcher is None:
        pytest.skip("no Python launcher")
    probe = subprocess.run([launcher, "-3.14", "-c", "import sys; print(sys.executable)"],
                           capture_output=True, text=True, timeout=60)
    if probe.returncode != 0 or not _usable(probe.stdout.strip()):
        pytest.skip(f"the Python launcher has no Python {core.PYTHON_VERSION}")
    assert _find_python().get("PYTHON") == probe.stdout.strip()
