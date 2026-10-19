# AlgoGuard test cycle 1 â€” defect cards

One section per DEF card. Paste the title into the card title and everything under it into the card body. Source: the Defects sheet of `2 - AlgoGuard Test Log v1.0.xlsx`.

## [DEF-01] CI installs Python 3.14.8 instead of the locked 3.14.6, so every model-loading unit test stops with ModelDeliveryError and CI is red on main

| Field | Value |
|---|---|
| Found by card | ENV-02.1 / ENV-02 |
| Card owner | Afel Anthonielle V. Quirao |
| Date found | 2026-10-19 |
| Severity | Medium |
| Priority | High |
| Build | cb3bcdb (CI); 940e082 (CI) |
| Environment | GitHub Actions windows-latest; setup-python '3.14' (patch level not pinned; requirements-model.lock records 3.14.6); Node 22 |
| Status | Open |
| Fixed in commit | â€” |
| Re-test result | â€” |

**Steps to reproduce**

1. Open GitHub > AlgoGuard-CSThesis/AlgoGuard > Actions > CI.
2. Open run 38886300881 (cb3bcdb) or 38870178965 (940e082), job 'checks'.
3. In 'Run actions/setup-python@v5' see 'python-version: 3.14' and 'Successfully set up CPython (3.14.8)'.
4. In 'Unit tests' see 40 errors and 4 failures with model_runtime.ModelDeliveryError.

**Expected result**

CI runs on the locked runtime (requirements-model.lock / model_runtime.PYTHON_VERSION = 3.14.6, as the workflow comment itself says) and all steps are green.

**Actual result**

Unit tests: '5 failed, 476 passed, 21 skipped, 34 deselected, 40 errors'; 44 of the 45 are ModelDeliveryError ('Install the locked Python model runtime.' x43 from model_runtime.py:38; 'Retrain this artifact with the locked model runtime.' x1 from model_runtime.py:74), because .github/workflows/ci.yml asks setup-python for '3.14' and gets 3.14.8. The remaining failure is DEF-03. Browser polling tests are then skipped. All six main runs since 2026-10-05 failed. The app's refusal of an unlocked runtime is by design; the defect is the unpinned CI Python version. Not reproduced on PC A: the working copy passes (537 passed), and so does a clean clone of cb3bcdb with a fresh Python 3.14.6 venv from requirements-dev.txt only (521 passed, 21 skipped, 34 deselected; exit 0; the 21 skips are 16 tests in test_maintainer_env.py and 5 integration modules that need psycopg2 from requirements-maintainer.txt, which CI does not install either), because PC A runs the locked 3.14.6.

**Evidence:** Test Log rows ENV-02.1 (13:00, 13:50); test-results/env_20261019/ci_run_38886300881_cb3bcdb_failed.log and ci_run_38870178965_940e082_failed.log; Automated Runs rows 'DEF-01 reproduction'; test-results/env_20261019/ci-repro.log, ci-repro-pytest.log, ci-repro-pytest-rs.log; CI runs https://github.com/AlgoGuard-CSThesis/AlgoGuard/actions/runs/38886300881 and .../38870178965

No e-mail addresses, passwords or tokens are included in this card or its evidence.

---

## [DEF-02] Local stack: supabase start exits 0 but leaves the crashed Edge Runtime stopped; Edge Function integration tests then fail with HTTP 503

| Field | Value |
|---|---|
| Found by card | ENV-02.2 / ENV-02 |
| Card owner | Afel Anthonielle V. Quirao |
| Date found | 2026-10-19 |
| Severity | Low |
| Priority | Medium |
| Build | dafe5e5 (product code = 940e082) |
| Environment | PC A (SANMIGUEL), Windows 11 Home 26H2 build 26300.9711, Python 3.14.6 venv; Docker Desktop 29.8.0; Supabase CLI 2.117.0 local stack |
| Status | Open |
| Fixed in commit | â€” |
| Re-test result | â€” |

**Steps to reproduce**

1. Let the supabase_edge_runtime_AlgoGuard container stop (here it had exited with code 255 eleven days earlier).
2. Run npx supabase start (CLI 2.117.0); it exits 0 and lists the Edge Runtime under 'Stopped services'.
3. Run python -m pytest -q -m integration.

**Expected result**

supabase start brings up every service enabled in supabase/config.toml ([edge_runtime] enabled = true), or fails; the integration lane passes.

**Actual result**

Exit 0 with the Edge Runtime still stopped; 5 integration tests fail with HTTP 503 'name resolution failed' from /functions/v1/account_admin. Workaround: docker start supabase_edge_runtime_AlgoGuard, then the lane passes (65 passed, 2 skipped). The same stopped Edge Runtime was noted in 05f-pilot.md section 8.2 (2026-09-30). Test-environment issue; the product is not affected.

**Evidence:** Test Log rows ENV-02.2 and INT-01.1; test-results/env_20261019/edge-runtime-restart.log; test-results/suites_20261019_132412/pytest-integration.log; test-results/integration_20261019_132933/pytest-integration.log

No e-mail addresses, passwords or tokens are included in this card or its evidence.

---

## [DEF-03] test_cap_reserves_before_enqueue_and_retry_does_not_consume_slot fails on the CI runner: 300 queued outbox writes do not finish within drain()'s 5 s limit

| Field | Value |
|---|---|
| Found by card | ENV-02.1 / ENV-02 |
| Card owner | Afel Anthonielle V. Quirao |
| Date found | 2026-10-19 |
| Severity | Low |
| Priority | Medium |
| Build | cb3bcdb (CI); 940e082 (CI) |
| Environment | GitHub Actions windows-latest; CPython 3.14.8 |
| Status | Open |
| Fixed in commit | â€” |
| Re-test result | â€” |

**Steps to reproduce**

1. Open CI run 38886300881 (cb3bcdb) or 38870178965 (940e082), job 'checks', step 'Unit tests'.
2. Find tests/test_cloud_outbox.py::test_cap_reserves_before_enqueue_and_retry_does_not_consume_slot.

**Expected result**

spool.drain() returns True after 300 events are queued (test line 55).

**Actual result**

'assert spool.drain()' -> False in both runs. Outbox.drain() waits at most 5 s (cloud_outbox.py:554-558) for the writer thread to persist the queue. Passes on PC A in the working copy and in the clean clone. The model runtime is not involved. Most likely the GitHub windows-latest runner writes the 300 SQLite rows more slowly than 5 s; not confirmed. It would keep CI red after DEF-01 is fixed.

**Evidence:** Test Log row ENV-02.1 (13:50); test-results/env_20261019/ci_run_38886300881_cb3bcdb_failed.log; ci_run_38870178965_940e082_failed.log

No e-mail addresses, passwords or tokens are included in this card or its evidence.

---

## [DEF-04] Deployed model misses the accuracy target on the independent evaluation set (81.47% vs >= 90%)

| Field | Value |
|---|---|
| Found by card | TC-03 / SYS-01 |
| Card owner | Victor Alexander A. Ong |
| Date found | 2026-10-19 |
| Severity | High |
| Priority | High |
| Build | dafe5e5 (product code = 940e082); model SHA-256 9c9f0fb1...847c (deployment #1) |
| Environment | PC A (SANMIGUEL), Windows 11 Home 26H2 build 26300.9711, Python 3.14.6 venv; scikit-learn 1.9.0 |
| Status | Open |
| Fixed in commit | â€” |
| Re-test result | â€” |

**Steps to reproduce**

1. On the build commit, with the venv active, run:
   python scripts/evaluate_model.py --model saved_models/run_3/stacking.joblib --compare-dir saved_models/run_3
2. The script removes every UNSW_NB15_training-set.csv row whose 15 features appear in datasets/*.csv and scores the rest (53,243 rows).
3. Read 'accuracy' in summary.md.

**Expected result**

accuracy >= 90% (Master Test Plan 3.3).

**Actual result**

accuracy = 81.47% on 53,243 unseen rows (20,674 attack / 32,569 normal). Full result: accuracy 81.47%, F1 79.49%, recall 92.44%, FPR 25.49%, ROC-AUC 0.9371; FP 8,301 of 32,569 normal flows. The same model scores accuracy 96.98% / F1 97.77% / FPR 4.07% on algoguard_big.csv, the data it was trained on: a generalisation gap from training on a 5,000-row sample.

**Evidence:** Test Log row TC-03; test-results/evaluation_20261019_153649/

No e-mail addresses, passwords or tokens are included in this card or its evidence.

---

## [DEF-05] Deployed model misses the F1-score target on the independent evaluation set (79.49% vs >= 90%)

| Field | Value |
|---|---|
| Found by card | TC-03 / SYS-01 |
| Card owner | Victor Alexander A. Ong |
| Date found | 2026-10-19 |
| Severity | High |
| Priority | High |
| Build | dafe5e5 (product code = 940e082); model SHA-256 9c9f0fb1...847c (deployment #1) |
| Environment | PC A (SANMIGUEL), Windows 11 Home 26H2 build 26300.9711, Python 3.14.6 venv; scikit-learn 1.9.0 |
| Status | Open |
| Fixed in commit | â€” |
| Re-test result | â€” |

**Steps to reproduce**

1. On the build commit, with the venv active, run:
   python scripts/evaluate_model.py --model saved_models/run_3/stacking.joblib --compare-dir saved_models/run_3
2. The script removes every UNSW_NB15_training-set.csv row whose 15 features appear in datasets/*.csv and scores the rest (53,243 rows).
3. Read 'F1-score' in summary.md.

**Expected result**

F1-score >= 90% (Master Test Plan 3.3).

**Actual result**

F1-score = 79.49% on 53,243 unseen rows (20,674 attack / 32,569 normal). Full result: accuracy 81.47%, F1 79.49%, recall 92.44%, FPR 25.49%, ROC-AUC 0.9371; FP 8,301 of 32,569 normal flows. The same model scores accuracy 96.98% / F1 97.77% / FPR 4.07% on algoguard_big.csv, the data it was trained on: a generalisation gap from training on a 5,000-row sample.

**Evidence:** Test Log row TC-03; test-results/evaluation_20261019_153649/

No e-mail addresses, passwords or tokens are included in this card or its evidence.

---

## [DEF-06] Deployed model misses the false-positive rate target on the independent evaluation set (25.49% vs <= 8%)

| Field | Value |
|---|---|
| Found by card | TC-03 / SYS-01 |
| Card owner | Victor Alexander A. Ong |
| Date found | 2026-10-19 |
| Severity | High |
| Priority | High |
| Build | dafe5e5 (product code = 940e082); model SHA-256 9c9f0fb1...847c (deployment #1) |
| Environment | PC A (SANMIGUEL), Windows 11 Home 26H2 build 26300.9711, Python 3.14.6 venv; scikit-learn 1.9.0 |
| Status | Open |
| Fixed in commit | â€” |
| Re-test result | â€” |

**Steps to reproduce**

1. On the build commit, with the venv active, run:
   python scripts/evaluate_model.py --model saved_models/run_3/stacking.joblib --compare-dir saved_models/run_3
2. The script removes every UNSW_NB15_training-set.csv row whose 15 features appear in datasets/*.csv and scores the rest (53,243 rows).
3. Read 'false-positive rate' in summary.md.

**Expected result**

false-positive rate <= 8% (Master Test Plan 3.3).

**Actual result**

false-positive rate = 25.49% on 53,243 unseen rows (20,674 attack / 32,569 normal). Full result: accuracy 81.47%, F1 79.49%, recall 92.44%, FPR 25.49%, ROC-AUC 0.9371; FP 8,301 of 32,569 normal flows. The same model scores accuracy 96.98% / F1 97.77% / FPR 4.07% on algoguard_big.csv, the data it was trained on: a generalisation gap from training on a 5,000-row sample.

**Evidence:** Test Log row TC-03; test-results/evaluation_20261019_153649/

No e-mail addresses, passwords or tokens are included in this card or its evidence.

---

## [DEF-07] Deployed model misses the ROC-AUC target on the independent evaluation set (0.9371 vs >= 0.95)

| Field | Value |
|---|---|
| Found by card | TC-03 / SYS-01 |
| Card owner | Victor Alexander A. Ong |
| Date found | 2026-10-19 |
| Severity | High |
| Priority | High |
| Build | dafe5e5 (product code = 940e082); model SHA-256 9c9f0fb1...847c (deployment #1) |
| Environment | PC A (SANMIGUEL), Windows 11 Home 26H2 build 26300.9711, Python 3.14.6 venv; scikit-learn 1.9.0 |
| Status | Open |
| Fixed in commit | â€” |
| Re-test result | â€” |

**Steps to reproduce**

1. On the build commit, with the venv active, run:
   python scripts/evaluate_model.py --model saved_models/run_3/stacking.joblib --compare-dir saved_models/run_3
2. The script removes every UNSW_NB15_training-set.csv row whose 15 features appear in datasets/*.csv and scores the rest (53,243 rows).
3. Read 'ROC-AUC' in summary.md.

**Expected result**

ROC-AUC >= 0.95 (Master Test Plan 3.3).

**Actual result**

ROC-AUC = 0.9371 on 53,243 unseen rows (20,674 attack / 32,569 normal). Full result: accuracy 81.47%, F1 79.49%, recall 92.44%, FPR 25.49%, ROC-AUC 0.9371; FP 8,301 of 32,569 normal flows. The same model scores accuracy 96.98% / F1 97.77% / FPR 4.07% on algoguard_big.csv, the data it was trained on: a generalisation gap from training on a 5,000-row sample.

**Evidence:** Test Log row TC-03; test-results/evaluation_20261019_153649/

No e-mail addresses, passwords or tokens are included in this card or its evidence.

---

## [DEF-08] train.py uses every non-target column as a feature: on the raw UNSW-NB15 file it trains on attack_cat (the label) and id, reports 100%, and local --deploy has no feature-schema check

| Field | Value |
|---|---|
| Found by card | SYS-01.5 / SYS-01 |
| Card owner | Victor Alexander A. Ong |
| Date found | 2026-10-19 |
| Severity | Medium |
| Priority | High |
| Build | dafe5e5 (product code = 940e082) |
| Environment | PC A (SANMIGUEL), Windows 11 Home 26H2 build 26300.9711, Python 3.14.6 venv; local training run 5 |
| Status | Open |
| Fixed in commit | â€” |
| Re-test result | â€” |

**Steps to reproduce**

1. Build a CSV with the UNSW_NB15_training-set.csv columns (e.g. the training file itself, or test-results/sys01_holdout/make_holdout.py's model_b_train.csv).
2. python train.py <that CSV> --models-dir saved_models/cycle1_B (no --deploy).
3. Read the comparison table and the artifact's feature_columns.

**Expected result**

Training uses only the supported 15-feature flow schema (model_runtime FEATURE_SCHEMA_VERSION 'unsw-nb15-fifteen-v1'), or refuses a CSV with other columns; label-derived columns such as attack_cat are never features.

**Actual result**

preprocessing_service.prepare_dataset() takes every column except the last as a feature: 44 features including attack_cat and id. Random Forest, AdaBoost, Gradient Boosting, Naive Bayes and Stacking report 100.00% accuracy / F1 / ROC-AUC (Naive Bayes is 48.72% on 15 features). Cloud publication and download refuse such a model (model_runtime.verify_artifact_contract, called from publish_model.py and model_delivery.py), but the local deploy path (train.py _deploy_stacking -> deployment_service.deploy_model) does not call it - by code reading; --deploy was not run. Workaround: train only on CSVs with the 15 features + label, as the bundled datasets are.

**Evidence:** Test Log row SYS-01.5; test-results/sys01_holdout/train_B_console.log, eval_B_raw_console.log; services/preprocessing_service.py:158-219; model_runtime.py:69-77; train.py:130-200

No e-mail addresses, passwords or tokens are included in this card or its evidence.

---

## [DEF-09] Dashboard shows the model as 'Deployed' while this installation's cached model is corrupt and refused

| Field | Value |
|---|---|
| Found by card | TC-08 / INT-01 |
| Card owner | Afel Anthonielle V. Quirao |
| Date found | 2026-10-19 |
| Severity | Low |
| Priority | Low |
| Build | dafe5e5 (product code = 940e082) |
| Environment | PC A (SANMIGUEL), Windows 11 Home 26H2 build 26300.9711, Python 3.14.6 venv; cloud mode, PC A analyst |
| Status | Open |
| Fixed in commit | â€” |
| Re-test result | â€” |

**Steps to reproduce**

1. Stop the app (cloud mode); back up the model cache folder.
2. Change one byte in the cached .joblib under <state dir>\models.
3. Start the app and sign in as an analyst.
4. Open the Dashboard, then Predict.

**Expected result**

Every page that reports model status says the model cannot be used on this PC (as Predict does).

**Actual result**

Predict: 'No active deployment - The cached model is corrupt. Remove it and retry.' Dashboard: green 'Deployed' badge, Stacking Ensemble, deployment #1 and its training metrics, with no warning. cloud_app.py dashboard() renders active_deployment(repo), the cloud record, and does not check the local cache (the model_message path only covers errors from that cloud lookup). No unsafe behaviour: the corrupt model is never used.

**Evidence:** Test Log row TC-08; test-results/int01_20261019_161543/TC-08_dashboard_while_corrupt.png, TC-08_predict_refused.png; cloud_app.py:522-541

No e-mail addresses, passwords or tokens are included in this card or its evidence.

---

## [DEF-10] Pilot limit L2: p95 upload acknowledgement 24.06 s on one live capture (budget <= 5 s)

| Field | Value |
|---|---|
| Found by card | PRF-01.P1 / PRF-01 |
| Card owner | Afel Anthonielle V. Quirao |
| Date found | 2026-10-19 |
| Severity | Medium |
| Priority | Medium |
| Build | 4c9666c (PC B, 2026-10-10) |
| Environment | PC B (LAPTOP-PNL5IIAJ), Wi-Fi, hosted Supabase pilot (Tokyo) |
| Status | Open |
| Fixed in commit | â€” |
| Re-test result | â€” |

**Steps to reproduce**

From the pilot record (not re-run in cycle 1):
1. PC B, live capture 6 on 2026-10-10 (Default interface, save attacks, commit 4c9666c).
2. Measure upload delay as cloud received_at minus client event_time for the stored events.

**Expected result**

p95 upload acknowledgement <= 5 s (pilot acceptance target, 2026-10-08).

**Actual result**

'p95 24.06 s, max 36.65 s, with 25 events taking 20-30 s+ around 01:12:25-01:12:44Z.' Not reproduced on 2026-10-16 (capture 19 p95 3.44 s, newer code 940e082); the other 11 runs met the budget. 'Cause not identified.' The pilot recorded it as limit L2 rather than re-targeting it.

**Evidence:** Test Log row PRF-01.P1; docs/migration/05f-pilot.md §4, §5 finding 3, §7 L2

No e-mail addresses, passwords or tokens are included in this card or its evidence.

---

## [DEF-11] Pilot limit L1: sustained >= 20 acknowledged events/s per client not demonstrated; shipped sources are paced below it

| Field | Value |
|---|---|
| Found by card | PRF-01.P2 / PRF-01 |
| Card owner | Afel Anthonielle V. Quirao |
| Date found | 2026-10-19 |
| Severity | Medium |
| Priority | Medium |
| Build | pilot 4c9666c / 9e18f30 |
| Environment | PC A and PC B, hosted Supabase pilot (Tokyo) |
| Status | Open |
| Fixed in commit | â€” |
| Re-test result | â€” |

**Steps to reproduce**

From the pilot record (not re-run in cycle 1):
1. Run a CSV replay at Fast speed and count acknowledged events per second.
2. Note the replay pace (SPEED_CHOICES: Fast = one flow per 0.35 s) and the 300-event storage cap per capture.

**Expected result**

Sustained acknowledged events per client >= 20 events/s (pilot budget; also Master Test Plan 3.4).

**Actual result**

PC A '≈ 1.9/s (capture 5: 300 events in 157 s)'; PC B 'backlog drain of 210 events in 4.1 s ≈ 51/s'. 'Sources are paced by design: CSV Fast emits one flow per 0.35 s, ≤ 2.9/s. Each capture stores at most 300 events.' 'A sustained-load harness is needed.' Recorded by the pilot as limit L1.

**Evidence:** Test Log row PRF-01.P2; docs/migration/05f-pilot.md §4, §5 finding 4, §7 L1

No e-mail addresses, passwords or tokens are included in this card or its evidence.

---

## [DEF-12] Manual Prediction and /predict accept missing, empty and negative numeric input: missing values are filled with training defaults and the record is saved

| Field | Value |
|---|---|
| Found by card | TC-05 / SYS-02 |
| Card owner | John Brian A. Tiopes |
| Date found | 2026-10-20 |
| Severity | Medium |
| Priority | High |
| Build | dafe5e5 (product code = 940e082) |
| Environment | PC A (SANMIGUEL), Windows 11 Home 26H2 build 26300.9711, Python 3.14.6 venv; model A (deployment #1) |
| Status | Open |
| Fixed in commit | â€” |
| Re-test result | â€” |

**Steps to reproduce**

1. Signed in, POST /predict (JSON) with the TC-01 Normal row but without 'dur', or with 'dur': ''.
2. Repeat with 'sbytes': '-5'.
(Equivalent: call services.simulation_service.predict_with_artifact with model A, test-results/sys02_files/tc05_server_validation.py.)

**Expected result**

Missing or empty fields and negative byte or packet counts are rejected with a plain message; nothing is classified or stored (TC-05).

**Actual result**

Missing dur -> accepted, filled from the artifact's feature_defaults (simulation_service.py:94), Normal 56.67%; empty dur -> accepted (NaN imputed), 56.67%; sbytes -5 -> accepted, Normal 86.65%. Accepted input is queued for upload (cloud_app.py:415-418). The cloud Manual Prediction form also fills absent fields with defaults before validating (cloud_app.py:608-611). The browser form blocks empty fields (required) but has no min/max, so negatives pass.

**Evidence:** Test Log row TC-05; test-results/sys02_files/tc05_server_validation_result.json

No e-mail addresses, passwords or tokens are included in this card or its evidence.

---

## [DEF-13] An out-of-range number (1e308) is refused with a technical scikit-learn message, not plain language

| Field | Value |
|---|---|
| Found by card | TC-05 / SYS-02 |
| Card owner | John Brian A. Tiopes |
| Date found | 2026-10-20 |
| Severity | Low |
| Priority | Low |
| Build | dafe5e5 (product code = 940e082) |
| Environment | PC A (SANMIGUEL), Windows 11 Home 26H2 build 26300.9711, Python 3.14.6 venv; model A |
| Status | Open |
| Fixed in commit | â€” |
| Re-test result | â€” |

**Steps to reproduce**

POST /predict or Manual Prediction with the TC-01 row and sbytes = 1e308.

**Expected result**

A plain message such as 'sbytes is out of range'.

**Actual result**

'Prediction failed: Input X contains infinity or a value too large for dtype('float32').' The value passes the finite-number check (simulation_service.py:99-108) and fails inside the model.

**Evidence:** Test Log row TC-05; test-results/sys02_files/tc05_server_validation_result.json

No e-mail addresses, passwords or tokens are included in this card or its evidence.

---

## [DEF-14] A truncated PCAP is replayed partially without telling the user it is cut short

| Field | Value |
|---|---|
| Found by card | SYS-02.1 / SYS-02 |
| Card owner | John Brian A. Tiopes |
| Date found | 2026-10-20 |
| Severity | Low |
| Priority | Medium |
| Build | dafe5e5 (product code = 940e082) |
| Environment | PC A (SANMIGUEL), Windows 11 Home 26H2 build 26300.9711, Python 3.14.6 venv |
| Status | Open |
| Fixed in commit | â€” |
| Re-test result | â€” |

**Steps to reproduce**

1. Use captures/cycle1-bad-truncated.pcap (first 60% of the TC-04 sample).
2. Replay it (Live Monitor > Recorded packets, or PcapReplaySource directly).

**Expected result**

The file is rejected, or the user is told the capture is truncated (SYS-02 card: bad files rejected without a crash).

**Actual result**

Not refused: 49 packets read, 7 flows classified; the only sign is a scapy console warning 'Pcap: packet has been truncated'. No crash. Empty and text files are refused correctly.

**Evidence:** Test Log row SYS-02.1; test-results/sys02_files/sys02_pcap_service_check_result.json

No e-mail addresses, passwords or tokens are included in this card or its evidence.

---

## [DEF-15] Live Monitor's scrolling flow feed cannot be reached by keyboard (Lighthouse: scrollable content not keyboard-accessible)

| Field | Value |
|---|---|
| Found by card | TC-12 / USE-01 |
| Card owner | Brynt M. Timtiman |
| Date found | 2026-10-19 |
| Severity | Medium |
| Priority | Medium |
| Build | 940e082 (found); fixed in 503cb9b |
| Environment | PC A (SANMIGUEL), Windows 11 Home 26H2 build 26300.9711, Python 3.14.6 venv; Chrome Lighthouse |
| Status | Fixed - re-test pending |
| Fixed in commit | 503cb9b |
| Re-test result | 2026-10-20 REG-01: fix present on 503cb9b (tabindex="0", role="region", aria-label, :focus-visible outline); full suite passed; Lighthouse re-run not executed. |

**Steps to reproduce**

1. Sign in and open Live Monitor.
2. Run Chrome Lighthouse > Accessibility, or press Tab through the page.

**Expected result**

Every scrollable region can be focused and scrolled with the keyboard.

**Actual result**

Reported through the fix commit 503cb9b ('fix the Ensure elements that have scrollable content are accessible by keyboard in Safari', 2026-10-19 19:48, from the USE-01 owner's account): the .monitor-feed-scroll container had no tabindex, role or label. The Lighthouse report itself was not received by the log keeper.

**Evidence:** git show 503cb9b; Test Log rows REG-01.1, TC-14

No e-mail addresses, passwords or tokens are included in this card or its evidence.

---
