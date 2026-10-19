# AlgoGuard Test Cycle 1 — Master Prompt

> **How to use:** open the AlgoGuard repository folder on PC A in VS Code, start Claude there, and paste everything below the line as your first message. Put `1 - AlgoGuard Master Test Plan v0.1.docx`, `0 - AlgoGuard Testing Kanban card sheet.docx` and `algoguard-test-tooling.patch` in `docs/testing/cycle-1/` first.

---

You are helping me (Cel Hendrix N. San Miguel, "Alec", Lead Architect) run **test cycle 1 of AlgoGuard** on this PC. I run the tests; you run the commands, guide me through the manual steps, and keep the records. Work through the phases below in order and stop at each **CHECKPOINT** to show me what you recorded.

## 1. Context

- Repository: `AlgoGuard-CSThesis/AlgoGuard`, branch `main`, starting at commit **`cb3bcdb`** (Stage 5F closed). `cb3bcdb` only changed documentation, so the product code under test is identical to **`940e082`**, the build named in the plan.
- Iteration 5's two-computer pilot (Oct 8–16) is recorded in `docs/migration/05f-pilot.md` and `docs/migration/05-iteration-summary.md`. It ran on this same product code, so its results count as evidence for SYS-03 and PRF-01 (see Phase 6).
- Test basis: **Master Test Plan v0.1** (`docs/testing/cycle-1/1 - AlgoGuard Master Test Plan v0.1.docx`). It follows our *Standard for Software and System Test Documentation*. Read it first with `pandoc -t plain` (or python-docx). Use its sections 1.2 (features F1–F8), 3.3 (exit criteria), 3.4 (performance budgets) and the 16 test cases TC-01 to TC-16 exactly as written. Do not change targets after you see results.
- Work is tracked on the **AlgoGuard Testing Kanban**: 20 cards, each with one owner. The card sheet is `docs/testing/cycle-1/0 - AlgoGuard Testing Kanban card sheet.docx`.

| Card | Owner | Test cases / checks |
|---|---|---|
| ENV-01 Test environment and data | Arella Biacea B. Mirasol | environment checks, evaluation set |
| ENV-02 CI and coverage | Afel Anthonielle V. Quirao | TC-16, CI status |
| SMK-01 Smoke gate | John Brian A. Tiopes | TC-01, TC-02, short CSV replay |
| UNT-01 Unit tests, lint, type check | Cel Hendrix N. San Miguel | TC-13 |
| INT-01 Integration | Afel Anthonielle V. Quirao | TC-08, TC-09, integration lane |
| SYS-01 Detection quality | Victor Alexander A. Ong | TC-03 |
| SYS-02 Traffic sources and bad input | John Brian A. Tiopes | TC-04, TC-05 |
| SYS-03 Node isolation and access | Cel Hendrix N. San Miguel | TC-06, TC-07 |
| PRF-01 Performance | Afel Anthonielle V. Quirao | TC-11, soak |
| SEC-01 Security | Arella Biacea B. Mirasol | TC-10, scans, roles, secrets |
| USE-01 Usability | Brynt M. Timtiman | TC-12, accessibility |
| REG-01 Regression | Cel Hendrix N. San Miguel | TC-14 |
| UAT-01 UAT | Pauleen Joy O. Gomez | TC-15 (she runs it) |
| DOC-05 Status report | Brynt M. Timtiman | report draft |
| DOC-06 Completion report | Chate Mae N. Caluba | report draft |
| LOG-01 Test Log and defects | Jan Leigh Augustine A. Munoz | the log and DEF list |

## 2. Rules

1. **Credit:** every log row names the **Owner** of its card, exactly as in the table. The Test Log has **no "tester" or "executed by" column**; do not add one, and do not write anywhere that a specific person executed a test.
2. **Never invent results.** Every Actual result must come from a command output, a file, or what I tell you I observed. If something cannot be run, the status is **Not executed** with the reason. A failed result is a valid result: log it and raise a defect. Do not "fix" the product to make a test pass during this cycle (fixes are done separately and verified in REG-01).
3. **Credentials:** never open, print or read `.env`, and never type or ask me to paste passwords, keys or tokens. I sign in myself. Redact e-mails, tokens and keys from anything you save.
4. **Shared cloud project:** do nothing destructive on the shared Supabase pilot. Membership revocation (TC-07) only on test accounts we created for this; the corrupted-model test (TC-08) only on a backed-up local cache that you restore afterwards. Do not publish or deploy models to the cloud.
5. **Network:** you lose your connection if I disconnect this PC. For TC-09, give me the steps first, then I do them and tell you what I saw.
6. **Build:** record the short SHA of `HEAD` in every row. After Phase 0 it is the tooling commit on top of `cb3bcdb`; note "product code = 940e082" in the evidence column.
7. Use Asia/Manila time (`YYYY-MM-DD HH:MM`) for every timestamp.

## 3. Records you keep (deliverables)

Create them in `docs/testing/cycle-1/` and update them after every test:

- **`2 - AlgoGuard Test Log v1.0.xlsx`** (openpyxl), sheets:
  - *Test Log*: Date / time · Case ID · Card · Owner · Build · Expected result · Actual result · Status (Pass / Fail / Blocked / Not executed) · Defect ID · Evidence. One row per execution, re-runs included.
  - *Defects*: Defect ID (DEF-01…) · Date found · Case / Card · Card owner · Title · Severity (plan's definitions) · Priority · Steps to reproduce · Expected · Actual · Build · Environment · Status · Fixed in commit · Re-test result.
  - *Automated Runs*: Date / time · Step · Build · Result · Key line · Log file.
  - *Exit Criteria*: each criterion in plan 3.3 and budget in 3.4 · Target · Measured · Met / Not met / Not measured · Source.
  - *By Owner*: per owner — cards, cases executed, passed, failed, not executed, defects raised.
- **`test-results/`** — keep every log and evaluation folder the scripts create.
- **DEF card text** — for each defect, a ready-to-paste GitHub card body in `docs/testing/cycle-1/defects.md`.

## 4. Phases

### Phase 0 — Restore tooling (M3)
1. `git status` must be clean and `HEAD` must be `cb3bcdb`. Confirm `git diff --stat 940e082 HEAD` lists only documentation files (no `.py`, `.js`, `.html`). If not, stop and tell me.
2. `git am docs/testing/cycle-1/algoguard-test-tooling.patch` (adds `scripts/run_test_suites.py`, `scripts/evaluate_model.py`, `scripts/RUN_TESTS.cmd`, `scripts/START_APP.cmd`; no product code). Move the three documents and the patch out of the commit's way first if git complains, then put them back.
3. In `scripts/RUN_TESTS.cmd`, the evaluation line points to `saved_models\run_3`. Change it to the model you use in SYS-01 (step below) and commit that change separately.

### Phase 1 — Environment (ENV-01 · Arella, ENV-02 · Afel)
1. Check: Windows 11 version, Python in `venv` is 3.14.6, `pip install -r requirements-dev.txt` succeeds, `pip install pytest-cov pip-audit bandit`, Npcap installed, whether this terminal is elevated. Log the results as ENV-01 rows.
2. Datasets present: `datasets/algoguard_big.csv`, `algoguard_bigger.csv`, `algoguard_biggest.csv`, `research/dataset/raw/UNSW_NB15_training-set.csv` (82,332 rows). Record row counts.
3. Ask me whether the official `UNSW_NB15_testing-set.csv` (175,341 rows) is available. If I put it in `research/dataset/raw/`, record it; it is needed for model B in SYS-01.
4. Ask me whether PC B (second PC) is available today for SYS-03 and record the answer.
5. ENV-02: `gh run list --branch main --limit 5` to record the CI result for the current main (if `gh` is not signed in, ask me to read the result from the Actions tab). Then TC-16: `python -m pytest --cov=services --cov-report=term -q` and record line coverage of `services/` against ≥ 80%.

**CHECKPOINT 1** — show me the ENV rows.

### Phase 2 — Unit baseline (UNT-01 · Cel) — TC-13
Run `python scripts/run_test_suites.py --security` (add `--integration` if `npx supabase status` shows the local stack running). Record one Automated Runs row per step and the TC-13 row. A flaky failure: re-run once, log both, raise a Low defect. pip-audit and Bandit results go to SEC-01 (Phase 6).

### Phase 3 — Smoke gate (SMK-01 · John Brian) — entry criterion
1. Start the app with `scripts\START_APP.cmd` (it runs `python app.py`; cloud mode comes from `ALGOGUARD_DB_MODE=supabase` in `.env`). I sign in.
2. TC-01 / TC-02: pick one row labelled Normal and one labelled Attack from `datasets/algoguard_big.csv` (print the values for me), I enter them on Manual Prediction and tell you the verdict, confidence and whether an alert was created.
3. Short CSV replay of `algoguard_big.csv` on Live Monitor: I report flows, Attack/Normal counts, alerts, errors.
4. Any failure here **suspends** testing (plan 3.2). Tell me and stop.

**CHECKPOINT 2** — smoke gate result.

### Phase 4 — Detection quality (SYS-01 · Victor) — TC-03 (model option C)
- **Model A (official result):** the model currently deployed. Use the cached deployed artifact if it is on this PC (look in `saved_models/`; ask me which run is deployed). If it is not here, train a local copy the same way: `python train.py datasets/algoguard_big.csv --models-dir saved_models/cycle1_A` (no `--deploy`).
- Evaluate A: `python scripts/evaluate_model.py --model <A>.joblib --compare-dir <A's run folder>` → accuracy, F1, recall, FPR, ROC-AUC vs plan 3.3; Stacking vs each single model; recall per attack category (flag < 90%).
- **Reproducibility:** train A again into `saved_models/cycle1_A2` and evaluate; numbers must match.
- **Model B (comparison only, not the official result):** `python train.py research/dataset/raw/UNSW_NB15_training-set.csv --models-dir saved_models/cycle1_B` (no `--deploy`; record the training time — it is useful for PRF-01). Evaluate B **only on `UNSW_NB15_testing-set.csv`** (B has seen the whole training set). If the testing set is available, also evaluate A on it so A and B are compared on the same data. If it is not available, B's evaluation is Not executed.
- Each target missed by model A → one High defect.

### Phase 5 — Integration (INT-01 · Afel) — TC-08, TC-09
1. If the local Supabase stack runs: `python -m pytest -m integration -q` and record. If not, record Not executed with the reason (Docker/Supabase CLI not running).
2. Run `python -m pytest -q tests/test_model_delivery.py tests/test_model_publication.py tests/test_cloud_outbox.py tests/test_model_architecture.py` and record (Stacking uses five base models + Logistic Regression).
3. TC-08 (manual): back up the client model cache folder, change one byte in the cached artifact, restart the app, I report what it shows; then restore the backup and confirm the app loads the model again.
4. TC-09 (manual, I do it): start a CSV replay in cloud mode, disconnect Wi-Fi, stop and restart the app, reconnect, sign in; I report pending vs synced counts and duplicates.

### Phase 6 — Sources, security, isolation, usability, performance
- **SYS-02 · John Brian** — TC-04: create the PCAP as in `docs/migration/05f-two-computer-runbook.md` section B (`write_sample_pcap`), replay it, I report flows classified/synced. Live capture for ~60 s: I report packets, flows, drops and excluded own-traffic packets. TC-05: on Manual Prediction I enter a missing field, `abc` in a numeric field, `1e308` and a negative byte count; I report each message; check nothing was stored. Bad files: you create an empty `.pcap`, a text file renamed `.pcap` and a truncated copy of the PCAP; I upload each. Check the 300-event cap message on a full `algoguard_big.csv` replay.
- **SEC-01 · Arella** — record pip-audit and Bandit from Phase 2 (justify each Medium/High or raise a defect). TC-10: start the app in **local (SQLite) mode** without touching `.env` — in a new cmd window run `set ALGOGUARD_DB_MODE=sqlite` then `python app.py` (environment variables override `.env`). I sign in as the local administrator and create a throwaway test account on the Admins page (or use an existing test account), then sign out. I try five wrong passwords on that test account and report the message on the sixth try. Close this local-mode app before going back to cloud mode. Roles: signed in as an analyst, I open `/admins` and `/nodes` and report. CSRF: `python -m pytest -q tests/test_api_validation.py tests/test_login_throttle.py`. Secrets: `git ls-files | findstr /i ".env"` must list only the `.example` files; `git grep -n -E "sb_secret_|service_role|eyJ[A-Za-z0-9_-]{20,}"` must find nothing outside tests/fixtures — show me hits, do not print values.
- **SYS-03 · Cel** — first record the two-PC pilot evidence from `docs/migration/05f-pilot.md` (cross-node isolation, hosted RLS check, revocation, offline → reconnect) as SYS-03 rows with Evidence = the section of 05f-pilot.md and Date = the pilot date it states; quote the figures exactly as the record gives them. Then, only if PC B is available today, re-run TC-06 and TC-07 live: follow `docs/migration/05f-two-computer-runbook.md` with two **test** analyst accounts on different nodes. TC-06: analyst A tries to see node B's alerts in the UI. TC-07: revoke analyst B (test account), B tries prediction and capture, then Restore. If PC B is not available: Not executed, reason "second PC not available", and note that the automated two-node test in the integration lane covers isolation.
- **USE-01 · Brynt** — TC-12: I check each page (Dashboard, Live Monitor, Manual Prediction, Alerts, System Logs, Reports, Accounts/Admins, Nodes) in Chrome DevTools device mode at 1366 px and 390 px and report any horizontal scroll or broken layout. Accessibility: I run Lighthouse → Accessibility on Login, Live Monitor and Manual Prediction and tell you the scores and listed issues; serious issues → defects.
- **PRF-01 · Afel** — first record the pilot's measured budgets from `docs/migration/05f-pilot.md` §4 (including limits L1 and L2) as PRF-01 rows; a budget the pilot did not meet is Fail with a Medium defect, not re-targeted. Then TC-11: during a 60 s live capture, compute event-to-verdict lag p50/p95 from the timestamps the app stores (flow end vs verdict time; exclude the idle-timeout wait as the plan says) — read them from the local outbox/SQLite, not from screenshots. Throughput: a fast CSV replay of `algoguard_biggest.csv`, events per second. Packet drop from the live capture counters. Soak: 30-minute CSV replay on loop or live capture; you write a small psutil script that samples the AlgoGuard process CPU % and RSS every 30 s to `test-results/soak_<time>.csv`. Each missed budget → Medium defect.

**CHECKPOINT 3** — show me the log so far and the open defects.

### Phase 7 — Regression (REG-01 · Cel) — TC-14
Only if defects were fixed during the cycle (fixes are separate commits by me): re-run each fixed case on the fix commit, update the Defects sheet, re-run `run_test_suites.py`, re-run the smoke gate. If nothing was fixed, record REG-01 as executed with "no fixes in cycle 1; full suite re-run" and re-run the suite once at the end.

### Phase 8 — UAT preparation (UAT-01 · Pauleen)
Write `docs/testing/cycle-1/UAT scenarios.md`: 3–5 scenarios (detect an attack in a replay; review, acknowledge and resolve an alert; administrator approves a node member; read the Reports page), each with steps, expected result, and a Pass/Fail line and signature line for Pauleen. **She executes them.** Until she does, UAT-01 rows are Not executed with reason "awaiting Product Owner".

### Phase 9 — Reports (DOC-05 · Brynt, DOC-06 · Chate)
1. **Test Status Report** (`4 - AlgoGuard Test Status Report - 2026-10-16.docx`): planned vs executed per card, defects by severity, blockers. Header: prepared for Brynt M. Timtiman (DOC-05).
2. **Test Completion Report v1.0** (`3 - AlgoGuard Test Completion Report v1.0.docx`), following the standard's Test Completion Report section and the same look as the Master Test Plan (Times New Roman, the template's cover fields): summary and overall verdict; results by card with owner; detection metrics (A official, B comparison); performance budgets; defects; cases not executed with reasons; every exit criterion Met / Not met; residual risks and workarounds; lessons learned; approvals table (Test Manager, Project Manager, Lead Architect, Product Owner) with blank signatures. Author line: Cel Hendrix N. San Miguel (author); Chate Mae N. Caluba, Test Lead (DOC-06).
3. Check every number in both reports against the Test Log and the `test-results/` files before you finish. List anything you could not verify.

### Phase 10 — Wrap up
1. Show me the per-owner summary (cards, executed, passed, failed, not executed, defects).
2. Tell me which Kanban cards to move to Done / In Review and give a one-line result comment for each card.
3. Commit the records (`docs/testing/cycle-1/`, `test-results/`) with message `test: record test cycle 1 results`. **Do not push** until I say so.

**FINAL CHECKPOINT** — list the deliverables and anything still Not executed.
