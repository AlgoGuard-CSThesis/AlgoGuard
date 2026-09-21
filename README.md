# AlgoGuard

AlgoGuard is a local Flask application that detects anomalies in network traffic using a deployed Stacking Ensemble. The web application is **inference-only**: it classifies traffic flow by flow in the Live Monitor, or one record at a time on the Prediction page. Model training and deployment happen offline through `train.py`, so the running detector is never disturbed by a training job.

The Live Monitor accepts three traffic sources through one pipeline: **live packet capture** from a network interface (data packets are aggregated into flows in real time and classified the moment each flow ends), **recorded packet captures** (`.pcap` files replayed through the same flow tracker, reproducibly), and the original **labelled CSV replay** of the bundled UNSW-NB15 samples, which keeps ground-truth labels visible for accuracy demonstrations. Live capture requires the Npcap driver and elevated privileges on Windows; when they are absent, both replay modes keep working and the monitor explains why live mode is unavailable. AlgoGuard observes and classifies traffic; it never blocks it, and it stores flow metadata only, never packet payloads. Capture live traffic only on networks you are authorized to monitor.

## Runtime Architecture

The application has two detection surfaces and one offline training tool:

| Surface | Purpose |
| --- | --- |
| Live Monitor (`/monitor`) | Streams flows from a live interface, a PCAP recording, or a CSV sample through the deployed model, one at a time, with a live feed, timeline chart, and running attack/normal statistics. |
| Prediction (`/simulation`, `POST /predict`) | Classifies a single hand-entered flow, also available as a JSON API. |
| `train.py` | Trains and ranks the six candidates offline and deploys the Stacking Ensemble when it passes the quality gate. |

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

Offline, run only when the model needs to be rebuilt:

```text
python train.py <csv> --deploy
  -> validation and independent training-run record
  -> stratified split
  -> five leakage-safe individual pipelines
  -> individual-model comparison ranking
  -> Stacking training with the five base learners
  -> Stacking evaluation and quality gate
  -> Stacking deployed as the active artifact
```

Online, the everyday detection path:

```text
python app.py
  -> Live Monitor replays flows through the active artifact
     (or Prediction classifies one hand-entered flow)
  -> Normal / Attack verdict with confidence and latency
  -> Attack raises an alert
  -> prediction, alert, and audit events stored in SQLite
```

Training history, results, deployments, predictions, alerts, reports, admin accounts, and audit events are stored in SQLite. Reports, per-run artifacts, and the active artifact are runtime files excluded from Git.

## Project Structure

```text
AlgoGuard/
|-- app.py
|-- train.py
|-- migrate.py
|-- requirements.txt
|-- requirements-dev.txt
|-- services/
|   |-- database_service.py
|   |-- deployment_service.py
|   |-- evaluation_service.py
|   |-- flow_tracker_service.py
|   |-- live_monitor_service.py
|   |-- model_registry.py
|   |-- preprocessing_service.py
|   |-- resource_service.py
|   |-- simulation_service.py
|   |-- traffic_source_service.py
|   `-- training_service.py
|-- templates/
|-- static/
|   |-- css/          (style.css imports legacy-ui.css)
|   `-- vendor/       (Bootstrap 5.3.3 + Bootstrap Icons, served locally)
|-- tests/
|-- datasets/
|   |-- algoguard_big.csv
|   |-- algoguard_bigger.csv
|   `-- algoguard_biggest.csv
|-- captures/
|-- database/
|-- saved_models/
|-- reports/
`-- research/
```

`app.py` serves detection only; the training services are reached through `train.py`. `uploads/` is retained for historical runtime files and is no longer written to.

`research/` is an archived notebook workspace and is not imported by the Flask application. `AlgoGuard Iterations (plan).md` documents the completed Iterations 1–4 and the architecture for the cloud migration; `AlgoGuard Migration (backlog).md` is the stage-by-stage task list for Iterations 5–6; `ALGOGUARD_USER_MANUAL.txt` is the end-user and demo guide.

## Installation on Windows PowerShell

Requirements: Python 3.10 or newer and Git. Clone the repository, then run all commands from the project root, the directory containing `app.py` and `requirements.txt`:

```powershell
git clone https://github.com/pqauoz/AlgoGuard.git
cd AlgoGuard
py -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

If PowerShell blocks activation (`running scripts is disabled on this system`), either allow it for the current window or call the environment's interpreter directly:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass   # then .\venv\Scripts\Activate.ps1
# or
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe app.py
```

The web interface is fully self-contained: Bootstrap and its icon font are vendored under `static/vendor/`, so the application runs without internet access once the Python packages are installed.

## First Run on a Fresh Clone

A fresh clone contains code and the sample datasets only. The trained model, the SQLite database, reports, and captures are runtime files excluded from Git, so a new installation has **no deployed model** until you train one. Do this once, in order:

```powershell
python train.py datasets\algoguard_big.csv --deploy   # ~20-60 s; trains six candidates, deploys Stacking
python app.py                                          # start the detector
```

Open `http://127.0.0.1:5000`.

The first command creates `database/algoguard.sqlite3` and prints a **one-time random password for the `admin` account** (the account is created by whichever command touches the new database first: `train.py`, `migrate.py`, or `app.py`). Copy it from the terminal before it scrolls away; it is never shown again. To pick the first credentials yourself, set `ALGOGUARD_ADMIN_USERNAME` and `ALGOGUARD_ADMIN_PASSWORD` before that first command (see Run the Application).

If you open the site before training, every page explains that no model is deployed and shows the command above; nothing else is broken.

## Database Migration

Migrations are additive and preserve existing rows:

```powershell
python migrate.py
```

The application also applies pending migrations at startup, so this command is optional; it exists for upgrading an existing installation without starting the server. Do not delete `database/algoguard.sqlite3` to upgrade an existing installation.

## Run the Application

```powershell
python app.py
```

Open `http://127.0.0.1:5000`.

AlgoGuard listens on `127.0.0.1` with the debugger off by default, because it handles captured network traffic. Both are opt-in:

```powershell
$env:FLASK_DEBUG = "1"          # developer reloader and debugger
$env:ALGOGUARD_HOST = "0.0.0.0" # reachable from other machines - only on a network you control
$env:ALGOGUARD_PORT = "5000"    # also the port excluded from live capture
```

Less common overrides: `ALGOGUARD_SECURE_COOKIES=1` marks the session cookie `Secure` (only behind HTTPS); `ALGOGUARD_DATABASE_PATH` and `ALGOGUARD_DEPLOYED_MODEL_PATH` relocate the SQLite file and the active artifact, which is how the test suite and disposable installations avoid touching `database/` and `saved_models/`. Environment variables set with `$env:` last for the current PowerShell window only.

On a new database, AlgoGuard creates the `admin` account with a random password
and prints it once in the terminal of whichever command created the database
(`train.py`, `migrate.py`, or `app.py`). Store it immediately. To choose the
first account credentials yourself, set these environment values before first
startup:

```powershell
$env:ALGOGUARD_ADMIN_USERNAME = "your_admin"
$env:ALGOGUARD_ADMIN_PASSWORD = "a-strong-password"
$env:ALGOGUARD_ADMIN_EMAIL = "admin@example.com"
$env:ALGOGUARD_SECRET_KEY = "a-long-random-secret"
python app.py
```

When `ALGOGUARD_SECRET_KEY` is omitted, the local single-process server generates
an unpredictable temporary key. That is safer than a shared development secret,
but it signs sessions only until the process restarts. Set a persistent random
key before exposing the app or running more than one server process.

Multiple accounts are supported. Administrators can create Administrator or
Analyst accounts from the Admins page. Password hashes and accounts persist in
SQLite. These first-run settings do not replace an account already stored in the
database. Login state uses a signed Flask browser session and ends when the session
cookie is cleared, the user logs out, or an ephemeral signing key changes after
restart; it is not a permanent login token.

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
dur,proto,service,state,spkts,dpkts,sbytes,dbytes,rate,sttl,dttl,sload,dload,sinpkt,dinpkt,label
2.001348,tcp,-,FIN,16,14,862,802,14.490233,254,252,3233.820312,2977.99292,133.423196,146.368077,Normal
1.665064,tcp,ftp-data,FIN,14,6,8928,320,11.410973,31,29,39835.10547,1282.833618,128.081847,332.462,Normal
0.472012,tcp,http,FIN,10,8,834,354,36.016032,254,252,12728.48926,5254.103516,52.445778,60.35343,Attack
0.320972,tcp,http,FIN,10,8,784,1256,52.964122,62,252,17596.55078,27391.79688,35.663556,38.518145,Attack
```

Each row is one traffic-flow observation. Each feature column is one measured property. The final `label` is the known class used during supervised training. Adding a row labeled `Attack` does not force future inputs with those exact values to be attacks; it gives the models another labeled example from which to learn.

## Included Dataset Files

Three CSV files are included in `datasets/`:

- `algoguard_big.csv`: 5,000 rows
- `algoguard_bigger.csv`: 10,000 rows
- `algoguard_biggest.csv`: 20,000 rows

The files are nested, stratified, non-replacement samples of the labeled UNSW-NB15 training partition. Each file uses the same 15 input features: `dur`, `proto`, `service`, `state`, `spkts`, `dpkts`, `sbytes`, `dbytes`, `rate`, `sttl`, `dttl`, `sload`, `dload`, `sinpkt`, and `dinpkt`. The final binary `label` uses `Normal` and `Attack`. Other source columns, including the identifier and `attack_cat`, were removed to keep training practical and prevent identifier noise or direct target leakage.

These files serve two purposes: they are the replay sources offered by the Live Monitor, and they are valid inputs to `train.py` when the model is rebuilt. Because each row keeps its true `label`, the Live Monitor can show whether each prediction matched the recorded outcome.

Dataset source and attribution: [The UNSW-NB15 Dataset, UNSW Research](https://research.unsw.edu.au/projects/unsw-nb15-dataset). Academic work using these files should cite the dataset publications listed by UNSW.

## Live Monitor

Open **Live Monitor** and choose a traffic source:

- **Sample replay (labelled flows)**: one of the bundled CSV samples, with replay speed (slow, normal, or fast) and flow order (sequential or randomised). Each verdict is compared against the recorded label.
- **Recorded packets (PCAP)**: a `.pcap`, `.pcapng`, or `.cap` file placed in `captures/`. The data packets are aggregated into bidirectional flows by the flow tracker, and the resulting flows are replayed at the chosen speed. Packet-derived flows have no ground-truth label, so the Actual column shows a dash.
- **Live interface capture**: a network interface, sniffed in real time. Packets are tracked into flows as they arrive; a flow is classified when its TCP connection tears down, after 15 seconds of silence, or in 120-second slices for long-lived connections. Live mode runs at wire pace (speed and order do not apply) and reports detection lag — the time from a flow ending to its verdict — alongside inference latency, plus captured and dropped packet counters. Each live session is recorded in the `capture_session` table.

For packet sources the flow tracker computes the same 15 features the model was trained on (`dur`, `proto`, `service`, `state`, `spkts`, `dpkts`, `sbytes`, `dbytes`, `rate`, `sttl`, `dttl`, `sload`, `dload`, `sinpkt`, `dinpkt`). `service` is inferred from well-known ports and `state` from observed TCP flags, which approximate how the original UNSW-NB15 features were generated; the closer your training data is to your own network's traffic, the better the verdicts (see Retrain and Deploy).

CSV replay uses the same Normal/Attack label mapping as training, including numeric
labels and recognized Normal aliases. Missing or ambiguous labels report an error.
Missing feature values and invalid numeric values are imputed by the saved pipeline;
stored records preserve those missing values as `null`.

If live capture fails after starting, the monitor reports an error and closes the
capture. A session becomes completed, stopped, or failed only after cleanup and
final database writes finish, so it can be restarted as soon as that state appears.

Packet monitoring checks the deployed model's required fields before opening a
capture. If any field is outside these 15 features, it reports the missing fields
and asks you to train with compatible data. Manual prediction remains available
for models trained with custom fields.

Then choose a storage mode and Start; Pause, Resume, and Stop control the session. The page polls for new classifications, so reloading the browser re-attaches to a session that is still running rather than orphaning it.

The feed reconnects automatically after temporary connection failures. Requests
time out after ten seconds; if a control response is lost, the page checks the
session status without repeating the action. An expired login prompts you to log in again.

**Live capture prerequisites (Windows):** install [Npcap](https://npcap.com) with the "WinPcap API-compatible mode" default, and run AlgoGuard from a terminal with Administrator rights. Without them, the monitor lists live capture as unavailable with the reason, and replay sources continue to work.

AlgoGuard excludes its own web traffic from capture. Without this, monitoring the interface the application serves from would classify the browser's own status polling and the feed would fill with AlgoGuard watching itself. The exclusion covers the port from `ALGOGUARD_PORT` and is applied both in the capture filter and per packet, so it still holds when a driver cannot compile the filter. Packets skipped this way are reported as "own traffic" beside the captured-packet count.

Storage modes bound how much the session writes to SQLite:

- **Session only** stores nothing; the feed is live output alone.
- **Save attacks** (default) stores only attack flows, each with a prediction and an alert.
- **Save every flow** stores every classification.

Persisting sessions stop writing after 300 records and mark themselves capped; classification continues. Stored flows are tagged `live_monitor` (replay sources) or `live_capture` (live interface) in `network_traffic`, so monitoring traffic stays distinguishable from manual predictions. Flows from packet sources store their real endpoint addresses and ports; CSV replay rows keep cosmetic private-range endpoints, since the samples carry no addresses.

One monitoring session runs per process. The artifact is loaded once when a session starts, so a redeployment mid-session is picked up the next time the monitor is started. Open monitor tabs track session IDs and reset their feeds when another tab starts a new session.

## Retrain and Deploy

Training is a terminal task, not a web page:

```powershell
python train.py datasets\algoguard_big.csv
python train.py datasets\algoguard_big.csv --deploy
python train.py my_traffic.csv --deploy --admin your_admin
```

Without `--deploy`, the run trains and ranks all six candidates, writes `reports/training_run_<id>_model_results.csv`, and prints the comparison table without touching the active model. With `--deploy`, the Stacking Ensemble is promoted only if it passes the quality gate; the five individual models are comparison results and can never be deployed. Each deployment saves the Stacking pipeline and its metadata to a separate `saved_models/deployed_model.<unique-id>.joblib` file, then switches the active file reference in one SQLite transaction. Previous files are retained for requests already in progress and deployment history. Existing installations that reference `deployed_model.joblib` continue to work. `ALGOGUARD_DEPLOYED_MODEL_PATH` sets the folder and base filename for new deployment files.

The default minimums are Accuracy 70%, F1-score 70%, and ROC-AUC 70%. They can be configured before startup with `ALGOGUARD_MIN_STACKING_ACCURACY`, `ALGOGUARD_MIN_STACKING_F1`, and `ALGOGUARD_MIN_STACKING_ROC_AUC`. Values are percentages from 0 to 100.

Artifacts from earlier deployment workflows are intentionally treated as legacy. After upgrading, run `train.py` on a dataset to create a `stacking-five-v3` artifact; an older individual or Stacking artifact cannot be reactivated under the current policy.

## Manual Prediction

The Prediction page builds its fields from the active artifact's saved feature schema. A prediction stores Normal or Attack, confidence, model name, timestamp, latency, and alert status. Attack creates an alert and audit events; Normal does not create an attack alert. The same path is available as JSON at `POST /predict` for an authenticated session.

The prediction and monitor-start APIs require a JSON object and the
`Content-Type: application/json` header. Malformed JSON, arrays, and scalar values
are rejected before any prediction is stored or monitor session is started.

In the JSON API, omitted fields use the saved training defaults. Explicit `null`
or empty numeric fields are imputed by the saved pipeline and recorded as `null`.
Non-finite numbers such as `NaN` or `Infinity` are rejected with a validation error.
Very large finite byte counts retain their feature values even when the optional
packet-size summary exceeds the database's integer range; that summary is stored as `null`.

## System Logs and Alerts

Alert History contains anomalous predictions from both the Live Monitor and manual prediction. System Logs contain authentication, validation, preprocessing, training, ranking, deployment, live monitoring, prediction, reporting, and application events. Logs are newest first and support date, module, status, model, run ID, and text filters.

## Quality Assurance

Install QA tools and run all checks:

```powershell
python -m pip install -r requirements-dev.txt
python -m ruff check app.py train.py migrate.py services tests
python -m pytest -q
```

The browser polling checks use Node.js's built-in test runner (no npm packages):
`node --test tests/monitor_poll.test.cjs`.

The ruff rule set is pinned in `pyproject.toml` (`E`, `F`, `W`, `I`) so the check reports the same result on every ruff release. The test suite uses temporary database and artifact paths, so it never modifies `database/` or `saved_models/`; expect it to take under a minute because it trains small models.

## Troubleshooting

`ModuleNotFoundError`: activate the same virtual environment where dependencies were installed, or invoke `.\venv\Scripts\python.exe` directly.

Lost the admin password on a fresh installation: stop the server, delete `database/algoguard.sqlite3` (only if it holds nothing you need), set `ALGOGUARD_ADMIN_USERNAME` and `ALGOGUARD_ADMIN_PASSWORD`, and start again; the new database is seeded with those credentials. Redeploy the model afterwards because the deployment record lived in the deleted database.

`python` opens the Microsoft Store or is not recognised: use `py` instead of `python` for the `venv` step, then run everything through the activated environment.

Invalid CSV: confirm the file is CSV, the target is last, exactly two target classes exist, and both classes have enough rows.

No active deployment: run `python train.py <csv> --deploy` and confirm Stacking passed the quality gate.

Missing artifact: the active deployment's database record must point to an existing model file. Use the artifact path printed by `train.py` rather than assuming a fixed filename. Redeploy with `python train.py <csv> --deploy` if the referenced file is missing.

Live Monitor will not start: a session is already running, so stop it first. If it reports an error immediately, the deployed artifact is missing or does not match the deployment record.

Live capture unavailable: install Npcap (Windows) and run AlgoGuard with Administrator rights, then reload the Monitor page. If a capture starts but classifies nothing, confirm the selected interface is the one carrying traffic; flows appear when they end (TCP teardown or 15 seconds of idle), not on the first packet.

Forgot local password: do not delete a database containing needed records. For a disposable new local installation only, recreate the database and seed credentials. Production-style password reset administration is outside this prototype's scope.

## Scope

AlgoGuard is intended for academic and controlled local use. Model quality depends on representative, correctly labeled data. It captures and classifies traffic on a single host, stores flow metadata only, and never blocks traffic or inspects payloads; it is not a replacement for a production IDS/IPS or a security operations platform.
