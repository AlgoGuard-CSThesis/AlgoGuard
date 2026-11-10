# AlgoGuard test cycle 2 — Test procedures (C2-DOC-04)

**Owner:** John Brian A. Tiopes (card C2-DOC-04) · **Plan:** Master Test Plan (cycle 2) v0.1 · **Build under test:** release 0.6.0, application code of `4740a43`, run from `8864f29` or a later commit that adds only test records · **Model:** model 60 (training run 10), deployment #3 on the hosted pilot

These are the steps a tester follows on PC A and PC B. They add only what cycle 2 needs on top of the [analyst guide](../../ANALYST_GUIDE.md) (installing and using AlgoGuard), the [maintainer guide](../../MAINTAINER_GUIDE.md) (package, checks and test lanes) and the [two-computer runbook](../../migration/05f-two-computer-runbook.md) (Stage 5F pilot). Each test case (TC-xx) is defined in the plan's Test Case Specification.

## 0. Rules for every session

- Never write a password, key, token, `.env` content or e-mail address in the Test Log, on a card or in a screenshot. Crop the account menu out of screenshots.
- AlgoGuard shows times in UTC; the Test Log uses Asia/Manila (UTC+8). When you quote a page, give both.
- Evidence goes in `test-results/cycle2_<YYYYMMDD>/` on PC A. Copy PC B's files there the same day (USB stick, shared drive, or commit them from PC B's clone).
- One Test Log row per execution, re-runs included, with the commit and the PC (for example `8864f29 (PC B, package)`).
- PC B's cases run first, while PC B is available (section 4).
- **The recorder.** `test-results/cycle2/monitor_recorder.js` records one Live Monitor session: live delay (TC-11), the share of flows called Attack (TC-24), packet drops, score bands, and the capture ID that upload delay needs (TC-23). Open Live Monitor and choose the settings first. Then press **F12**, open **Console**, paste the whole file and press Enter (type `allow pasting` first if the browser asks). Change `LABEL` on its first line to the case and PC, for example `TC-24_PC-B`. Then press **Start Monitoring**. When the session stops, it prints a summary and saves `algoguard_<label>_<time>.json` to Downloads. It only reads and keeps no IP address.

## 1. Before every session (both PCs)

1. **Pilot awake.** Open the pilot project in the Supabase dashboard. If it says *Paused*, restore it and wait until it is healthy: the Free plan pauses idle projects. On PC A the maintainer can also run `venv\Scripts\python.exe cloud_migrate.py --status`, which should show 19 applied and none pending.
2. **Clocks.** On each PC, open Command Prompt as administrator and run `w32tm /resync` (if it says the service has not started, run `net start w32time` first). Then run `w32tm /stripchart /computer:time.windows.com /samples:3 /dataonly` and write the offset in the Test Log. Upload delay compares the two PCs' clocks with the cloud's, so this matters.
3. **Build.** PC A: `git rev-parse --short HEAD` and `git status` (nothing changed). PC B: the Administrator's **Computers** page shows PC B's version `0.6.0+<commit>`. Write both in the log.
4. **Model.** The Dashboard names deployment #3, and Live Monitor shows *Pinned deployment #3* once a session starts.
5. **Account.** Sign in as the account the case names: the **Administrator**, the **PC A analyst**, the **PC B analyst**, or the **new analyst** of TC-18.

## 2. Once, before the first session (C2-ENV-01)

### 2.1 Build the analyst package (PC A)

```bat
venv\Scripts\python.exe scripts\build_analyst_package.py
```

It packages the newest commit into `dist\AlgoGuard-analyst-0.6.0.zip` and prints its build, for example `build 0.6.0+8864f29`. Write the build in the log. Copy the zip to PC B. Give PC B's user the project URL and publishable key in a message, not in any file in the repository.

### 2.2 Install AlgoGuard on PC B (TC-17)

Follow analyst guide sections 1–4, with these choices:

1. Unzip to a **new** folder, for example `C:\AlgoGuard-C2`. Leave the Stage 5F folders alone (PC B's earlier clone and `C:\AlgoGuardPilot\state-PC-A`). They hold that installation's identity and stay untouched until the cycle ends.
2. Run `SETUP_ALGOGUARD.cmd`, fill in the two lines in Notepad, save and close. Setup must say **Settings OK**.
3. Run `START_ALGOGUARD.cmd`, open `http://127.0.0.1:5000` and sign in as the **PC B analyst**. The Dashboard shows **Awaiting approval** and an installation ID. Send the ID to the Administrator.
4. On PC A, the Administrator opens **Nodes**, finds the request by **both** the PC B analyst and the installation ID, and clicks **Approve**. This is UAT scenario 4 if the Product Owner does it.
5. On PC B, reload the Dashboard: it shows **Node approved**, with no restart. Open Live Monitor: model 60 downloads and the page shows deployment #3.

Write down: how long setup took, every message shown, anything the guide did not say, and the version on **Computers**. TC-17 passes when all of the above happen without changing anything except the two `.env` lines.

### 2.3 Accounts and data

- On **Accounts**, the Administrator creates an analyst account for the TC-18 person. They choose a password with the person; nobody writes it down here.
- Untouched evaluation set on PC A: `research\dataset\processed\official_unsw\UNSW_NB15_training-set.csv`, 175,341 rows. Its SHA-256 must start `bec7dd5e` (`certutil -hashfile <file> SHA256`). If it is missing, download the official training partition from the UNSW-NB15 page and check the hash against `test-results/iter6_6a/protocol_6a2.md`.
- The maintainer changes the pilot's database password before M3, if not yet done (plan risk table).

## 3. Smoke gate on each PC (C2-SMK-01: TC-01, TC-02)

1. **Predict** page: type the **first Normal row** of `datasets\algoguard_big.csv` and run it. Expect **Normal**, attack score **Low**, then **Synced**.
2. Same with the **first Attack row**. Expect **Attack**, score **High** or **Very high**, an alert, then **Synced**.
3. **Live Monitor:** *Sample replay*, *Sample traffic – 5,000 flows*, *Fast*, *Sequential*, *Save attacks*. Run it for 2 minutes, then Stop. Expect alerts, no error, and **Pending upload** back to 0.

Testing goes on only if all three pass on both PCs.

## 4. PC B session (both PCs; about 2 hours)

Run the cases in this order: each one leaves the state the next needs.

### 4.1 Benign live traffic, live delay and live upload (TC-24, TC-11, TC-23 live part)

On **each PC** (PC B first; PC A the same way, at another time):

1. Live Monitor: *Live interface capture*, the network card that carries the traffic (Wi-Fi), *Save every flow*.
2. Paste the recorder with `LABEL` set to `TC-24_PC-B` (or `TC-24_PC-A`), then **Start Monitoring**.
3. For **10 minutes**, use the PC normally: web browsing, e-mail, video. No scans, no downloads of unusual software. Then **Stop**.
4. Keep the downloaded JSON. Its summary gives:
   - `attack_share_percent`: **TC-24** passes at 8% or less (DEF-19).
   - `fin_rst_lag_ms.p95`: **TC-11** passes at 500 ms or less (DEF-17). Report `n` too.
   - `capture.drop_percent`: the packet-drop budget passes at 1% or less.
   - `capture_id`: needed for TC-23 (section 4.2, step 4).

### 4.2 Upload delay bursts (TC-23, DEF-10)

1. **PC B alone:** Live Monitor, *Sample replay*, *Sample traffic – 5,000 flows*, *Maximum*, *Sequential*, *Save every flow*. Paste the recorder (`TC-23_burst_PC-B`) and Start. The session stores 300 records within seconds; let it finish or Stop after one minute.
2. **PC A alone:** the same, labelled `TC-23_burst_PC-A`.
3. **Both at once:** both PCs set up as in step 1. Count down together and press Start at the same moment. Use the labels `TC-23_both_PC-A` and `TC-23_both_PC-B`.
4. On PC A (maintainer settings), pass every `capture_id` from steps 1–3 and section 4.1 to:

   ```bat
   venv\Scripts\python.exe test-results\def_fixes_20261023\upload_delay.py <capture_id> <capture_id> ...
   ```

   For each capture it prints the upload delay p50, p95 and maximum, the acknowledged events per second, and the number of distinct events. It also gives PC A's clock offset; for PC B's captures, use PC B's offset from section 1. **TC-23** passes when every capture's p95 is 5 s or less and stored = distinct (no duplicates, nothing lost). The burst's acknowledged events per second is also the **sustained events per second** budget (20 or more).

### 4.3 Port scan between the two PCs (TC-25, exploratory)

Run this only on the team's own network, with its owner's permission. It needs nmap on PC B (<https://nmap.org>).

1. PC A: `ipconfig` and note PC A's IPv4 address. Live Monitor: *Live interface capture*, *Save attacks*. Paste the recorder (`TC-25_PC-A`), then Start.
2. PC B, in Command Prompt: `nmap -sT --top-ports 1000 <PC A address>`. Wait until it finishes, plus 30 seconds.
3. PC A: Stop. Record how many flows were called Attack, their bands, and the destination ports from the JSON. This is an observation only, not pass/fail; Windows Firewall on PC A answers few of the probes.

### 4.4 Cross-node read (TC-06)

PC A, signed in as the **PC A analyst**. PC B's records exist from section 3. Use PC B's installation ID from section 2.2.

1. **Alerts**, **Logs**, **Reports**: the **Computer** filter offers only computers this analyst is approved on, and no PC B rows appear under *All computers you can see*.
2. Hand-edited addresses: open `http://127.0.0.1:5000/alerts?node=<PC B installation ID>`, then the same with `/logs` and `/reports`. Expect no PC B rows. Then open the number of a report PC B created (the Administrator can see it) as `/reports/<number>`. Expect a not-found page, and no data from PC B in any error.
3. Data API: in PC A's AlgoGuard folder, run the probe yourself (it asks for the analyst's e-mail and password):

   ```bat
   venv\Scripts\python.exe test-results\cycle2\data_api_probe.py --other-node <PC B installation ID>
   ```

   Expect `0` in the "Other node rows" column for every table, and `RESULT: PASS`. Save the printed table (it has no secrets) as evidence.

### 4.5 Revoked membership (TC-07)

1. PC B: signed in as the PC B analyst, no session running.
2. PC A, Administrator, **Nodes**: revoke **only** the PC B analyst's membership on PC B's new installation. Do not deactivate the account.
3. PC B, without signing out: try one Manual Prediction and try to Start Monitoring. Expect both refused (*Your account or this installation needs administrator approval.*). Write down the exact wording.
4. PC A: **Restore** the same membership.
5. PC B: within a minute, without restarting AlgoGuard or pressing Retry, the Dashboard's upload status returns to *Active*, and a Manual Prediction and a short sample replay work and sync. Write down how long it took.

### 4.6 Administrator views and Stale (TC-20)

1. PC A, Administrator: **Computers** lists PC A and PC B (the new installation) with version, model, capture state and records waiting, not stored or rejected.
2. **Alerts**, **Logs**, **Reports** with *All computers you can see*: rows from both computers, each labelled with its computer. On PC A, PC B's alerts are **View only**.
3. PC B: sign out (and stop AlgoGuard). After 3 minutes, **Computers** shows PC B as **Stale**.
4. PC A, signed in as the PC A analyst: the Computer filter shows only PC A's installations.

### 4.7 End of the PC B session

Every capture is stopped, PC B's Dashboard shows nothing pending, PC B is signed out, and the JSON files are copied to PC A. If PC B leaves the team's hands after the cycle, the Administrator revokes both PC B installations (the Stage 5F one and the new one) on **Nodes**.

## 5. PC A cases

| Case | Steps | Pass when |
|---|---|---|
| TC-03 (C2-SYS-01) | `venv\Scripts\python.exe scripts\evaluate_model.py --model saved_models\run_10\stacking.joblib --data research\dataset\processed\official_unsw\UNSW_NB15_training-set.csv --train-csv research\dataset\raw\UNSW_NB15_training-set.csv research\dataset\processed\def19\training_local.csv --out test-results\cycle2_<date>\tc03`. The output must say 175341 rows loaded, 66602 removed and 108739 evaluated, and give the model SHA-256 `6756c632…9604` (model 60). Otherwise stop and report. | Accuracy, F1, recall ≥ 90%; FPR ≤ 8%; ROC-AUC ≥ 0.95. Stage 6A measured 86.2% / 85.4% / 76.0% / 2.3% / 0.970, so expect a High DEF card for the three missed targets. |
| TC-04 (C2-SYS-02) | Live Monitor, *Recorded packets (PCAP)*, `cycle1-tc04-sample.pcap` from the `captures` folder, *Save every flow*. | Every flow (11 in cycle 1) classified with a score, stored and Synced. |
| TC-05 (C2-SYS-02) | Predict page and `POST /predict`: a missing field, `abc` in a number field, `1e308`, a negative number. Then replay `cycle1-bad-empty.pcap`, `cycle1-bad-text.pcap` and `cycle1-bad-truncated.pcap`. | Each refused in plain language; nothing stored; no crash. |
| TC-08 (C2-INT-01) | Close AlgoGuard. Copy the cached model file from the state folder's `models` folder to a backup. In the original, change one byte (any hex editor), then start AlgoGuard. Then follow the message: delete the folder it names and start again. | *The model copy saved on this computer is corrupt.*; Predict and Live Monitor refuse; after deleting the folder, a good copy downloads and works. |
| TC-09 (C2-INT-01) | Sample replay, *Normal* speed, *Save every flow*. After 1 minute, turn Wi-Fi **off**, and check that a website no longer loads (Airplane mode alone did not disconnect PC B in Stage 5F). Wait 1 minute; close AlgoGuard; start it; turn Wi-Fi on; sign in. | Records shown **Pending** while offline; all **Synced** after sign-in; `upload_delay.py` shows stored = distinct for the capture. |
| TC-10 (C2-SEC-01) | Sign out. Five wrong passwords for the PC A analyst, then the right one. | Every refusal has the same plain message and does not say whether the account exists; nothing secret in the page or `Logs`; the right password signs in. |
| TC-12 (C2-USE-01) | The browser walk: `set RUN_UI_WALK=1`, `UI_WALK_PYTHON=<python with playwright>`, then `pytest -m integration tests\integration\test_ui_walk.py` (maintainer guide section 9). Then look at Computers, Retention and Accounts yourself at 390 px (browser dev tools, device toolbar). | The walk passes; no page scrolls sideways. |
| TC-19 (C2-SYS-02) | Sample replay with *Save attacks*. Pick one Attack row in Recent Flows and note its band and percentage. Find the same alert on **Alerts**, put it in a report (**Reports**), and look at the **Dashboard**. One Manual Prediction. On **Alerts**, set the dates to October 2026. | The same band and percentage everywhere; the band matches its thresholds; no "Critical" anywhere; October's older alerts say **Not scored** with their legacy severity. |
| TC-21 (C2-SYS-03) | Administrator, **Retention**. Read *A cleanup now would remove* and *Kept although old*. **Do not press Remove expired records.** | The preview lists counts and kept evidence; the audit list does not change; nothing is removed. |
| TC-22 (C2-SEC-01) | Sign in (time T0). Start a sample replay at *Slow*, *Session only*, and leave it running. At T0 + 65 minutes, make a Manual Prediction. Put the PC to sleep for 10 minutes, wake it, and make another Manual Prediction. | Both predictions **Synced**, with no sign-in page; the Dashboard upload status is *Active*; nothing lost. |
| Soak (C2-PRF-01) | During a 30-minute sample replay at *Normal*: `venv\Scripts\python.exe test-results\prf01\soak_sampler.py`. | CPU max ≤ 80%; RAM max ≤ 1 GiB; no steady growth (compare the halves). |
| Automated (C2-UNT-01, C2-ENV-02, C2-INT-01, C2-SEC-01) | `scripts\RUN_TESTS.cmd`, started with right-click **Run as administrator** while the local stack runs. It runs the offline suite, lint, type check, browser polling tests, pip-audit, Bandit and the integration lane with the Npcap tests. Then coverage (TC-16): `venv\Scripts\python.exe -m pytest --cov=services -q`. Then the clean-install release check: `set RUN_CLEAN_INSTALL=1` and `venv\Scripts\python.exe -m pytest -m integration tests\integration\test_clean_install.py`. Check CI on GitHub for the build commit. | Every suite passes; coverage of `services/` ≥ 80%; every scan finding fixed or justified; CI green. |

## 6. Re-tests carried from cycle 1 (C2-REG-01)

| Defect | Re-tested by | Closed when |
|---|---|---|
| DEF-10 upload delay | TC-23 | Every capture's p95 ≤ 5 s on the pilot, on both PCs and both at once |
| DEF-17 live lag | TC-11 | p95 ≤ 500 ms for FIN/RST flows on both PCs |
| DEF-19 benign live traffic called Attack | TC-24 | ≤ 8% on both PCs |

Results go on each DEF card's "Cycle 2 results" section and in the Test Log. A defect that fails on either PC is reopened with that PC's figures.

## 7. Teardown (every session)

Stop every capture. On each PC, the Dashboard shows nothing pending (or **Export pending records** has saved what is left). Sign out. Copy the evidence to `test-results/cycle2_<date>/`, update the Test Log and the cards the same day.
