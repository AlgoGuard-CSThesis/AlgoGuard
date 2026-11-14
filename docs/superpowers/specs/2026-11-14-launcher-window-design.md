# Launcher window and default capture adapter: design

Date: 2026-11-14. Status: approved by the Lead Architect in conversation; implementation not started.

## Why

Two findings from the test cycle 2 release check concern people who are not technical:

- Installing and starting AlgoGuard means running two `.cmd` files and editing `.env` in Notepad.
- DEF-28: with "Default interface" selected, a live capture on PC A watched the Radmin VPN adapter
  instead of Wi-Fi. In 46 minutes it saw 21 packets and none of the browsing, with no warning.
  A person who leaves every default alone gets a capture that shows nothing.

Both are fixed before the tag `test-cycle-2-v1.0`, so the tag covers the package that is handed
out. The release number stays 0.6.0; the package's `BUILD` file names the commit.

## Decisions

| Question | Decision |
|---|---|
| Python | The user installs Python 3.14.6 first. If it is missing, the entry file says so and opens the download page. Nothing is bundled or installed for them. |
| Order | Build the window and the DEF-28 fix, check them on PC A, then close cycle 2. |
| Scope | A guided window: status, Set up, Settings, Start, Stop, Open AlgoGuard, Restart as administrator. |
| Toolkit | Tkinter, standard library only, because the window must run before the `venv` folder exists. |
| Existing scripts | `SETUP_ALGOGUARD.cmd` and `START_ALGOGUARD.cmd` stay, as the fallback and for the automated clean-install check. |

Rejected: a PowerShell window (a second language the tests cannot cover) and a setup page inside
the web app (the app cannot start before setup has run).

## Part 1: the launcher window

### Files

| File | Purpose | Depends on |
|---|---|---|
| `ALGOGUARD.cmd` | The one file a user double-clicks. Checks `py -3.14` reports 3.14.6; if not, prints the same message as `SETUP_ALGOGUARD.cmd` and opens the download page; otherwise starts the window with `pyw` so no console stays open. | Windows, the Python launcher |
| `launcher_core.py` | All logic, no Tkinter: status checks, settings read and write, commands, output parsing. | Standard library only |
| `algoguard_launcher.pyw` | The window. Draws, runs core functions on a worker thread, shows results. | `launcher_core`, Tkinter |

`launcher_core.py` must not import any project module: project modules need packages that are
not installed until Set up has run.

### `launcher_core` interface

- `status(folder) -> Status` with five fields, each `(ok: bool, text: str)`:
  - `python`: the interpreter running the window is 3.14.6.
  - `packages`: `venv\Scripts\python.exe` exists and reports 3.14.6.
  - `settings`: result of the settings check (below); `text` is its message.
  - `npcap`: `%SystemRoot%\System32\Npcap\wpcap.dll` exists. Not required; `text` says live capture
    needs it and replays work without it.
  - `running`: something answers on `127.0.0.1` at the port in `.env` (`ALGOGUARD_PORT`, default 5000).
- `read_settings(folder) -> (url, has_key)`. The key itself is never returned.
- `write_settings(folder, url, key)`:
  - refuses, without writing, a URL that does not start with `https://`, a key that does not start
    with `sb_publishable_`, and any key starting with `sb_secret_`;
  - creates `.env` from `.env.example` if missing;
  - replaces only the lines `NEXT_PUBLIC_SUPABASE_URL=` and `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=`,
    keeping every other line, comment and line ending; writes through a temporary file and replace.
- `check_settings(folder) -> (code, message)`: runs `venv\Scripts\python.exe config.py` and returns
  its exit code (0 ready, 2 invalid, 3 cloud settings missing) and its one line of output.
  `config.py` is the only judge of the settings; the launcher adds no second set of rules beyond
  the three refusals above.
- `setup_steps(folder) -> list[Step]`: the same commands as `SETUP_ALGOGUARD.cmd`, in order:
  create the venv with `py -3.14 -m venv venv` (skipped if it exists with the right version),
  `venv\Scripts\python.exe -m pip install --disable-pip-version-check -r requirements.txt`.
- `start_command(folder)`: `venv\Scripts\python.exe app.py`.
- `running_url(line) -> str | None`: reads the address from the app's "Running on http://..." line.
- `port_in_use(port) -> bool`.

### Window behaviour

- Five status rows, refreshed when the window opens and after every action.
- **Set up**: runs the steps on a worker thread; an indeterminate progress bar and the commands'
  output appear in the log box; ends with "Setup finished" or the failing step's message.
- **Settings**: a small form with two boxes. The URL box shows the saved URL; the key box is always
  empty with the note "saved" or "not set". Save calls `write_settings`, then `check_settings`,
  and shows the message. The key is not written to the log box.
- **Start**: enabled when packages and settings are ready and nothing is running. Starts the app
  without a console window, reads its output into the log box, and when the "Running on" line
  appears, enables **Open AlgoGuard** and opens the browser once.
- **Stop**: ends the app and its child processes (`taskkill /PID <pid> /T /F` after a short
  graceful attempt). This matches closing the `START_ALGOGUARD.cmd` window today; records waiting
  to upload are on disk and are kept.
- **Open AlgoGuard**: opens the address in the default browser.
- **Restart as administrator**: relaunches the window elevated (Windows shows its own prompt).
  Shown with the note that live capture may need it.
- If the port already answers when the window opens, the status says AlgoGuard is already running
  and Start is disabled; Open still works. The window does not stop a copy it did not start.
- Closing the window while AlgoGuard runs asks for confirmation, then stops it.
- Every failure shows a plain message and leaves the buttons usable. No tracebacks in the window.

### Package and documents

- `scripts/build_analyst_package.py`: add the three files to `SUPPORT_FILES`.
- `docs/ANALYST_GUIDE.md` sections 2 and 3, `ALGOGUARD_USER_MANUAL.txt` section 2 (and remove the
  launcher from future improvements), `README.md` quick start, `docs/MAINTAINER_GUIDE.md` section 6:
  the window is the main route; the two `.cmd` files are the manual route.

## Part 2: DEF-28, the default capture adapter

- `services/traffic_source_service.py`:
  - new `default_capture_interface()`: the name of the adapter that holds the IPv4 route to the
    internet (scapy `conf.route.route("0.0.0.0")`, resolved to an interface name through
    `conf.ifaces`). Returns `None` if it cannot be resolved. Verified on PC A: returns "Wi-Fi"
    where scapy's own default is "Radmin VPN".
  - `LiveCaptureSource`: when no interface is given, use `default_capture_interface()`; if that is
    `None`, behave as today.
  - `list_capture_interfaces()`: that adapter is listed first.
- `services/live_monitor_service.py`: `get_options()` also returns the default adapter's label;
  `_source_label` names the adapter actually used.
- `templates/monitor.html`: the first option reads "Default interface (<adapter label>)".
- `cloud_monitor.py`: the session's `source_name` for a default capture is the resolved adapter,
  so the audit log reads "Live capture - Wi-Fi", not "default interface".
- Out of scope: a warning when a capture sees no packets. The analyst guide's troubleshooting row
  ("A live session shows no rows") stays.

## Testing

| What | How |
|---|---|
| `launcher_core` | `tests/test_launcher.py` on temporary folders: settings edit keeps other lines and comments; `.env` created from the example; the three refusals write nothing; `read_settings` never returns the key; status fields for missing venv, wrong version, missing and valid settings; setup steps and start command; `running_url`; `port_in_use`. |
| Window | One smoke test: the window builds, shows five status rows and closes. Skipped when Tkinter cannot open a display. |
| Package | `tests/test_analyst_package.py`: the three files are in the zip; `ALGOGUARD.cmd` has Windows line endings; `launcher_core.py` imports no project module. |
| DEF-28 | `tests/test_traffic_sources.py` with scapy's route and interfaces replaced: default follows the route; falls back when it cannot be resolved; an explicit choice is unchanged; list order; the label. |
| Regression | Offline suite, Ruff, Pyright; the integration lane and `RUN_CLEAN_INSTALL=1` on the final commit, PC idle. |
| By hand, PC A | (1) New package unzipped to an empty folder: `ALGOGUARD.cmd`, Set up, Settings, Start, Open, sign in, Stop. (2) Live capture for 10 minutes on "Default interface": the pilot shows web flows and the log names the Wi-Fi adapter. |

## Not included

- Installing or bundling Python.
- Any change to sign-in, models, uploads or the pages other than the interface dropdown's first
  option.
- A second PC check of the window; PC B is not needed.
