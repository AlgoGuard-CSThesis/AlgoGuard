# AlgoGuard

---
Kanban Board
https://github.com/users/pqauoz/projects/5/views/1
---
Testing Kanban Board
https://github.com/users/bry22191919-hash/projects/4/views/2

AlgoGuard detects anomalies in network traffic. Analysts run it on their own Windows computers: it classifies each network flow as Normal or Attack with a deployed Stacking Ensemble, and stores detections in a shared cloud project (Supabase) where administrators manage accounts, approve computers and review every installation's records. Models are trained and published by a maintainer; analyst installations only download and verify them.

The Live Monitor accepts three traffic sources through one pipeline: **live packet capture** from a network interface (packets are grouped into flows in real time and each flow is classified when it ends), **recorded packet captures** (`.pcap` files replayed through the same flow tracker), and **labelled CSV replay** of bundled UNSW-NB15 samples, which keeps ground-truth labels visible for demonstrations. Live capture needs the Npcap driver and administrator rights on Windows; without them both replay sources keep working. AlgoGuard observes and classifies traffic; it never blocks it, and it stores flow metadata only, never packet payloads. Capture live traffic only on networks you are authorized to monitor.

## Which guide to read

| You are | Read |
|---|---|
| An analyst installing or using AlgoGuard, or an administrator managing accounts and computers | [docs/ANALYST_GUIDE.md](docs/ANALYST_GUIDE.md) |
| The maintainer: cloud project, migrations, first administrator, training and publishing models, analyst packages, backups, cutover, recovery, tests | [docs/MAINTAINER_GUIDE.md](docs/MAINTAINER_GUIDE.md) |
| Reviewing how and why it was built | [AlgoGuard Iterations (plan).md](<AlgoGuard Iterations (plan).md>), [the backlog](<AlgoGuard Migration (backlog).md>) and the stage records in [docs/migration/](docs/migration/) |

**Analyst quick start.** Install Python 3.14.6, unzip the analyst package (or clone this repository), run `SETUP_ALGOGUARD.cmd` and enter the project URL and publishable key from your administrator, then run `START_ALGOGUARD.cmd` (as administrator for live capture), open `http://127.0.0.1:5000` and sign in. An administrator approves the computer once on the Nodes page.

## Runtime Architecture

| Part | Where it runs | Purpose |
|---|---|---|
| Web console (`app.py` → `cloud_app.py`) | Each analyst computer, on `127.0.0.1` only | Sign-in, Dashboard, Live Monitor, Manual Prediction, Alerts, Reports, Logs, Nodes; for administrators also Accounts, Computers and Retention. |
| Capture and scoring processes (`services/capture_worker.py`, `services/inference_worker.py`) | Each analyst computer | Packet capture and model scoring in separate processes, so neither waits behind the other or the web pages. |
| Local queue (`cloud_outbox.py`, `cloud_sync.py`) | Each analyst computer, in `.algoguard/` | Saves each stored detection on disk first, then uploads it in the background; records survive restarts and outages. |
| Model delivery (`model_delivery.py`, `model_runtime.py`) | Each analyst computer | Downloads the active published model, checks its hash and runtime, and pins it for each capture. |
| Cloud project (`supabase/`) | Supabase | Auth (online e-mail/password sign-in), the database with row-level security per user and installation, the private model store and the account-administration function. |
| Maintainer tools (`train.py`, `publish_model.py`, `cloud_migrate.py`, `bootstrap_admin.py`, `cloud_recovery.py`, `legacy_*.py`, `scripts/`) | The maintainer's computer only | Training, publishing, schema changes, the first administrator, backups, cutover and analyst packages. Never part of the analyst package. |

`train.py` trains these exact candidates:

1. Random Forest
2. Gradient Boosting
3. AdaBoost
4. K-Nearest Neighbors
5. Naive Bayes
6. Stacking Ensemble using all five base estimators and Logistic Regression as the final estimator

Each individual model and the Stacking Ensemble receive fresh, unfitted estimator instances. Stacking uses all five model families as base learners and Logistic Regression as its final estimator. All evaluations use the same stratified train/test split. A Scikit-learn pipeline fits missing-value handling, scaling, and categorical encoding only on training data, then saves preprocessing and classification together in the Joblib artifact.

## Ranking

Higher is better for Accuracy, Precision, Recall, F1-score, and ROC-AUC. Lower is better for False Positive Rate, process CPU time, peak process RAM increase, and complete artifact size.

AlgoGuard min-max normalizes all nine metrics across the five valid individual models. The overall score is their equal arithmetic average. Invalid or incomplete individual results are excluded. Ties are resolved by F1, Recall, ROC-AUC, FPR, RAM, model size, then model name.

This ranking is for research comparison only and never chooses the deployed model. Stacking is evaluated separately on raw classification metrics and must pass the deployment quality gate.

## Main Workflow

On the maintainer's computer, when the model needs to be rebuilt:

```text
python train.py <csv> --deploy
  -> validation and independent training-run record (local SQLite training store)
  -> stratified split, five leakage-safe individual pipelines, comparison ranking
  -> Stacking training, evaluation and quality gate
  -> Stacking becomes the maintainer's local active model
python publish_model.py --model-id <id> --publication-id <uuid> --yes
  -> runtime and feature checks, immutable upload, hash verification
  -> the single active model of the cloud project
```

On each analyst computer, every day:

```text
START_ALGOGUARD.cmd  (python app.py)
  -> online sign-in; this installation approved by an administrator
  -> the active model is downloaded once, verified and cached
  -> Live Monitor or Manual Prediction classifies flows: Normal / Attack,
     confidence, attack score and latency
  -> stored detections are saved on this computer, then uploaded;
     an Attack becomes an alert in the cloud
```

## Project Structure

```text
AlgoGuard/
|-- app.py                     web console entry point (cloud mode by default)
|-- cloud_*.py                 cloud application, sign-in, local queue, uploads, recovery
|-- model_delivery.py          verified model download and cache
|-- config.py                  settings (.env), checked with `python config.py`
|-- SETUP_ALGOGUARD.cmd        analyst setup: packages and .env
|-- START_ALGOGUARD.cmd        analyst start
|-- train.py, publish_model.py, cloud_migrate.py, bootstrap_admin.py,
|   cleanup_model_objects.py, legacy_backup.py, legacy_import.py   maintainer tools
|-- services/                  flow tracking, capture, scoring, legacy local services
|-- templates/, static/        pages and styles (Bootstrap vendored; works offline)
|-- datasets/                  bundled UNSW-NB15 replay samples
|-- captures/                  your .pcap files for replay (not in git)
|-- supabase/                  migrations, account function, local stack config
|-- scripts/                   dataset building, evaluation, capture checks, packaging
|-- docs/                      guides, stage records (migration/), test cycles (testing/)
|-- tests/                     offline suite; tests/integration/ needs the local stack
|-- research/                  archived notebook workspace (not used by the app)
`-- database/, saved_models/, reports/   maintainer training records (not in git)
```

`research/` is an archived notebook workspace and is not imported by the application. `ALGOGUARD_USER_MANUAL.txt` is the older end-user and demonstration guide: its explanations of pages, flow fields and test values still apply, while installation and sign-in now follow the analyst guide. `uploads/` is kept for historical runtime files and is no longer written to.

## Installation

Analysts follow [the analyst guide](docs/ANALYST_GUIDE.md): Python 3.14.6, `SETUP_ALGOGUARD.cmd`, the two cloud settings, `START_ALGOGUARD.cmd`. Maintainers clone the repository and install the development and maintainer requirements as in [the maintainer guide](docs/MAINTAINER_GUIDE.md):

```bat
git clone https://github.com/AlgoGuard-CSThesis/AlgoGuard.git
cd AlgoGuard
py -3.14 -m venv venv
venv\Scripts\python.exe -m pip install -r requirements-dev.txt -r requirements-maintainer.txt
```

Use the Python version recorded in `requirements-model.lock` (3.14.6): published models record their exact runtime, and loading refuses a mismatch. The web interface is self-contained (Bootstrap and its icons are vendored under `static/vendor/`).

`python config.py` checks the settings without starting anything. `python app.py` serves the cloud application when `.env` holds `NEXT_PUBLIC_SUPABASE_URL` and `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY`; without them it refuses to start and says how to set it up. AlgoGuard listens on `127.0.0.1` with the debugger off; cloud mode refuses any other host.

## Cloud Mode

Only the project URL and publishable key belong in an analyst `.env`; a secret or service key, a non-HTTPS remote URL, or a non-loopback `ALGOGUARD_HOST` is refused at startup. Maintainer credentials live in `.env.maintainer` and are read only by maintainer tools.

- **Sign-in is online, every start.** Log in with the e-mail and password an Administrator created. Tokens live in process memory and are renewed in the background; the browser cookie is a random session ID. A cached model never permits offline sign-in.
- **Installation approval.** The first sign-in enrolls this installation (node). An Administrator approves it on the Nodes page; until then monitoring and uploads are refused with an explanation.
- **Models come from the maintainer.** The verified active model is downloaded into the local cache and pinned for each capture. Analysts never train.
- **Local state** lives in `.algoguard/` (`ALGOGUARD_STATE_DIR`): the installation ID (`node-id`), the owner-restricted upload queue, and the model cache.

Stored detections are queued locally and uploaded in the background, up to four batches at a time, so a slow or lost connection never stalls classification. Each record reports one of `in_memory` (not yet saved), `durable_pending` (saved on this computer), `synced` (committed in the cloud), `dropped` (not stored: session cap, full queue, or local capacity), or `rejected` (refused by the cloud; kept for export). Only `synced` means the cloud has the record. Pending records survive restart, upload only under the account that produced them, expire after seven days, and can be downloaded from the dashboard's **Export pending records**. The analyst guide explains these states and their limits in plain terms.

**API in cloud mode.** `POST /predict` returns `event_uuid` and `persistence`; `prediction_id` and `alert_id` are `null` until the upload is acknowledged. Poll `GET /api/events/<event_uuid>` for `synced: true` and the database IDs. JSON endpoints (`/predict`, `/api/events/...`, `/monitor/...`, `/outbox/export`, `/api/reports/...`) also accept `Authorization: Bearer <access token>` without a cookie or CSRF token; HTML pages accept only the browser session. See `docs/migration/05d-integration.md`.

## Legacy Local Mode

Before the cloud migration AlgoGuard kept everything in a local SQLite database. That mode remains for installations that have not been moved to the cloud yet, for the maintainer's training records, and for the test suite, but it is no longer what `app.py` starts by default. It runs only with `ALGOGUARD_DB_MODE=sqlite` set in `.env` or the environment, and prints a notice at startup. In this mode:

- `python train.py datasets\algoguard_big.csv --deploy` must run once on a new database, since a fresh clone has no model; it creates `database/algoguard.sqlite3` and prints a one-time random password for the `admin` account (set `ALGOGUARD_ADMIN_USERNAME` and `ALGOGUARD_ADMIN_PASSWORD` before the first command to choose them).
- `python migrate.py` applies the additive SQLite migrations; `app.py` also applies them at startup. Do not delete the database to upgrade.
- Administrators create Administrator or Analyst accounts on the Admins page. New passwords need 12 to 128 characters with upper- and lower-case letters, a digit, and a symbol. Sign-in pauses for 15 minutes after 5 failures for one account or 20 from one address.
- `ALGOGUARD_SECRET_KEY` sets a persistent session-signing key; without it, sessions end when the process restarts. `ALGOGUARD_SECURE_COOKIES=1` marks the session cookie `Secure` (only behind HTTPS). `ALGOGUARD_HOST=0.0.0.0` exposes the console to other machines; only do that on a network you control.

Moving such an installation to the cloud is a maintainer task: [the maintainer guide](docs/MAINTAINER_GUIDE.md) section 8 and [the cutover procedure](docs/migration/05e-cutover.md). Cloud records cannot be converted back into a local database.

## CSV Contract

This contract applies to datasets passed to `train.py` and to any CSV replayed by the Live Monitor.
Replay also accepts fewer rows or a single recognized class, since it does not train a model.

- Use one `.csv` file per run.
- Include a header row.
- Put all input features before the target.
- Put the target label in the final column.
- Use exactly two target classes representing Normal and Attack.
- Prefer `Normal` / `Attack` or `0` / `1`. Exactly one class must be a recognized
  Normal label (`normal`, `benign`, `legitimate`, `clean`, `0`, or `0.0`, ignoring
  case and surrounding whitespace); the other class represents Attack. Ambiguous
  labels are rejected. Rename them explicitly rather than relying on alphabetical order.
- Include at least three rows per class; realistic evaluation requires substantially more.
- Numeric and text features are accepted. Missing feature values are imputed.
- Target values cannot be missing.

Example:

```csv
dur,proto,service,state,spkts,dpkts,sbytes,dbytes,rate,sload,dload,sinpkt,dinpkt,ct_dst_src_ltm,ct_dst_ltm,ct_srv_dst,label
0.017994,tcp,-,FIN,46,46,2870,24004,5057.241402,1248416.125,10440369.0,0.389267,0.396864,2,2,5,Normal
0.842104,tcp,http,FIN,10,6,508,268,17.812526,4351.006348,2128.003174,87.584222,154.941,2,2,2,Normal
1.1e-05,udp,-,INT,2,0,320,0,90909.0902,116363632.0,0.0,0.011,0.0,1,1,1,Attack
8e-06,pipe,-,INT,2,0,200,0,125000.0003,100000000.0,0.0,0.008,0.0,5,2,5,Attack
```

Each row is one traffic-flow observation. Each feature column is one measured property. The final `label` is the known class used during supervised training. Adding a row labeled `Attack` does not force future inputs with those exact values to be attacks; it gives the models another labeled example from which to learn.

## Included Dataset Files

Three CSV files are included in `datasets/`:

- `algoguard_big.csv`: 5,000 rows
- `algoguard_bigger.csv`: 10,000 rows
- `algoguard_biggest.csv`: 20,000 rows

The files are nested, stratified, non-replacement samples of the labeled UNSW-NB15 training partition, drawn from the rows outside the evaluation hold-out (see Retrain and Deploy). Each file uses the same 16 input features: the 13 per-flow features `dur`, `proto`, `service`, `state`, `spkts`, `dpkts`, `sbytes`, `dbytes`, `rate`, `sload`, `dload`, `sinpkt`, and `dinpkt`, and three connection counts: `ct_dst_src_ltm`, `ct_dst_ltm`, and `ct_srv_dst` (of the last 100 connections, how many had the same source and destination address, the same destination address, and the same service and destination address). The final binary `label` uses `Normal` and `Attack`. Other source columns, including the identifier and `attack_cat`, were removed to keep training practical and prevent identifier noise or direct target leakage.

These files serve two purposes: they are the replay sources offered by the Live Monitor, and they are valid inputs to `train.py` when the model is rebuilt. Because each row keeps its true `label`, the Live Monitor can show whether each prediction matched the recorded outcome.

Dataset source and attribution: [The UNSW-NB15 Dataset, UNSW Research](https://research.unsw.edu.au/projects/unsw-nb15-dataset). Academic work using these files should cite the dataset publications listed by UNSW.

## Live Monitor

Open **Live Monitor** and choose a traffic source:

- **Sample replay (labelled flows)**: one of the bundled CSV samples, with replay speed (slow, normal, fast, or maximum) and flow order (sequential or randomised). Maximum adds no delay: flows are classified as fast as the model allows, in batches. Each verdict is compared against the recorded label.
- **Recorded packets (PCAP)**: a `.pcap`, `.pcapng`, or `.cap` file placed in `captures/`. The data packets are aggregated into bidirectional flows by the flow tracker, and the resulting flows are replayed at the chosen speed. Packet-derived flows have no ground-truth label, so the Actual column shows a dash.
- **Live interface capture**: a network interface, sniffed in real time. Packets are tracked into flows as they arrive; a flow is classified when its TCP connection tears down, after 15 seconds of silence, or in 120-second slices for long-lived connections. Live mode runs at wire pace (speed and order do not apply) and reports detection lag — the time from a flow ending to its verdict — alongside inference latency, plus captured and dropped packet counters. Each live session is recorded in the `capture_session` table.

For packet sources the flow tracker computes the same 16 features the model was trained on: the 13 per-flow features (`dur`, `proto`, `service`, `state`, `spkts`, `dpkts`, `sbytes`, `dbytes`, `rate`, `sload`, `dload`, `sinpkt`, `dinpkt`) and the three connection counts, taken over the last 100 flows the tracker finished. Only destination-keyed counts are used: on one capturing PC almost every connection has the same source address. UNSW-NB15's TTL fields (`sttl`, `dttl`) are not used: in the dataset they mostly identify the lab's own hosts, and Windows' default TTL of 128 never occurs there, so models trained with them flagged most benign live traffic. Flows that end together, such as the connections of one page load, are classified together in one model call. `service` is inferred from well-known ports (except HTTPS, reported as `-` because UNSW-NB15's `ssl` rows are all attacks) and `state` from observed TCP flags (pings report `ECO`; a closed connection is held 50 ms so packets still in flight are counted), which approximate how the original UNSW-NB15 features were generated. Byte counts are IP-layer and the loads leave out the first packet, as in UNSW-NB15, and a TCP packet larger than the 1,500-byte MTU counts as the segments that crossed the wire (Windows captures before the network card splits or after it merges them); the closer your training data is to your own network's traffic, the better the verdicts (see Retrain and Deploy).

CSV replay uses the same Normal/Attack label mapping as training, including numeric
labels and recognized Normal aliases. Missing or ambiguous labels report an error.
Missing feature values and invalid numeric values are imputed by the saved pipeline;
stored records preserve those missing values as `null`.

If live capture fails after starting, the monitor reports an error and closes the
capture. A session becomes completed, stopped, or failed only after cleanup and
final database writes finish, so it can be restarted as soon as that state appears.

Packet monitoring checks the deployed model's required fields before opening a
capture. If any field is outside these 16 features, it reports the missing fields
and asks you to train with compatible data. Manual prediction remains available
for models trained with custom fields.

Then choose a storage mode and Start; Pause, Resume, and Stop control the session. The page polls for new classifications, so reloading the browser re-attaches to a session that is still running rather than orphaning it.

Each feed row shows the verdict, its confidence and the **attack score**: the model's probability that the flow is an attack, as a band (Low below 50%, Medium from 50%, High from 70%, Very high from 90%) and a percentage. The band travels with the classification, so rows the session did not save show it too. See Accuracy, Scores and Their Limits before reading a score as a measured likelihood.

The feed reconnects automatically after temporary connection failures. Requests
time out after ten seconds; if a control response is lost, the page checks the
session status without repeating the action. An expired login prompts you to log in again.

**Live capture prerequisites (Windows):** install [Npcap](https://npcap.com) with its defaults ("WinPcap API-compatible mode"). If Npcap was installed for administrators only, run AlgoGuard with Administrator rights. Without them, the monitor lists live capture as unavailable with the reason, and replay sources continue to work.

AlgoGuard excludes its own web traffic from capture. Without this, monitoring the interface the application serves from would classify the browser's own status polling and the feed would fill with AlgoGuard watching itself. The exclusion covers the port from `ALGOGUARD_PORT` and is applied both in the capture filter and per packet, so it still holds when a driver cannot compile the filter. Packets skipped this way are reported as "own traffic" beside the captured-packet count.

Storage modes bound how much the session stores (in cloud mode, through the local upload queue):

- **Session only** stores nothing; the feed is live output alone.
- **Save attacks** (default) stores only attack flows, each with a prediction and an alert.
- **Save every flow** stores every classification.

Persisting sessions stop storing after 300 records and mark themselves capped; classification continues. Stored flows are tagged `live_monitor` (replay sources) or `live_capture` (live interface) in `network_traffic`, so monitoring traffic stays distinguishable from manual predictions. Flows from packet sources store their real endpoint addresses and ports; CSV replay rows keep cosmetic private-range endpoints, since the samples carry no addresses.

One monitoring session runs per process. The artifact is loaded once when a session starts, so a redeployment mid-session is picked up the next time the monitor is started. Open monitor tabs track session IDs and reset their feeds when another tab starts a new session.

## Retrain and Deploy

Training is a maintainer task in a terminal, not a web page, and its records stay in the maintainer's local SQLite training store; publishing a trained model to the cloud is described in the maintainer guide:

```powershell
python train.py datasets\algoguard_big.csv
python train.py datasets\algoguard_big.csv --deploy
python train.py my_traffic.csv --deploy --admin your_admin
```

The last column is the label; the other columns are the features, except that `id`
and `attack_cat` are always dropped (a row number and the attack name, which would
leak the label). A file that has all 16 flow features, such as the raw UNSW-NB15
export, trains on exactly those 16. When a file's features are not the 16 flow
features, `train.py` says so before training and again before `--deploy`: such a
model still works for Manual Prediction and CSV replay with the same columns, but
Live Monitor packet capture and cloud publishing refuse it.

The deployed model is trained on the UNSW-NB15 rows outside an evaluation hold-out (the rows listed in
`research/dataset/holdout_ids.csv`) plus benign flows recorded on a Windows PC, and is measured on that
hold-out with `scripts/evaluate_model.py`. To rebuild the UNSW-NB15 part (about 20 minutes):

```powershell
python scripts\build_flow_datasets.py --evidence test-results\<folder>
python train.py research\dataset\processed\flow_window_v2\training.csv --deploy
python scripts\evaluate_model.py --model saved_models\run_<id>\stacking.joblib --data research\dataset\processed\flow_window_v2\holdout_eval.csv --train-csv research\dataset\processed\flow_window_v2\training.csv
```

The first command also rewrites the three bundled samples from the same data. To add benign traffic from your own network, record captures and append their flows as Normal rows with `test-results/iter6_6a/build_def19_training.py`, then train on the combined file. A run that was evaluated before deployment is deployed as it is, without training again, with `python train.py --deploy-run <run id>`. Saved models
are compressed (about 12 MB for this Stacking Ensemble, under the cloud storage limit).

Without `--deploy`, the run trains and ranks all six candidates, writes `reports/training_run_<id>_model_results.csv`, and prints the comparison table without touching the active model. With `--deploy`, the Stacking Ensemble is promoted only if it passes the quality gate; the five individual models are comparison results and can never be deployed. Each deployment saves the Stacking pipeline and its metadata to a separate `saved_models/deployed_model.<unique-id>.joblib` file, then switches the active file reference in one SQLite transaction. Previous files are retained for requests already in progress and deployment history. Existing installations that reference `deployed_model.joblib` continue to work. `ALGOGUARD_DEPLOYED_MODEL_PATH` sets the folder and base filename for new deployment files.

The default minimums are Accuracy 70%, F1-score 70%, and ROC-AUC 70%. They can be configured before startup with `ALGOGUARD_MIN_STACKING_ACCURACY`, `ALGOGUARD_MIN_STACKING_F1`, and `ALGOGUARD_MIN_STACKING_ROC_AUC`. Values are percentages from 0 to 100.

Artifacts from earlier deployment workflows are intentionally treated as legacy. After upgrading, run `train.py` on a dataset to create a `stacking-five-v3` artifact; an older individual or Stacking artifact cannot be reactivated under the current policy.

## Accuracy, Scores and Their Limits

Stage 6A measured the model on data it has never seen and checked live features against Argus. The deployed model is training run 10: run 8's UNSW-NB15 rows plus 2,460 benign flows recorded on a Windows PC (DEF-19). The evidence is in `docs/migration/06a-evidence.md` and `test-results/iter6_6a/`; the main limits are:

- **Replay accuracy is not an accuracy claim.** The bundled samples are drawn from the training rows, so the Live Monitor's match rate on them shows the model reproduces its training data, nothing more.
- **Untouched evaluation.** On the official UNSW-NB15 training partition, minus every row already seen (108,739 rows), run 10 reaches accuracy 86.2%, F1 85.4%, recall 76.0%, false-positive rate 2.3% and ROC-AUC 0.970 (run 8: 86.0%, 85.2%, 75.9%, 2.5%, 0.969). Accuracy, F1 and recall miss the 90% targets, and Fuzzers attacks are caught only 22.8% of the time. The repository's file named `UNSW_NB15_training-set.csv` is in fact the official testing partition, which all models were trained on.
- **Calibration.** On the hold-out run 8's scores were well calibrated (expected calibration error 0.015), so no calibrator was added. On the untouched set they were not (0.112): 21.9% of the flows scored Low were attacks.
- **Live features.** Live features follow UNSW-NB15's definitions. On a fresh capture, 15 of the 16 inputs matched Argus within the predeclared tolerances; the 16th, `sload`, matches only once Argus's Ethernet-header bytes are discounted (99% of flows). Evidence: one PC, one Wi-Fi adapter, generated web traffic.
- **False alarms on live traffic (DEF-19).** UNSW-NB15 labels every HTTPS (`ssl`) row an attack, so HTTPS is reported as an unidentified service (`-`), and run 10 has seen modern benign traffic. On ordinary browsing it labels 3–7% of benign flows Attack (run 8 labelled 70–97%). That holds for the PC whose traffic it was trained with; other computers may see more, and attacks on live traffic remain untested.
- **Latency.** Live sessions capture packets and score flows in two separate processes, so neither waits behind the other or behind the web pages. In the app, from a flow's last packet to its verdict took a median of 89–92 ms and a 95th percentile of 114–129 ms (two 90-second runs on 2026-10-26, about 90 packets/s), within the 500 ms budget; 50 ms of that is the hold for packets in flight. Upload to the cloud adds about 0.8–0.9 s (median).

## Manual Prediction

The Prediction page builds its fields from the active artifact's saved feature schema. A prediction stores Normal or Attack, confidence, the attack probability with its band and band-policy version, model name, timestamp, latency, and alert status. Confidence belongs to the verdict (a confident Normal has a high confidence and a Low attack score); the attack score is always the Attack class's probability. Attack creates an alert and audit events; Normal does not create an attack alert. The same path is available as JSON at `POST /predict` for an authenticated session.

The prediction and monitor-start APIs require a JSON object and the
`Content-Type: application/json` header. Malformed JSON, arrays, and scalar values
are rejected before any prediction is stored or monitor session is started.

Every field of the saved feature schema is required, in the form and in the JSON API:
an omitted, `null` or empty field is refused with "Enter a value for every field;
missing: ..." and nothing is stored, so a guessed default is never recorded as if it
had been observed. Counts, sizes, rates, durations, TTLs and gaps cannot be negative,
and values beyond the model's numeric range are refused in plain language.
Non-finite numbers such as `NaN` or `Infinity` are rejected with a validation error.
Very large finite byte counts retain their feature values even when the optional
packet-size summary exceeds the database's integer range; that summary is stored as `null`.

## System Logs and Alerts

Alert History contains anomalous predictions from both the Live Monitor and manual prediction, with the source and destination of each flow. Alerts, reports, the dashboard and the monitor feed show the same attack-score band (`services/alert_policy.py`, policy `attack-score-bands-v1`). The score is a probability, not a measure of impact, and no band is called Critical. Alerts stored before scores existed show **Not scored** with their legacy severity (High, Medium or Low by confidence), which is still recorded for older readers. Changing a band threshold requires a new policy version; the cloud database accepts only versions its migrations define. Alerts start Open and are moved to Acknowledged or Resolved (or reopened) from the Alerts page in both SQLite and cloud mode; the dashboard's Open Alerts counts only Open alerts, and every change is logged.

System Logs contain authentication, validation, preprocessing, training, ranking, deployment, live monitoring, prediction, reporting, and application events. Logs are newest first and support date, module, status, model, run ID, and text filters.

## Quality Assurance

Install QA tools and run all checks (the maintainer guide, section 9, also covers the integration lane against the local Supabase stack):

```bat
venv\Scripts\python.exe -m pip install -r requirements-dev.txt -r requirements-maintainer.txt
npm ci
npm run typecheck
venv\Scripts\python.exe -m ruff check .
venv\Scripts\python.exe -m pytest -q
```

The browser polling checks use Node.js's built-in test runner (no npm packages):
`node --test tests/monitor_poll.test.cjs`.

GitHub Actions (`.github/workflows/ci.yml`) runs the same lint, type, unit, and
polling checks on Windows for every push to `main` and every pull request. The
Supabase integration lane stays a local run because it needs Docker.

Before running it, check that every local service is up: `npx supabase start` can
exit successfully while listing a crashed container under "Stopped services". If
the Edge Runtime is listed there, run `docker start supabase_edge_runtime_AlgoGuard`;
otherwise the Edge Function tests fail with HTTP 503 "name resolution failed".

The pinned Pyright checker uses the project's `venv` and checks all application,
script, and test modules. Its scope is configured in `pyproject.toml`; generated
reports and backup copies are not included.

The ruff rule set is pinned in `pyproject.toml` (`E`, `F`, `W`, `I`) so the check reports the same result on every ruff release. The test suite uses temporary database and artifact paths, so it never modifies `database/` or `saved_models/`; the expanded cloud suite can take several minutes on Windows.

## Troubleshooting

Analyst problems (setup, sign-in, approval, models, uploads, capture) are covered in the analyst guide's Troubleshooting table. For maintainers:

`AlgoGuard cannot start. This installation is not set up yet.`: `.env` has no cloud settings. Fill them in, or set `ALGOGUARD_DB_MODE=sqlite` for legacy local mode. `python config.py` reports what is wrong without starting anything.

`ModuleNotFoundError`: use the virtual environment where the requirements were installed, `venv\Scripts\python.exe`.

`python` opens the Microsoft Store or is not recognised: use `py -3.14` for the `venv` step, then run everything through `venv\Scripts\python.exe`.

Invalid CSV: confirm the file is CSV, the target is last, exactly two target classes exist, and both classes have enough rows.

No active deployment in legacy local mode or before publishing: run `python train.py <csv> --deploy` and confirm Stacking passed the quality gate. Missing artifact: the active deployment's record must point to an existing model file; use the artifact path printed by `train.py`, or redeploy.

Live Monitor will not start: a session is already running, so stop it first. Live capture unavailable: install Npcap and run AlgoGuard with Administrator rights, then reload the Monitor page; flows appear when they end (TCP teardown or 15 seconds of idle), not on the first packet.

Lost the `admin` password of a disposable legacy local database: stop the server, delete `database/algoguard.sqlite3` (only if it holds nothing you need), set `ALGOGUARD_ADMIN_USERNAME` and `ALGOGUARD_ADMIN_PASSWORD`, start again and redeploy. Cloud passwords are reset in the Supabase dashboard; AlgoGuard has no password-reset page.

## Scope

AlgoGuard is intended for academic and controlled use, released at the scope of its documented pilot: its research targets (accuracy, F1 and recall of 90% on untouched data, and attack detection on live traffic) are not met, as recorded under Accuracy, Scores and Their Limits. Model quality depends on representative, correctly labeled data. Each installation captures and classifies traffic on its own host, stores flow metadata only, and never blocks traffic or inspects payloads; administrators can observe installations but not control their captures. It is not a replacement for a production IDS/IPS or a security operations platform.
