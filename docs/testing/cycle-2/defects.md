# AlgoGuard test cycle 2 — Defect reports (C2-LOG-01)

Each defect is a DEF card on the [Testing Kanban](https://github.com/users/bry22191919-hash/projects/4) and a row in the Defects sheet of the Test Log. New cycle 2 defects are numbered from **DEF-20**. Their full text goes below, in the same layout as `docs/testing/cycle-1/defects.md`: found by, card owner, date, severity, priority, build, environment, status, steps to reproduce, expected and actual results, and evidence. No e-mail address, password, token or IP address of real traffic is written here.

Severity follows the Master Test Plan (cycle 2), Incident Report section: **Critical** (data crosses between nodes, an unverified model loads, credentials are exposed, or saved results are lost), **High** (a feature is unusable, known rows get the wrong verdict, or a detection target is missed), **Medium** (a workaround is needed, or a performance budget is missed), **Low** (cosmetic, wording or layout).

## Carried over from cycle 1 for re-test

Their full reports are in `docs/testing/cycle-1/defects.md` and on their cards. Cycle 2 re-tests them on the hosted pilot (C2-REG-01).

| ID | Severity | Fixed in | Re-test in cycle 2 | Closed when |
|---|---|---|---|---|
| DEF-10 Upload delay (pilot limit L2) | Medium | Stage 6C, `d69cf63`: up to four upload requests at once | TC-23 (C2-PRF-01) | Upload delay p95 ≤ 5 s for every capture, on both PCs and on both at once |
| DEF-17 Live lag p95 above 500 ms (closed in cycle 1; slower again under load in Stage 6A) | Medium | Stage 6A addendum, `5b5a36a`, `5567c4e`: capture and scoring in separate processes | TC-11 (C2-PRF-01) | p95 ≤ 500 ms for FIN/RST flows on both PCs |
| DEF-19 Benign live traffic labelled Attack | High | Stage 6A, `6cb88e6` and model 60 (training run 10), published to the pilot on 2026-11-04 as deployment #3 | TC-24 (C2-SYS-01) | ≤ 8% of benign live flows labelled Attack on both PCs |

## Cycle 2 defects

Raised during the cycle 2 QA pass (2026-11-10 to 2026-11-12). Text as on each DEF card.

| ID | Severity | Priority | Status |
|---|---|---|---|
| DEF-20 | Low | Medium | Closed |
| DEF-21 | Medium | High | Closed |
| DEF-22 | Low | Low | Closed |
| DEF-23 | Low | High | Closed |
| DEF-24 | High | High | Open - known limit; AlgoGuard stays a limited research pilot (Lead Architect decision, 2026-11-10) |
| DEF-25 | Low | Low | Open - workaround: run the integration lane on an idle PC |
| DEF-26 | Low | Medium | Closed |

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
| Status | Open - known limit; AlgoGuard stays a limited research pilot (Lead Architect decision, 2026-11-10) |
| Fixed in commit | - |
| Re-test result | Fix attempted 2026-11-10/11: training on most of the official training partition (protocol and results in test-results/model_run11/; trained as run 12) raised hold-out recall to 0.960 but failed its predeclared gate: hold-out F1 0.910 (floor 0.926), false positives 9.4% (run 10: 2.7%), 10.8% of benign flows to unseen sites labelled Attack (limit 8%). Not deployed; model 60 stays. |

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
| Status | Open - workaround: run the integration lane on an idle PC |
| Fixed in commit | - |
| Re-test result | 2026-11-11 with the PC idle: passed twice; burst p95 1.66 s with four uploads at once (4.31 s one at a time), 0 lost, 0 duplicates, 26.3 events/s sustained. |

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
