# Stage 6D — Packaging, documentation, and final QA

Status: implemented and verified on 2026-11-04 on Windows against the local Supabase stack. **No
migration** was added; the hosted pilot already has every migration and model 60 (checked
read-only on 2026-11-04). The research release gate stays unmet (06a), so this release is a
**limited research pilot** in the documented scope.

## Outcome

| Sub-stage | Result |
|---|---|
| 6D.1 Entry points | Cloud mode is the default; legacy local mode needs `ALGOGUARD_DB_MODE=sqlite`. `SETUP_ALGOGUARD.cmd` and `START_ALGOGUARD.cmd` set up and start an analyst installation; `python config.py` checks the settings. `scripts/build_analyst_package.py` builds an allow-listed, checked analyst zip. Runtime packages are pinned exactly. Training and every other privileged tool stay out of the package. |
| 6D.2 Documentation | `docs/ANALYST_GUIDE.md` (in the package) and `docs/MAINTAINER_GUIDE.md`; README and user manual rewritten around the cloud application, with the legacy mode and the research limits stated. |
| 6D.3 Clean installation | The package, set up in an empty folder, reached monitoring with every source, survived restart and an outage, refused hostile edits and requests, and held no privileged value. It found one defect (uploads blocked after approval) and dependency drift; both fixed and re-checked. |
| 6D.4 Final QA | Every lane passes, including the clean install and a browser walk of every page at 1366 px and 390 px with no console errors, failed requests or page wider than the screen. The walk found four presentation defects and a missed pilot finding; all fixed, the last one after the final full lane (see Verification). |

## 6D.1 — Analyst and maintainer entry points

Code: `config.py`, `app.py`, `app_version.py`, `SETUP_ALGOGUARD.cmd`, `START_ALGOGUARD.cmd`,
`.env.example`, `scripts/build_analyst_package.py`, model and approval messages in
`model_delivery.py`, `model_runtime.py`, `cloud_app.py`, `cloud_monitor.py` and the Live Monitor,
Manual Prediction and Dashboard pages. Tests: `tests/test_analyst_package.py`.

- **Cloud mode is the default.** `ALGOGUARD_DB_MODE` now defaults to `supabase`. The legacy SQLite
  business application runs only when `ALGOGUARD_DB_MODE=sqlite` is set explicitly, and says so
  when it starts. An installation with no cloud settings is "not set up yet": `python app.py`
  refuses to start with setup help (no traceback), while maintainer tools that never serve the
  app (`train.py`, `migrate.py`, `publish_model.py`) keep working on the maintainer's training
  store. Half a cloud configuration, a secret key or a non-HTTPS remote URL is still an error.
  `python config.py` checks the settings without starting anything (exit 0 ready, 2 invalid,
  3 cloud settings missing).
- **Setup and start.** `SETUP_ALGOGUARD.cmd` checks for Python 3.14.6, makes the `venv`, installs
  `requirements.txt`, creates `.env` from `.env.example` and opens it in Notepad until
  `config.py` accepts it, and says whether Npcap is installed. `START_ALGOGUARD.cmd` checks the
  setup and starts the app; it explains when to use "Run as administrator" (only if live capture
  is listed as unavailable: Npcap's default install lets AlgoGuard capture without it).
- **Training stays a maintainer tool.** Analyst pages never offer training. The messages an
  analyst sees for a missing or unusable model now say what to do and whom to ask: "Model not
  available" replaces "No active deployment" in cloud mode; a corrupt cached model names the
  folder to delete; a Python or package mismatch names the version and `SETUP_ALGOGUARD.cmd`;
  approval refusals, including a capture started before approval, say to give the administrator
  the installation ID shown on the Dashboard.
- **Analyst package.** `scripts/build_analyst_package.py` builds `dist/AlgoGuard-analyst-<version>.zip`
  from the last commit (or the working tree with `--worktree`). Files come from an allow list:
  the modules reachable from `app.py` and the two worker processes (31 modules, found by reading
  every import, including imports inside functions), the pages, styles, sample datasets, the two
  scripts, `.env.example`, `requirements.txt`, `requirements-model.lock` and the analyst guide.
  The build refuses settings, database, model and capture files, secret keys, passwords in
  connection strings, private keys, signed tokens, real e-mail addresses, and any packaged module
  that imports one left out. It writes `BUILD` (release and commit), which `app_version()`
  reports on the Computers page when an installation has no git. The package is 70 files.
- **Exact dependencies (pilot finding 10).** `requirements.txt` pins every analyst runtime package
  and its dependencies (including `narwhals`, which scikit-learn needs and nothing pinned) to the
  versions every check ran with, instead of `>=` (`d0f89d4`, `d03ceb2`; 6D.3).
- **Legacy tooling kept.** `legacy_backup.py`, `legacy_import.py`, `migrate.py`, `train.py` and
  the SQLite routes stay for maintenance and the test suite. The local upload queue
  (`.algoguard/outbox`) remains separate from the old business database, which cloud mode never
  opens or creates (`test_app_entry_point_serves_the_cloud_app_without_touching_sqlite`).

## 6D.2 — Installation and operating documentation

- **`docs/ANALYST_GUIDE.md`** (in the package): requirements (Windows, Python 3.14.6, Npcap), setup,
  `.env`, online e-mail/password sign-in, approval of the installation, the three traffic sources
  and storage choices, where records are (Session, Memory, Pending, Synced, Not stored, Rejected),
  what works offline and what does not, the 7-day expiry, queue and disk limits, sign-in renewal,
  how to read attack scores and their limits, the administrator pages (Accounts, Nodes, Computers,
  Retention), a troubleshooting table (settings, Python, sign-in and clock, connectivity, approval,
  every model message, Npcap, blocked uploads, Not stored, Rejected, retention, port in use) and
  updating or removing an installation.
- **`docs/MAINTAINER_GUIDE.md`**: roles and the two settings files, a maintainer computer, setting
  up a cloud project (migrations, Edge Function, sign-ups off, first administrator, first model),
  migrations, training, evaluation, publishing, rollback by republishing and cleanup of unused
  uploads, building analyst packages, backups and recovery, moving legacy installations,
  checks and test lanes, and the environment overrides kept for tests and automation.
- **README** now starts with the cloud application, which guide to read, the runtime parts and an
  analyst quick start; the old SQLite setup became "Legacy Local Mode"; Troubleshooting and Scope
  reflect the release. **`ALGOGUARD_USER_MANUAL.txt`** gained a release note, cloud setup and
  sign-in, the cloud pages, attack scores instead of confidence severities, uploads, a cloud demo
  checklist, and Part B (training) marked maintainer only.

## 6D.3 — Installation from a clean folder

Test: `tests/integration/test_clean_install.py` (opt-in: `RUN_CLEAN_INSTALL=1`; it installs the
packages from the internet). Result: `test-results/iter6_6d/clean_install.json` and the installed
app's log `clean_install.app.log`; dependency evidence `dependency_drift.md`.

The package was built from the last commit, unpacked into an empty folder outside the repository
and set up with `SETUP_ALGOGUARD.cmd`, with the two cloud values written into `.env` as an analyst
does in Notepad. The installed app then ran as its own process, started as `START_ALGOGUARD.cmd`
starts it, against the local Supabase stack, and was driven over HTTP the way its pages are. The
administrator and maintainer steps (accounts, publishing model 60 to the local stack) used the
maintainer path. No training command ran and the installation never had a database, a
`saved_models` folder or `train.py`.

**Final run, 2026-11-04 03:11, build `0.6.0+24b6eab`** (part of the final QA lane): Windows 11
(10.0.26300), Intel 12-thread CPU, 15.7 GiB RAM, Python 3.14.6, Npcap 1.10.6. Package 70 files,
1,332 KiB.

| Check | Result |
|---|---|
| Setup | `SETUP_ALGOGUARD.cmd` exit 0 in 153 s (environment and packages from the internet); "Settings OK"; no Npcap note (installed) |
| Enrollment | First sign-in shows **Awaiting approval** and the installation ID; a capture started before approval ends with the approval message naming the installation ID; the administrator approves on the Nodes page; the Dashboard shows **Node approved** without a restart |
| Model | Model 60 (11,596,540 bytes) downloaded and verified on the first visit to Predict |
| Manual prediction | Normal, attack score Low; uploaded 28.7 s after approval with no Retry uploads (see the finding below) |
| CSV replay (Maximum, Save every flow) | 960 flows before the cap; 300 stored (the session cap), 660 Not stored; all 300 synced, 300 unique events in the cloud; bands Low, High, Very high shown |
| PCAP replay | 11 flows (10 TCP sessions and DNS), all synced, 11 in the cloud |
| Live capture (40 s, default interface) | 4 flows, all synced. The window held little traffic; the larger live runs are in 6A and 6C |
| Restart | The old browser session is sent to sign-in; after signing in, the cached model is reused (same file, same modification time); restart 3.0 s, first start 25.7 s |
| Cloud outage during a capture | The API gateway stopped for 25 s during a Fast CSV capture: 66 records waited as Pending, upload status Retrying; all 113 synced within 16.9 s of reconnecting; 113 unique events, none lost or duplicated |
| Direct requests with the analyst's token | 0 rows of other installations in `ingest_event`, `capture_session` and `alert`; another installation's event by ID not returned; creating an Administrator through `account_admin` 403; inserting an Administrator role 403; approving itself on another installation 403; role unchanged |
| Edited `node-id` | Set to another approved installation's ID: sign-in shows Awaiting approval and capture is refused |
| Secret key in `.env` | `config.py` exit 2 and `app.py` exit 1 with the explanation, no traceback |
| Privileged values in installed files | 124 installed files outside third-party packages, including `.env` and the `.algoguard` state (queue, installation ID, model cache): none contains the maintainer secret key, the database connection string, either password, or the analyst's access and refresh tokens |

### Found by the clean install and repaired

- **Uploads stayed blocked after approval** (fixed in `d82022f`). The upload worker checks
  membership right after the first sign-in. On a new, not yet approved installation that check is
  refused, which blocked uploads, and they stayed blocked after approval until the analyst
  pressed Retry uploads or signed in again, although the Nodes page promised they would start
  without a restart. In the first run the manual prediction never uploaded. While uploads are
  blocked for permission, the worker now checks approval again at most every 30 s and resumes on
  its own; a revoked membership stays blocked
  (`test_uploads_resume_by_themselves_once_the_installation_is_approved`). Repeated: uploaded
  28.7 s after approval.
- **Dependency drift** (pilot finding 10; `d0f89d4`, `d03ceb2`). The first install resolved five of
  fifteen runtime packages to newer versions than the tested environment, and a sixth
  (`narwhals`, needed by scikit-learn) was pinned nowhere. All are now pinned; the final install
  matched all 25 pins with nothing unpinned (`dependency_drift.md`).
- **Administrator rights.** The first start script said live capture is unavailable without
  "Run as administrator"; Npcap's default install captures without it, so the script, guides and
  manual now say to use it only when live capture is listed as unavailable.

**Limits.** The clean folder was on the maintainer's computer, which already had Python 3.14.6,
Npcap and a warm package cache; there was no second computer (as decided for cycle 1). The cloud
was the local stack, not the hosted pilot. No new person followed the guide; that is part of
test cycle 2.

## 6D.4 — Final QA

### Browser walk at 1366 px and 390 px

Test: `tests/integration/test_ui_walk.py` with `ui_walk_driver.py` (opt-in: `RUN_UI_WALK=1` and
`UI_WALK_PYTHON`, a Python with Playwright). Result and screenshots: `test-results/iter6_6d/ui/`
(`ui_walk.json`, one screenshot per page and state).

A cloud app with an approved installation for an analyst and an administrator, model 60 and a
recorded capture, is served on 127.0.0.1 and walked in headless Microsoft Edge at 1366 × 768 and
390 × 844, once as each role: a refused and a correct sign-in, the Dashboard, Predict and its
result, the Live Monitor with each source started and ended from the page (sample replay,
recorded packets, live capture), Alerts and a report created from selected alerts, Reports and a
report, Logs, Nodes, and for the administrator Accounts (with an account created), Computers,
Retention (preview), the monitor and prediction pages, an unknown address, and logout (through
the folded menu at 390 px). Final walk: 65 page views covering 33 pages and states. Sample replay and recorded packets completed at both widths with attack scores in the feed; live capture ran 25 s at each width and saw little traffic. No console error, page error, failed request or unexpected error response, and no page wider than the screen (largest horizontal overflow 0 px).

The walk passes only with no console errors, page errors, failed requests or error responses
other than the two it causes on purpose (the refused sign-in and the unknown address), and no
page wider than the screen.

**Found by the walk and fixed** (`24b6eab`; the record commit for the last two items):

- **Accounts wider than the screen:** 195–204 px at 1366 px and 691–701 px at 390 px. Its
  screen-reader labels are absolutely positioned and escaped the table's horizontal scrolling;
  `.table-responsive` is now positioned, so the table scrolls inside its panel. The one-column
  phone layouts also use `minmax(0, 1fr)` so a wide child cannot widen the page.
- **Unknown addresses** showed Flask's unstyled "Not Found"; they now get the console's own
  "Page not found" page (JSON for API callers).
- **Missing site icon:** every page asked for `/favicon.ico`, which did not exist; an SVG icon is
  linked from every page.
- **Retention 26 px wider than a phone screen:** the capacity warning badge ("Pressure: run a
  cleanup or shorten what is kept") did not wrap. It appeared only once the local stack's
  database passed 80% with test data. Badges in panel headings now wrap.
- **Pilot finding 16** (an alert marked Resolved twice by a double click), assigned to 6B, had not
  been fixed: a submitted form's buttons are now disabled, so a second click cannot repeat the
  action.

The page's Content Security Policy refuses evaluated strings (`script-src` has no
`unsafe-eval`); the walk had to poll from Python because Playwright's `wait_for_function` depends
on evaluation. That confirms the policy rather than a defect.

### Re-checks

| Backlog item | Evidence | Result |
|---|---|---|
| Malformed JSON | O `test_invalid_json_bodies_are_rejected_before_actions`, `test_json_actions_require_json_content_type` (legacy); new O `test_malformed_json_is_refused_before_anything_is_queued_or_started` (cloud: broken JSON, arrays, scalars, empty body, wrong content type on `/predict` and `/monitor/start` answer 400 and nothing is queued or started) | Passed |
| CSRF | O `test_cookie_writes_require_csrf_but_bearer_api_calls_do_not`, `test_login_rejects_missing_csrf_token`, `test_json_endpoint_rejects_missing_csrf_token` | Passed |
| Role and node isolation | I `test_real_api_crud_and_administrator_write_scope`, `test_protected_tables_views_and_revocation`, `test_a_profile_cannot_grant_itself_a_role`, `test_edge_refuses_anonymous_analyst_and_demoted_admin`, `test_administrators_read_every_node_and_analysts_only_their_own`; O `test_analysts_see_only_their_computers_even_with_a_hand_edited_url`, `test_analysts_can_neither_preview_nor_run_a_cleanup`; the clean install's direct requests and edited `node-id` | Passed |
| Outages | 6C.2 durable-recovery lane (lost answers, cut-off requests, killed uploader, stalled network); the clean install's gateway outage | Passed |
| Artifacts | I `test_manifest_and_model_object_attacks_are_refused`, `test_a_missing_model_file_fails_safely_and_recovers_when_restored`; O `test_failed_download_never_promotes_or_deserializes`, `test_cached_corruption_and_failed_update_keep_pinned_model` | Passed |
| Concurrent publication | I `test_independent_publishers_serialize_and_retry_does_not_reactivate`, `test_two_independent_nodes_download_and_write_concurrently` | Passed |
| Recovery | I `test_every_application_table_is_backed_up_or_deliberately_excluded`, `test_two_computers_with_pending_records_restore_and_replay_under_their_own_users`, `test_post_cutover_forward_recovery_retains_new_writes_artifacts_and_pending_events` | Passed |
| Clean-install acceptance | I `test_a_clean_install_reaches_monitoring_without_training_or_privileged_keys` (6D.3) | Passed |
| No unexplained errors | The browser walk (above); no `Traceback` in the installed app's log through the whole clean install (`clean_install.app.log`, 164 lines); the lanes' failures are the ones recorded under Verification | Passed |

### Research and operating evidence attached to the release

| Evidence | Where |
|---|---|
| Feature fidelity (15 of 16 within predeclared tolerances; `sload` with the link-layer correction) | [06a-evidence.md](06a-evidence.md) §6A.1 and addendum |
| Independent evaluation on the untouched set; calibration assessment | 06a §6A.2; `test-results/iter6_6a/evaluation_final/`, `calibration_6a2.json` |
| Benign false alarms on live traffic (DEF-19), live latency (DEF-17) | 06a addendum; `results_def19_part2.md`, `tc11_inapp_def17_fix.json` |
| Performance under sustained load, outage recovery | [06c-recovery.md](06c-recovery.md) §6C.4; `test-results/iter6_6c/sustained_6c4.json` |
| Recovery drills, backups, model updates | 06c §6C.1–6C.3 |
| Administrator visibility and retention | [06b-operations.md](06b-operations.md) |
| Clean installation, dependency pinning, browser walk | This record; `test-results/iter6_6d/` |

The research targets are not met, so the release is limited to the documented pilot scope: the
README's Scope and "Accuracy, Scores and Their Limits" and the analyst guide's section 7 tell
every reader that a score is not a measure of harm or a guaranteed likelihood, that live
verdicts are leads to check, and that attack detection on live traffic is untested.

## Verification

Run on Windows on 2026-11-04 (Python 3.14.6, the pinned packages, the local Supabase stack,
Npcap 1.10.6, Microsoft Edge for the browser walk):

- Offline suite (`pytest`): **669 passed**, 102 deselected (the integration lane), 231.7 s. An
  earlier full run, started straight after a browser walk, had one failure that did not repeat
  in the next full run; its name was not captured.
- Integration lane (`pytest -m integration` with `RUN_WINDOWS_CAPTURE_TESTS=1`,
  `RUN_CLEAN_INSTALL=1` and `RUN_UI_WALK=1`): **101 passed, 1 failed** of 102 in 749.6 s (100
  tests before this stage, plus the clean install and the browser walk). The failure was the
  browser walk: the Retention page was 26 px wider than a 390 px screen, because its "Pressure:
  run a cleanup or shorten what is kept" badge did not wrap. The local stack's database had
  reached 80% of 500 MB with test data, which is what showed that badge. Panel-heading badges now
  wrap (record commit), and the browser walk then passed on its own (161.8 s).
- Clean installation inside that lane: passed (build `0.6.0+24b6eab`, `clean_install.json`). The
  record commit after it changes only the stylesheet and the double-submit guard, both covered by
  the final browser walk.
- `ruff check .` passed; `npm run typecheck` (pyright) 0 errors;
  `node --test tests/monitor_poll.test.cjs` 11 passed.
- Hosted pilot, read only: all 19 migrations applied; model 60 is the only active model and
  matches the local file byte for byte.

## Open items

- **Test cycle 2 on the hosted pilot:** re-test DEF-10 and DEF-19, install the package on the
  pilot with a person who has not used AlgoGuard following the analyst guide, and record the
  results. The hosted pilot needs no change for 6D.
- **Research gate** (06a): untouched-set accuracy, F1 and recall below 90%; calibration only on
  the hold-out; attacks on live traffic untested; benign live evidence from one computer.
- **Legacy local mode** pages were not part of the browser walk; they are maintenance-only and
  their behaviour is covered by the offline suite.
- **Maintainer:** rotate the pilot database password; PC B `git pull` before its next session.
