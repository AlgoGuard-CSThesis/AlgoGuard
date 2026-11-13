# Test cycle 2 — PC B session notes (2026-11-10, Asia/Manila)

Working notes; the results table is in `pc-b-results.md`. No passwords, keys, e-mail addresses or
IP addresses are recorded here.

## Environment

- PC B: LAPTOP-PNL5IIAJ · Windows 11 Home Single Language, build 26200 · AMD Ryzen 5 7520U (4 cores) ·
  7.2 GB RAM · Python 3.14.6 · Npcap 1.10.6 · Chrome
- Network: Wi-Fi (MediaTek Wi-Fi MT7902). The other adapter, "Ethernet 2", is a VirtualBox
  Host-Only adapter and is not used.
- Repository clone at `4cf3fdd`; application code unchanged since `4740a43`
  (`git diff --stat 4740a43 HEAD -- . ':!docs' ':!test-results'` empty).

## Log

- 17:40:59 Clock vs time.windows.com: -0.109 s (3 samples: -0.1090, -0.1087, -0.1088). Under 1 s, no resync.
- 17:41:13 Package built from this clone: `dist\AlgoGuard-analyst-0.6.0.zip`, 70 files, **build 0.6.0+4cf3fdd**.
- 17:41:20 Setup timer started. Unzipped to `C:\AlgoGuard-C2`; setup script at
  `C:\AlgoGuard-C2\AlgoGuard\SETUP_ALGOGUARD.cmd` (the zip has a top-level `AlgoGuard` folder).
  No `.env` and no `.algoguard` folder before setup.
- 17:42 At the person's request ("u do it"), the agent ran setup. Before setup, the agent created
  `.env` from `.env.example` and copied in only the two lines `NEXT_PUBLIC_SUPABASE_URL` and
  `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` from the clone's `.env`, by script, without displaying them.
  Every other line is the template's. **Deviation:** because `.env` was already filled, setup's
  Notepad step did not run, so it was not exercised in this session.
- 17:42–17:44 `SETUP_ALGOGUARD.cmd`: exit 0 in **109 s**. Messages: "[1/3] Creating the Python
  environment in the venv folder...", "[2/3] Installing AlgoGuard's Python packages (a few minutes
  the first time)...", "[3/3] Checking the settings file .env...", "Settings OK. Cloud project:
  <URL> AlgoGuard will open at http://127.0.0.1:5000", "Setup finished. Start AlgoGuard with
  START_ALGOGUARD.cmd.", then "Press any key to continue". No Npcap warning, so Npcap was found.
  Packages included scikit-learn 1.9.0, pandas 3.0.3, numpy 2.5.1, scapy 2.8.0, Flask 3.1.3.
- 17:46 `START_ALGOGUARD.cmd` started in its own window (not as administrator). Port 5000 listening;
  `/` redirects to `/login` ("Login | AlgoGuard").
- 17:46:04 Installation ID created: `3cf6c3e5-ce7f-43c5-9392-f4df15a44b07`.
- ~17:46:35 First sign-in by the PC B analyst. The model download began (`models\*.part`) and
  stopped at exactly 10,485,760 bytes at 17:46:45.
- 17:47 **AlgoGuard and its window were gone.** Port 5000 was closed and no AlgoGuard process was
  left. The Windows Application log shows no crash and Defender shows no detection. If `app.py` had
  failed on its own, the start script's `pause` would have kept the window open, so the window was
  closed rather than the program crashing. The cause is still to be confirmed with the person.
- 17:48 Restarted by the agent as a background process: `venv\Scripts\python.exe -u app.py`, run
  from `C:\AlgoGuard-C2\AlgoGuard` with its output logged (the log stays outside the repository
  because it contains the project URL). "Running on http://127.0.0.1:5000" at 17:48:12. The same
  installation ID is kept.
