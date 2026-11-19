# AlgoGuard test cycle 2 — Defect reports (C2-LOG-01)

Each defect is a DEF card on the [Testing Kanban](https://github.com/users/bry22191919-hash/projects/4) and a row in the Defects sheet of the Test Log. New cycle 2 defects are numbered from **DEF-20**. Their full text goes below, in the same layout as `docs/testing/cycle-1/defects.md`: found by, card owner, date, severity, priority, build, environment, status, steps to reproduce, expected and actual results, and evidence. No e-mail address, password, token or IP address of real traffic is written here.

Severity follows the Master Test Plan (cycle 2), Incident Report section: **Critical** (data crosses between nodes, an unverified model loads, credentials are exposed, or saved results are lost), **High** (a feature is unusable, known rows get the wrong verdict, or a detection target is missed), **Medium** (a workaround is needed, or a performance budget is missed), **Low** (cosmetic, wording or layout).

## Carried over from cycle 1 for re-test

Their full reports are in `docs/testing/cycle-1/defects.md` and on their cards. Cycle 2 re-tested them on the hosted pilot (C2-REG-01); the whole re-test history is on each card.

| ID | Severity | Fixed in | Re-test in cycle 2 | Closed when | Cycle 2 result |
|---|---|---|---|---|---|
| DEF-10 Upload delay (pilot limit L2) | Medium | Stage 6C, `d69cf63`: up to four upload requests at once; cycle 2, `911f3f8`: the local spool saves a burst in a few writes | TC-23 (C2-PRF-01) | Upload delay p95 ≤ 5 s for every capture, on both PCs and on both at once | **Closed 2026-11-17.** 13 Nov: PC B 3.19 s (both PCs at once); PC A 5.52 s (both) and about 5.5 s (alone): Fail. Cause: the spool flushed the disk for every record, about 45 records/s on PC A. After the fix, PC A alone 3.98 s and live 1.89 s. PC B alone and both PCs on the fixed build not run (Lead Architect decision, 2026-11-17). |
| DEF-17 Live lag p95 above 500 ms (closed in cycle 1; slower again under load in Stage 6A) | Medium | Stage 6A addendum, `5b5a36a`, `5567c4e`: capture and scoring in separate processes | TC-11 (C2-PRF-01) | p95 ≤ 500 ms for FIN/RST flows on both PCs | **Closed.** PC A 2026-11-16, capture 62: p95 407.3 ms over 238 FIN/RST flows. PC B not run (reduced cycle). |
| DEF-19 Benign live traffic labelled Attack | High | Stage 6A, `6cb88e6` and model 60 (training run 10), published to the pilot on 2026-11-04 as deployment #3 | TC-24 (C2-SYS-01) | ≤ 8% of benign live flows labelled Attack on both PCs | **Closed.** PC A 2026-11-16, capture 62: 156 of 2,704 flows = 5.77%, after DEF-28 was fixed. PC B not run (reduced cycle). |

## Cycle 2 defects

Raised during the cycle 2 QA pass and its release check (2026-11-10 to 2026-11-19). Text as on each DEF card.

| ID | Severity | Priority | Status |
|---|---|---|---|
| DEF-20 | Low | Medium | Closed |
| DEF-21 | Medium | High | Closed |
| DEF-22 | Low | Low | Closed |
| DEF-23 | Low | High | Closed |
| DEF-24 | High | High | Closed (fixed by training run 13: release 0.6.1, deployment #4) |
| DEF-25 | Low | Low | Closed |
| DEF-26 | Low | Medium | Closed |
| DEF-27 | Low | Medium | Closed |
| DEF-28 | Medium | High | Closed |
| DEF-29 | Low | Low | Closed |
| DEF-30 | Low | Medium | Closed |
| DEF-31 | Low | Medium | Closed |
| DEF-32 | Low | Medium | Closed |
| DEF-33 | Low | Medium | Closed |

### [DEF-20] Concurrent sign-in test failed once on CI: the fake cloud server reset connections under 8 parallel requests

| Field | Value |
|---|---|
| Found by card | TC-13 / C2-UNT-01 |
| Card owner | Cel Hendrix N. San Miguel |
| Date found | 2026-11-10 |
| Severity | Low |
| Priority | Medium |
| Build | 8864f29 (CI run 42781784566, attempt 1) |
| Environment | GitHub Actions windows-latest, Python 3.14.6 |
| Status | Closed |
| Fixed in commit | 10412b9 |
| Re-test result | 2026-11-10 on PC A: the test passed 8 of 8 times while model training kept the CPU busy. 2026-11-11: offline suite 682 passed on cbe0cca. |

**Steps to reproduce**

1. Open GitHub > Actions > CI run 42781784566, attempt 1, job 'checks', step 'Unit tests'.
2. Find tests/test_cloud_app.py::test_concurrent_bearer_requests_never_swap_identities.

**Expected result**

The test passes: 20 parallel /predict calls each return an event_uuid owned by the right account.

**Actual result**

KeyError: 'event_uuid'. The fake Supabase server logged ConnectionAbortedError [WinError 10053] and ConnectionResetError [WinError 10054]: it kept Python's default backlog of 5 waiting connections, so Windows reset connections under 8 parallel requests on the slower CI runner. Passed on the re-run and on 4cf3fdd.

**Evidence:** Test Log rows TC-13; tests/fake_supabase.py (_Server, request_queue_size = 128)

No e-mail addresses, passwords or tokens are included in this card or its evidence.

### [DEF-21] evaluate_model.py silently skips feature schema v2 training files when removing rows seen in training

| Field | Value |
|---|---|
| Found by card | Algorithm review / C2-SYS-01 (TC-03) |
| Card owner | Victor Alexander A. Ong |
| Date found | 2026-11-10 |
| Severity | Medium |
| Priority | High |
| Build | 4cf3fdd |
| Environment | PC A (SANMIGUEL), Windows 11 Home 26H2 build 26300.9818, Python 3.14.6 venv; local Supabase stack in Docker where stated |
| Status | Closed |
| Fixed in commit | 7e76db2 |
| Re-test result | 2026-11-11 01:22 TC-03 on b1e647c: the run 10 training file is now compared on its 13 per-flow features: 66,641 rows removed and 108,700 evaluated (Stage 6A: 66,602 and 108,739); model 60's figures unchanged to three decimals. Three new unit tests (tests/test_evaluate_model.py) pass. |

**Steps to reproduce**

1. venv\Scripts\python.exe scripts\evaluate_model.py --model saved_models\run_10\stacking.joblib --data research\dataset\processed\flow_window_v2\holdout_eval.csv --train-csv research\dataset\processed\def19\training_local.csv
2. Read the 'training rows from' lines and 'removed as seen in training'.

**Expected result**

Every training file is used to remove the evaluation rows it contains, or the run stops with a message.

**Actual result**

A training file without sttl and dttl (every feature schema v2 file, including the deployed model's) is skipped without a message: no 'training rows from' line and 0 rows removed, although 3 hold-out rows match a training row. Published figures are unaffected: the untouched evaluation also passed the full UNSW-NB15 file.

**Evidence:** Test Log row TC-03; test-results/cycle2_qa_20261111/tc03/

No e-mail addresses, passwords or tokens are included in this card or its evidence.

### [DEF-22] Guides do not say live capture watches only IPv4 TCP and UDP, or that dropped packets exclude the Npcap driver's own drops

| Field | Value |
|---|---|
| Found by card | Algorithm review / C2-SYS-02 |
| Card owner | John Brian A. Tiopes |
| Date found | 2026-11-10 |
| Severity | Low |
| Priority | Low |
| Build | 4cf3fdd |
| Environment | PC A (SANMIGUEL), Windows 11 Home 26H2 build 26300.9818, Python 3.14.6 venv; local Supabase stack in Docker where stated |
| Status | Closed |
| Fixed in commit | cbe0cca |
| Re-test result | 2026-11-11: README (Traffic sources) and docs/ANALYST_GUIDE.md (Live interface capture) state both on cbe0cca - Pass. |

**Steps to reproduce**

1. Read services/traffic_source_service.py: BASE_BPF_FILTER = 'ip and (tcp or udp)'; packets_dropped counts only AlgoGuard's own queues.
2. Read the README's Live interface capture paragraph and the analyst guide's traffic source table.

**Expected result**

The guides say which traffic live capture analyses and what the dropped counter means.

**Actual result**

Neither says that IPv6 traffic and pings are not analysed, or that packets the Npcap driver discards are not counted, so an analyst could read 'no attacks, 0 dropped' as covering all traffic.

**Evidence:** README.md; docs/ANALYST_GUIDE.md

No e-mail addresses, passwords or tokens are included in this card or its evidence.

### [DEF-23] Werkzeug 3.1.8 has a known advisory (CVE-2026-102598): a static-file request naming a Windows device hangs

| Field | Value |
|---|---|
| Found by card | C2-SEC-01 (pip-audit) |
| Card owner | Arella Biacea B. Mirasol |
| Date found | 2026-11-11 |
| Severity | Low |
| Priority | High |
| Build | cbe0cca |
| Environment | PC A (SANMIGUEL), Windows 11 Home 26H2 build 26300.9818, Python 3.14.6 venv; local Supabase stack in Docker where stated |
| Status | Closed |
| Fixed in commit | 23fc068 |
| Re-test result | 2026-11-11 on 23fc068: pip-audit -r requirements.txt: 'No known vulnerabilities found'. |

**Steps to reproduce**

1. venv\Scripts\python.exe -m pip_audit -r requirements.txt

**Expected result**

No known vulnerabilities in the pinned runtime packages.

**Actual result**

PYSEC-2026-4312 (GHSA-g6x2-hccm-hh4m) in Werkzeug 3.1.8, fixed in 3.1.9: on Windows, safe_join accepts device names such as NUL:, so a request for one under the static folder hangs. AlgoGuard listens on 127.0.0.1 only, so only the same computer can send it. Werkzeug is not in requirements-model.lock, so published models are unaffected.

**Evidence:** test-results/suites_20261111_005819/pip-audit.log; test-results/cycle2_qa_20261111/bandit_review.md

No e-mail addresses, passwords or tokens are included in this card or its evidence.

### [DEF-24] Model 60 misses the accuracy, F1 and recall targets on the untouched UNSW-NB15 set (86.2% / 85.4% / 76.0%, target >= 90%)

| Field | Value |
|---|---|
| Found by card | TC-03 / C2-SYS-01 |
| Card owner | Victor Alexander A. Ong |
| Date found | 2026-11-11 |
| Severity | High |
| Priority | High |
| Build | b1e647c; model 60 (training run 10), SHA-256 6756c632...9604, deployment #3 |
| Environment | PC A (SANMIGUEL), Windows 11 Home 26H2 build 26300.9818, Python 3.14.6 venv; local Supabase stack in Docker where stated |
| Status | Closed (fixed by training run 13: release 0.6.1, deployment #4) |
| Fixed in commit | 9bd15a5, cb13f3f; release 0.6.1 (1fda877); model 72 = training run 13 (evidence ec65008) |
| Re-test result | Fix attempted 2026-11-10/11: training on most of the official training partition (protocol and results in test-results/model_run11/; trained as run 12) raised hold-out recall to 0.960 but failed its predeclared gate: hold-out F1 0.910 (floor 0.926), false positives 9.4% (run 10: 2.7%), 10.8% of benign flows to unseen sites labelled Attack (limit 8%). Not deployed; model 60 stays. Decision line study 2026-11-13 (test-results/cycle2_qa_20261111/def24_decision_line.py and .json; no model deployed): for model 60 no decision line meets all targets on the untouched set even when chosen on that set (best accuracy 89.4%, with 11.3% false positives); the lowest line that passes the selection rules (0.40) gives untouched accuracy 86.5%, F1 85.9%, recall 77.2%. 9,480 of the 13,973 missed attacks score below 0.10, so a line cannot recover them. Run 12 at line 0.70 would pass its gate (hold-out recall 92.8%, false positives 6.0%) but needs an app change and has no cross-file untouched data left. Stays a known limit; better features or a new predeclared protocol are future work (docs/MODEL_TRAINING.md, section 9). Second attempt started 2026-11-17 (Lead Architect decision): a check of unused measurements, weighting and decision lines (test-results/def24_check_20261117/summary.md) found that new measurements do not help, but training on both files with PC A's browsing weighted 10x, a histogram Gradient Boosting member and a lower line set inside the model meets every target on a held slice of file 2. It is now being tried as training run 13 under a predeclared protocol (design: docs/superpowers/specs/2026-11-17-def24-run13-design.md); the reserved 32,610 rows stay unread until its gate passes. Protocol committed and pushed in 3315c14 (test-results/model_run13/protocol_run13.md) on 2026-11-18, before any run 13 data existed. Training file built the same day: 167,082 rows (research/dataset/processed/run13/training.csv, manifest test-results/model_run13/run13_data_manifest.json; the reserved rows were not read). Step 1 (choosing the line) is running. Step 1 (2026-11-18, test-results/model_run13/line_choice.json): line 0.30 chosen (margin 0.013; lines 0.25 to 0.35 qualified); file-2 out-of-fold accuracy/F1/recall/FPR 0.926/0.929/0.917/0.065, AUC 0.985; worst selection recording 6.7%; file-1 hold-out FPR 13.5% (reported, not gated); Fuzzers recall 72.5%. Step 2 (training the final model) started. Step 2 (2026-11-18): training run 13 trained in 9.5 min, Stacking Ensemble model 72 with line 0.30 inside (31.5 MiB; test-results/model_run13/train_console_run13.log). Step 3 gate passed (test-results/model_run13/gate_run13.json): selection recordings 0.0/2.8/3.4/7.3% Attack (limit 8% each), artifact within 50 MiB, deployment quality gate passed, line and workflow v4 in the artifact. File-1 hold-out, reported not gated: accuracy 90.1%, F1 88.5%, recall 98.2%, FPR 15.1% (protocol expected about 11-15%), ROC-AUC 0.988. Step 4 (two fresh browsing recordings on PC A) next. Step 4 (18 Nov): the first pair of fresh recordings is not used. Started from an administrator window, Chrome ended at once and loaded no test site (0.6 and 1.5 MB of web traffic, against 51-58 MB in earlier recordings). Neither file was scored. The pair is recorded again from a normal window (test-results/model_run13/fresh_recordings_note.md). Steps 4-5 (18 Nov): second pair of fresh recordings, normal window: 4.36% and 4.12% of flows labelled Attack (model 60: 4.98% and 4.89%), passed; www.gnu.org never answers from PC A, in every generator recording. Final test, once, on the 32,610 reserved rows: accuracy 92.93%, F1 93.32%, recall 92.82%, false positives 6.94%, ROC-AUC 0.986; all five targets met, so run 13 ships as release 0.6.1. Model 60 on the same rows: recall 76.04%. Fuzzers recall 76.1% (model 60: 23.0%). Evidence ec65008. Release 0.6.1 committed (1fda877; evidence ec65008) with MODEL_TRAINING.md, README and guides; full check 774 passed, ruff and pyright clean; analyst package 0.6.1+1fda877 built (76 files). Push waits for the launcher check TC-17.W2. PC A and PC B install 0.6.1 before run 13 (model 72) is published, since a PC on 0.6.0 refuses a v4 model. Pushed on 19 Nov with the DEF-30 and DEF-31 fixes (ed4d774; CI run 44419622121 passed). PC A runs 0.6.1+ed4d774; PC B was not available and stays on 0.6.0, which refuses a v4 model, so it is updated before its next use. Model 72 (run 13) published as deployment #4, active. SMK-01.R13 (19 Nov 07:44, PC A, hosted pilot) passed: a 5,000-flow sample replay with deployment #4 pinned, 3,522 Attack (70.44%); against the sample's labels 7 of 3,403 attacks missed and 126 of 1,597 normal flows called Attack (7.9%); 300 records stored and synced, all with deployment 4; PC A reports deployment #4. TC-24 re-test (19 Nov 15:07-15:29, PC A, Wi-Fi, deployment #4) passed: 1,046 flows, 38 labelled Attack (3.63%, limit 8%; model 60 on 16 Nov: 5.77%). Final lane: on ed4d774 the browser walk found DEF-32 (Low, fixed in bc89a0c); on bc89a0c 99 tests and the clean-install check passed, the walk's own timing flaw (DEF-33) was fixed in 3fb8270, and the walk passed. With run 13 on the pilot, the rows TC-01 and TC-02 type gave Normal (Low 1.2%) and Attack (Very high 99.7%, with an alert). Closed 19 Nov: all five targets met on the final test, within the limits stated in advance (same-file test; file-1 hold-out false positives 15.1%; Fuzzers recall 76.1%). PC B (0.6.0) refuses the v4 model until it is updated to 0.6.1. |

**Steps to reproduce**

1. venv\Scripts\python.exe scripts\evaluate_model.py --model saved_models\run_10\stacking.joblib --data research\dataset\processed\official_unsw\UNSW_NB15_training-set.csv --train-csv research\dataset\raw\UNSW_NB15_training-set.csv research\dataset\processed\def19\training_local.csv
2. Read the targets table.

**Expected result**

Accuracy, F1 and recall >= 90%; false-positive rate <= 8%; ROC-AUC >= 0.95 (Master Test Plan (cycle 2) 3.3).

**Actual result**

108,700 unseen rows: accuracy 0.8615, F1 0.8537, recall 0.7600 (not met); false-positive rate 0.0232, ROC-AUC 0.9695 (met). Fuzzers caught 22.8% of the time (11,113 of the 13,869 missed attacks).

**Evidence:** Test Log row TC-03; test-results/cycle2_qa_20261111/tc03/

No e-mail addresses, passwords or tokens are included in this card or its evidence.

### [DEF-25] Sustained-load integration test misses the 5 s burst budget while the PC is busy (p95 5.07 s)

| Field | Value |
|---|---|
| Found by card | C2-INT-01 (integration lane) |
| Card owner | Afel Anthonielle V. Quirao |
| Date found | 2026-11-11 |
| Severity | Low |
| Priority | Low |
| Build | cbe0cca |
| Environment | PC A (SANMIGUEL), Windows 11 Home 26H2 build 26300.9818, Python 3.14.6 venv; local Supabase stack in Docker where stated |
| Status | Closed |
| Fixed in commit | d940168 |
| Re-test result | 2026-11-11 with the PC idle: passed twice; burst p95 1.66 s with four uploads at once (4.31 s one at a time), 0 lost, 0 duplicates, 26.3 events/s sustained. Fix d940168 (2026-11-13): the test reads the CPU use first and skips with a message when more than half is in use, instead of failing on a measurement it cannot make fairly; the maintainer guide says to run the lane on an idle PC. Ruff clean, the test collects; CI on 40d3ca6 green (run 43390305974). The integration lane is re-run on the final commit at close. |

**Steps to reproduce**

1. Start a CPU-heavy job (here: python train.py on 144,942 rows).
2. python -m pytest -m integration tests/integration/test_sustained_load.py

**Expected result**

Burst upload p95 <= 5 s with four uploads at once (DEF-10 budget).

**Actual result**

p95 5.07 s with four uploads at once, 6.11 s one at a time, against the local stack behind a 0.2 s each-way delay; everything else in the test passed.

**Evidence:** test-results/suites_20261111_005819/pytest-integration.log; test-results/cycle2_qa_20261111/sustained_load_idle.json

No e-mail addresses, passwords or tokens are included in this card or its evidence.

### [DEF-26] Expiry test failed once on CI: it accepted only one of the two stop messages a timing race can produce

| Field | Value |
|---|---|
| Found by card | TC-13 / C2-UNT-01 |
| Card owner | Cel Hendrix N. San Miguel |
| Date found | 2026-11-11 |
| Severity | Low |
| Priority | Medium |
| Build | b1e647c (CI run 42957447209) |
| Environment | GitHub Actions windows-latest, Python 3.14.6 |
| Status | Closed |
| Fixed in commit | 7fe5251 |
| Re-test result | 2026-11-11 on PC A: the test passed 10 of 10 times in a row; offline suite 683 passed on 7fe5251. CI on 7fe5251: run 43049444785. |

**Steps to reproduce**

1. Open GitHub > Actions > CI run 42957447209, job 'checks', step 'Unit tests'.
2. Find tests/test_cloud_app.py::test_expiry_without_refresh_stops_capture_and_requires_login.

**Expected result**

When a sign-in expires while the cloud is unreachable, the capture stops, the message asks for an online login, and the test passes whichever thread noticed first.

**Actual result**

AssertionError: 'expired' not in 'Your sign-in ended. Capture stopped locally; log in online to continue.' The Live Monitor loop (cloud_monitor.py) and the upload worker (cloud_sync.py) both notice the expiry; on the slower CI runner the upload worker ended the sign-in first, so the monitor reported 'sign-in ended' instead of 'expired and could not be refreshed'. Both stop the capture and ask for an online login, so the application behaved correctly; the test expected one wording. The test passed in every local run (offline suite 683 passed three times on 2026-11-11).

**Evidence:** Test Log row TC-13; tests/test_cloud_app.py

No e-mail addresses, passwords or tokens are included in this card or its evidence.

### [DEF-27] Two upload tests failed on CI by timing: the stop hand-off took over 5 s and a late back-off outlasted the reconnect wait

| Field | Value |
|---|---|
| Found by card | TC-13 / C2-UNT-01 |
| Card owner | Cel Hendrix N. San Miguel |
| Date found | 2026-11-13 |
| Severity | Low |
| Priority | Medium |
| Build | 0eca41f (CI run 43380608246, attempts 1 and 2; attempt 3 passed) |
| Environment | GitHub Actions windows-latest, Python 3.14.6 |
| Status | Closed |
| Fixed in commit | 00277ce |
| Re-test result | 2026-11-13 on PC A with the fix: both tests passed 8 of 8 times in a row; offline suite 683 passed. CI on 00277ce: run 43385566496, all checks passed (2026-11-13 15:30). The same commit 0eca41f had also passed CI on attempt 3 without the fix. |

**Steps to reproduce**

1. Open GitHub > Actions > CI run 43380608246, job 'checks', step 'Unit tests', attempts 1 and 2.
2. Find tests/test_cloud_app.py::test_csv_capture_pins_deployment_and_uploads_through_the_outbox (attempt 1) and ::test_network_loss_keeps_capturing_and_reconnect_uploads_without_duplicates (attempt 2).

**Expected result**

The tests pass whenever the application keeps its contract: every captured event reaches the local queue and, after the network returns, the cloud, once.

**Actual result**

Attempt 1: assert stopped['local_drain_complete'] is True failed. Stop waits at most 5 s for the local hand-off to the upload queue, and on the CI runner the writes were still going. Attempt 2: 'condition not reached in time' after 20 s waiting for the uploads after reconnect. The test clears the upload back-off once; an upload already on its way while offline can fail after that and set a new back-off of up to 60 s. The merge added only test result files, the same code passed CI at 0c802b1 and on attempt 3, and both tests pass locally (10 of 10 and 8 of 8). The application behaved as designed; the tests' timing was too tight for the shared runner.

**Evidence:** Test Log row TC-13; tests/test_cloud_app.py

No e-mail addresses, passwords or tokens are included in this card or its evidence.

### [DEF-28] 'Default interface' captures a VPN adapter instead of the adapter that carries the PC's traffic, so a live session shows almost nothing and gives no warning

| Field | Value |
|---|---|
| Found by card | TC-24 / C2-SYS-01 |
| Card owner | Victor Alexander A. Ong |
| Date found | 2026-11-13 |
| Severity | Medium |
| Priority | High |
| Build | 0c802b1 (PC A; application code of 7fe5251) |
| Environment | PC A, Windows 11, Wi-Fi as the internet connection plus a Radmin VPN adapter, Npcap, hosted pilot |
| Status | Closed |
| Fixed in commit | 347d83c |
| Re-test result | 2026-11-14: 'Default interface' now follows the route to the internet (scapy's route table, loaded explicitly in the web process), the list shows that adapter first and the page names it. On PC A it resolves to Wi-Fi, not the Radmin VPN adapter. Tests: 8 new in test_traffic_sources.py, one of them in a fresh Python process; offline suite 716 passed; CI on 70eb19f green (run 43597164049). 2026-11-16 Pass on 0.6.0+eb3e9ed (PC A, started from ALGOGUARD.cmd): the first option read 'Default interface (Intel(R) Wi-Fi 6 AX203 ...)' and was left selected; the pilot log says 'Live monitoring started on Live capture - Wi-Fi'; capture 62 (15:28-15:51) saw 33,401 packets, 0 dropped, and 2,704 flows of ordinary browsing (7 flows in 46 minutes before the fix). |

**Steps to reproduce**

1. On a PC with a VPN adapter installed and Wi-Fi as its internet connection, open Live Monitor.
2. Choose Live interface capture, leave Network interface on 'Default interface', storage Save every flow, and press Start Monitoring.
3. Browse normally for 30 minutes.

**Expected result**

The capture watches the adapter that carries the PC's internet traffic, or says clearly that the chosen adapter is silent.

**Actual result**

Capture 61 ran 46 minutes (17:01-17:47) and saw 21 packets and 7 flows, all local discovery UDP (ports 138, 1900, 5353, 5355), none of the browsing. The log reads 'Live monitoring started on Live capture - default interface'. 'Default interface' is passed on as no interface, and the capture library's default on this PC is the Radmin VPN adapter, while the route to the internet uses the Wi-Fi adapter. The page showed a running session with no warning. TC-24 (benign false alarms, DEF-19) and TC-11 (live lag, DEF-17) could not be measured from this session.

**Evidence:** Test Log row TC-24; test-results/cycle2_qa_20261111/release_check/algoguard_TC-24_PC-A_2026-11-13T09-45-47-974Z.json; pilot capture 61

No e-mail addresses, passwords or tokens are included in this card or its evidence.

### [DEF-29] CI lint failed on 8307519: the new DEF-10 diagnosis scripts had lines over 100 characters and an unused import

| Field | Value |
|---|---|
| Found by card | TC-13 / C2-UNT-01 |
| Card owner | Cel Hendrix N. San Miguel |
| Date found | 2026-11-16 |
| Severity | Low |
| Priority | Low |
| Build | 8307519 (CI run 43838213629) |
| Environment | GitHub Actions windows-latest, Python 3.14.6 |
| Status | Closed |
| Fixed in commit | 0e8f2e2 |
| Re-test result | 2026-11-16 on PC A: ruff check . 'All checks passed!'; the two pilot scripts still run. CI on 0e8f2e2: run 43838701008 passed. |

**Steps to reproduce**

1. Open GitHub > Actions > CI run 43838213629, job 'checks', step 'Lint'.
2. Or run venv\Scripts\python.exe -m ruff check . on 8307519.

**Expected result**

Lint passes on every commit pushed to main.

**Actual result**

Found 6 errors, all in test-results/cycle2_qa_20261111/def10_diagnosis/: five lines over 100 characters (E501) in def10_harness.py, def10_server_time.py and def10_timeline.py, and an unused import (F401) in def10_harness.py. The scripts were copied from the scratch folder as evidence and only the changed application files were linted before the push, not the whole repository. Application code and tests were unaffected; the other CI steps passed.

**Evidence:** Test Log row TC-13; GitHub Actions runs 43838213629 and 43838701008

No e-mail addresses, passwords or tokens are included in this card or its evidence.

### [DEF-30] ALGOGUARD.cmd and the guides promise no administrator prompt, but Windows asked to allow the Python installer on a fresh Windows account

| Field | Value |
|---|---|
| Found by card | TC-17.W2 / C2-USE-01 |
| Card owner | Brynt M. Timtiman |
| Date found | 2026-11-19 |
| Severity | Low |
| Priority | Medium |
| Build | 0.6.1+1fda877 (package, PC A) |
| Environment | PC A, Windows 11 Home (build 26300), a temporary local Windows account with no Python of its own (deleted after the test); Npcap installed for the whole PC; hosted pilot settings |
| Status | Closed |
| Fixed in commit | ed4d774 |
| Re-test result | 2026-11-19 on ed4d774: ALGOGUARD.cmd ('If Windows asks you to allow the installer, allow it.'), the analyst guide (section 2), the user manual and the maintainer guide (section 6) say that Windows may ask to allow the Python installer; no user-facing text says that no administrator rights are needed. The cause of the prompt is not known: the maintainer guide asks to keep Python's installer logs on the next fresh-account check. Offline suite 778 passed. CI on ed4d774: run 44419622121 passed. |

**Steps to reproduce**

1. On PC A, sign in to a new local Windows account that has no Python of its own.
2. Unzip AlgoGuard-analyst-0.6.1+1fda877.zip in that account and double-click ALGOGUARD.cmd.
3. Press Y at 'Install Python 3.14.6 and set up AlgoGuard now? [Y/N]'.

**Expected result**

Python 3.14.6 installs for that account only and without an administrator prompt, as ALGOGUARD.cmd ('No administrator rights are needed.'), the analyst guide (section 2) and the user manual state.

**Actual result**

Windows asked by itself to allow the installer; the tester allowed it. Python 3.14.6 was then installed for that account only: Windows' Application log shows its six packages (Core Interpreter, Executables, Development Libraries, Standard Library, Tcl/Tk Support, pip Bootstrap) installed by the test account at 05:35-05:37 from that account's own package cache. Nothing was installed for the whole computer: no Python under Program Files, no machine-wide Python registration, no new machine-wide package cache. The options used (InstallAllUsers=0, Include_launcher=0) ask for no elevation in Python's installer, which elevates only for an all-users install or an all-users launcher, so the cause is not known. The installer's own log was in the account's TEMP folder and was deleted with the account.

**Evidence:** Test Log row TC-17.W2; Lead Architect's report; Windows Application log (MsiInstaller events, 2026-11-19 05:35-05:37)

No e-mail addresses, passwords or tokens are included in this card or its evidence.

### [DEF-31] When no browser opens, the AlgoGuard window does not say which address to open, so on a fresh Windows account the sign-in page was never reached

| Field | Value |
|---|---|
| Found by card | TC-17.W2 / C2-USE-01 |
| Card owner | Brynt M. Timtiman |
| Date found | 2026-11-19 |
| Severity | Low |
| Priority | Medium |
| Build | 0.6.1+1fda877 (package, PC A) |
| Environment | PC A, Windows 11 Home (build 26300), a temporary local Windows account whose browser had never been set up (deleted after the test); hosted pilot settings |
| Status | Closed |
| Fixed in commit | ed4d774 |
| Re-test result | 2026-11-19 on ed4d774: 4 new tests in tests/test_launcher.py pass (the window names the address when AlgoGuard is ready and for Open AlgoGuard); offline suite 778 passed. CI on ed4d774: run 44419622121 passed. On PC A (project folder, 0.6.1+ed4d774) the window said 'AlgoGuard is ready. Opening your browser... If its page does not appear, open your browser yourself and go to http://127.0.0.1:5000'. The analyst guide (sections 3 and 9) and the user manual give the same step. The run on a fresh account was not repeated: message text only, and the test account was deleted. |

**Steps to reproduce**

1. On a Windows account whose browser has never been set up, finish the AlgoGuard window's first run: Set up, then Settings.
2. Press Start and wait until AlgoGuard runs.

**Expected result**

The browser shows AlgoGuard's sign-in page, or the window says which address to open.

**Actual result**

AlgoGuard started, but no browser page appeared: the account had no browser set up yet, so Windows opened nothing. The window said only 'AlgoGuard is ready. Opening your browser...'. The address was on the window's AlgoGuard line, but nothing said to open it by hand. The sign-in page was not reached on that account, which was deleted after the test.

**Evidence:** Test Log row TC-17.W2; Lead Architect's report

No e-mail addresses, passwords or tokens are included in this card or its evidence.

### [DEF-32] A very long signed-in name makes every administrator page 3 px wider than a 1366 px screen, so the page scrolls sideways

| Field | Value |
|---|---|
| Found by card | TC-12 / C2-USE-01 |
| Card owner | Brynt M. Timtiman |
| Date found | 2026-11-19 |
| Severity | Low |
| Priority | Medium |
| Build | ed4d774 (local stack, browser walk) |
| Environment | PC A, Windows 11 Home (build 26300), Microsoft Edge 157.0.4341.60 headless through Playwright 1.65.2; local Supabase stack; the walk's generated administrator account |
| Status | Closed |
| Fixed in commit | bc89a0c |
| Re-test result | 2026-11-19: the name ends in '...' after 14rem with the full name on hover; a new test in tests/test_ui.py passes; offline suite 778 passed (1 skipped, the launcher window's Tk start-up, which passes on its own); CI on bc89a0c passed (run 44513081858). The browser walk with the long generated names passed on the fix (18:52) and on 3fb8270 (19:46): 65 page views, none wider than the screen; inside the bc89a0c lane none was wider either. |

**Steps to reproduce**

1. Sign in as an administrator whose name is long, for example the walk's 38-character 'zz_migration_smoke_1eb71debb5_ui_admin'.
2. Open any page at 1366 px wide (pytest -m integration tests/integration/test_ui_walk.py with RUN_UI_WALK=1).

**Expected result**

No page is wider than the screen (TC-12).

**Actual result**

13 administrator pages at 1366 px were 3 px wider than the screen: the menu bar (div.navbar-collapse, 1180 px) holds ten links and the full name, with nothing to spare. Whether it overflows depends on the letters of the name: the same walk passed on 17 Nov and in an earlier run on 19 Nov with other generated names, and failed with this one. Short real names fit. The first attempt on ed4d774 ended with request errors only because PC A went to sleep during the walk (15:47 to 18:40); that is not this defect.

**Evidence:** Test Log row TC-12; test-results/cycle2_qa_20261111/release_check/ui_walk_ed4d774_def32.json (13 pages, 3 px, the menu bar) and integration_ed4d774.txt

No e-mail addresses, passwords or tokens are included in this card or its evidence.

### [DEF-33] The browser walk presses Start while the previous capture is still releasing its resources, and counts AlgoGuard's designed refusal as a failure

| Field | Value |
|---|---|
| Found by card | TC-12 / C2-USE-01 |
| Card owner | Brynt M. Timtiman |
| Date found | 2026-11-19 |
| Severity | Low |
| Priority | Medium |
| Build | bc89a0c (local stack, browser walk inside the integration lane) |
| Environment | PC A, Windows 11 Home (build 26300), Microsoft Edge 157.0.4341.60 headless through Playwright 1.65.2; local Supabase stack; a slow run (the lane took 17 minutes, usually 10) |
| Status | Closed |
| Fixed in commit | 3fb8270 (test only) |
| Re-test result | 2026-11-19: three new offline tests (tests/test_ui_walk_driver.py, a stand-in page) pass: the designed refusal is retried and set aside; any other refusal, or one that outlasts 30 s, stays a problem. The walk passed on 3fb8270 with every source started from an accepted Start (PCAP 11 flows on each width). CI on 3fb8270 passed (run 44517408622). |

**Steps to reproduce**

1. Run the integration lane with RUN_UI_WALK=1 on a busy PC, so that the 12-second sample replay does not finish and the walk presses Stop.
2. The walk presses Start for the next source as soon as the page reads 'stopped'.

**Expected result**

The walk starts each traffic source and counts only real faults.

**Actual result**

On the desktop pass the replay was stopped after 13 s; Start for the PCAP source came while the previous capture was still releasing its resources, so AlgoGuard answered 409 'The previous capture is still releasing its resources. Try again shortly.' as designed (test_model_recovery waits for this). The walk counted the 409 and its console line as two problems and failed. It also took the PCAP source as run: the page still showed the replay's 'stopped' state and its 50 feed rows. AlgoGuard behaved as designed; the fault is in the walk.

**Evidence:** Test Log row TC-12; test-results/cycle2_qa_20261111/release_check/ui_walk_bc89a0c_def33.json and integration_bc89a0c.txt

No e-mail addresses, passwords or tokens are included in this card or its evidence.
