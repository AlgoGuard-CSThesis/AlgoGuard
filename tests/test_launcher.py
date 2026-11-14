"""The launcher window's logic (launcher_core): settings, status, commands."""

import socket
import sys

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


def test_the_window_builds_shows_five_checks_and_closes(folder, monkeypatch):
    import importlib.machinery
    import importlib.util
    from pathlib import Path

    tkinter = pytest.importorskip("tkinter")
    path = Path(core.__file__).with_name("algoguard_launcher.pyw")
    loader = importlib.machinery.SourceFileLoader("algoguard_launcher", str(path))
    spec = importlib.util.spec_from_loader("algoguard_launcher", loader)
    assert spec is not None
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
        # The progress bar shows only while Set up works; a full bar left behind looks stuck.
        assert window.progress.grid_info() == {}
        window.busy = True
        window.show_progress(True)
        assert window.progress.grid_info() != {}
        window.messages.put(("done", None))
        window.pump()
        assert window.progress.grid_info() == {} and window.busy is False
    finally:
        root.destroy()
