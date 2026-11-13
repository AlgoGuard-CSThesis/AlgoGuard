# AlgoGuard maintainer guide

This guide is for the person who runs the cloud project behind AlgoGuard: database changes,
the first administrator, model training and publishing, analyst packages, backups, moving old
installations to the cloud, and recovery. Analysts use [the analyst guide](ANALYST_GUIDE.md)
instead; administrators manage accounts and approvals in the app itself.

Commands are for Command Prompt, run from the repository folder. `py` and `venv\Scripts\...`
paths are Windows paths.

## 1. Roles and credentials

| Role | Does | Holds |
|---|---|---|
| Analyst | Monitors traffic on their own computer | Their e-mail and password; the project URL and publishable key in `.env` |
| Administrator | Creates accounts, approves installations, views computer health, runs retention | The same as an analyst, with the Administrator role |
| Maintainer | Everything in this guide | `.env.maintainer` with the database connection string and the secret key |

Keep the two settings files apart:

- **`.env`** holds only public values (`NEXT_PUBLIC_SUPABASE_URL`,
  `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY`) and local settings. The app refuses to start with a
  secret or service key in it, with a non-HTTPS remote URL, or with a host other than loopback.
- **`.env.maintainer`** (copy `.env.maintainer.example`) holds `DATABASE_URL` (the pooler
  connection string, port 6543), `SUPABASE_API_URL` and `SUPABASE_SECRET_KEY`. Only the
  maintainer tools read it (`maintainer_env.py`); the web app never does. Keep it on trusted
  maintainer computers only. Never send it, paste it, or put it in a package, an issue or a
  test record. Change the database password and the secret key if either is ever exposed.

Both files are ignored by git, and the analyst package is built from an allow list that cannot
include them (section 6).

## 2. Maintainer computer

```bat
git clone https://github.com/AlgoGuard-CSThesis/AlgoGuard.git
cd AlgoGuard
py -3.14 -m venv venv
venv\Scripts\python.exe -m pip install -r requirements-dev.txt -r requirements-maintainer.txt
npm ci
copy .env.maintainer.example .env.maintainer
```

Use Python 3.14.6, the version recorded in `requirements-model.lock`: models record the exact
runtime they were trained with, and both publishing and loading refuse a mismatch. `npm ci`
installs the Supabase command-line tool and the type checker used by the checks.

To run the app on this computer as an analyst would, fill in `.env` the same way an analyst
does (`copy .env.example .env`). With no cloud settings, `python app.py` refuses to start and
explains why. `ALGOGUARD_DB_MODE=sqlite` runs the **legacy local mode**, which keeps every record
in `database/algoguard.sqlite3` and never uploads; use it only for installations that have not
been moved to the cloud yet (section 8) and for the training records in section 5.

## 3. Setting up a cloud project (once)

1. Create the Supabase project and fill in `.env.maintainer`. `venv\Scripts\python.exe
   check_db_connection.py` confirms that the database connection works before anything else.
2. Apply the database schema and confirm nothing is pending:

   ```bat
   venv\Scripts\python.exe cloud_migrate.py --status
   venv\Scripts\python.exe cloud_migrate.py --dry-run
   venv\Scripts\python.exe cloud_migrate.py --yes
   venv\Scripts\python.exe cloud_migrate.py --status
   ```

   The `Target:` line names the database before anything happens; check it is the project you
   mean.
3. Deploy the account-administration function. It checks the caller's token itself, so the
   gateway check is turned off:

   ```bat
   npx supabase functions deploy account_admin --project-ref <project-ref> --no-verify-jwt
   ```

4. In the Supabase dashboard, under **Authentication**, turn off public sign-ups and keep the
   e-mail provider on. If your plan offers it, also turn on leaked-password protection.
5. Create the first Administrator. The generated password is printed once:

   ```bat
   venv\Scripts\python.exe bootstrap_admin.py --email <admin e-mail> --username <name>
   ```

6. Publish a model (section 5), then sign in to the app as the Administrator and check that
   Manual Prediction shows the active model.

After that, administrators create every other account on the **Accounts** page.

**Forgotten passwords.** The app has no page for changing or resetting a password, and Supabase's
e-mailed recovery links have nowhere to land in it, so a forgotten password is replaced here. The
new password is generated and printed once (`--password-stdin` reads one instead; it must meet the
Accounts page rule). Give it to the user privately:

```bat
venv\Scripts\python.exe reset_password.py --email <user e-mail>
```

A reset also ends the account's running sign-ins: they can no longer renew, so each stops when its
current access token expires (one hour at most with Supabase's default) and the user signs in
again. To lock someone out at once, deactivate the account on the Accounts page.

## 4. Database changes

Schema changes live in `supabase/migrations/` as numbered SQL files. Never edit a migration
that has been applied anywhere; add a new one. `cloud_migrate.py` records each applied file and
its checksum:

- `--status` lists applied and pending files.
- `--dry-run` checks checksums and lists what would be applied, without changing anything.
- `--yes` applies pending files, each in its own transaction. Required for a remote database.
- `--allow-out-of-order` applies a pending file older than the newest applied one; use it only
  when you know why.

Test a new migration on the local stack first (section 9). Clients built before a migration
keep working where the migration says so (for example, older clients upload unscored records
after the attack-score change).

## 5. Models

**Train and evaluate** (on a maintainer computer, in the local training records):

```bat
venv\Scripts\python.exe train.py datasets\algoguard_big.csv
venv\Scripts\python.exe train.py <training csv> --deploy
venv\Scripts\python.exe scripts\evaluate_model.py --model saved_models\run_<id>\stacking.joblib --data <held-out csv> --train-csv <training csv>
```

`train.py` trains and ranks the six candidates and, with `--deploy`, makes the Stacking
Ensemble the local active model if it passes the quality gate. The README's "Retrain and Deploy"
section describes the datasets, the quality gate and how the current model (training run 10) was
built; `docs/migration/06a-evidence.md` records its evaluation. `docs/MODEL_TRAINING.md`
explains the whole method in one place (data, features, candidates, gate, measurement and the
training-run history), for reports and the thesis. Training records live in the
local SQLite file `database/algoguard.sqlite3`; keep a backup of it (section 7).

**Publish** a trained model to the cloud. Pick a new publication ID once and write it down:

```bat
venv\Scripts\python.exe -c "import uuid; print(uuid.uuid4())"
venv\Scripts\python.exe publish_model.py --model-id <local model id> --publication-id <ID> --yes
```

Publishing checks the model (quality gate, runtime, the 16 flow features) before anything is
uploaded, uploads it under a new name that is never overwritten, verifies the uploaded bytes,
and then makes it the single active model. Success prints `"status": "active"`. If it fails,
run the same command with the **same** ID: it continues where it stopped instead of starting a
second publication. Running captures keep the model they started with; new sessions use the new
one. To go back to an earlier model, publish that local model again with a new ID.

**Clean up** model uploads that nothing uses (failed or abandoned publications older than a
day). It only lists them unless you add `--yes`; active, published and superseded models are
always kept:

```bat
venv\Scripts\python.exe cleanup_model_objects.py
venv\Scripts\python.exe cleanup_model_objects.py --yes
```

## 6. Analyst packages

Build the zip that analysts install:

```bat
venv\Scripts\python.exe scripts\build_analyst_package.py
```

It packages the last commit (commit your changes first) into
`dist\AlgoGuard-analyst-<version>.zip`, with a `BUILD` file naming the commit, which each
installation reports on the Computers page. The contents are chosen from an allow list: the
analyst app and every project module it imports, its pages and styles, the sample datasets,
`SETUP_ALGOGUARD.cmd`, `START_ALGOGUARD.cmd`, `.env.example`, the requirements files and the
analyst guide. Training, publishing, migration, import and recovery tools, tests, test records,
research files, databases, models, captures and settings files cannot be included. The build
refuses to write the zip if any file contains a secret key, a database password, a private key,
a signed token or a real e-mail address, or if a packaged module imports one that is missing.

Give analysts the zip together with the project URL and publishable key, and create their
accounts on the Accounts page. Bump `RELEASE` in `app_version.py` for a new release.

## 7. Backups and recovery

- **Cloud records:** `cloud_recovery.py export` writes a verified package of every application
  table and the model files; `verify` checks one; `restore` loads it into an empty project with
  the current migrations; `restore-outbox` gives an installation back its records waiting to
  upload. Tables a new project creates itself are excluded on purpose (`RECOVERY_EXCLUDED`).
  Supabase Auth users and project settings are not in the package: rebuild them as section 3
  and [the cutover record](migration/05e-cutover.md) describe.
- **Training records and models:** back up `database/algoguard.sqlite3` and `saved_models/`
  on the maintainer computer, for example with `legacy_backup.py backup`.
- Cloud records cannot be turned back into a local SQLite database. Recovery goes forward,
  with `cloud_recovery.py`.

The full procedure, including which writers to stop first, is in
[05e-cutover.md](migration/05e-cutover.md) ("Recovery"); [06c-recovery.md](migration/06c-recovery.md)
records the restore drills.

## 8. Moving an old local installation to the cloud

Installations that still run in legacy local mode keep their records in
`database/algoguard.sqlite3`. Move them with `legacy_backup.py` (consistent backup, verify,
test restore) and `legacy_import.py` (import into the cloud under a node ID, then enable cloud
writes), following [05e-cutover.md](migration/05e-cutover.md) ("Controlled cutover") step by
step. Imports keep the original identities and evidence. Afterwards the installation removes
`ALGOGUARD_DB_MODE=sqlite` from its `.env`, adds the cloud settings and signs in.

## 9. Checks and test lanes

```bat
venv\Scripts\python.exe -m ruff check .
npm run typecheck
venv\Scripts\python.exe -m pytest -q
node --test tests\monitor_poll.test.cjs
```

The offline suite uses a fake cloud service and temporary paths and never touches `database/`,
`saved_models/` or `.algoguard/`. The **integration lane** runs against the local Supabase stack
in Docker. The first time on a computer, make the stack's token-signing key before starting it
(in Command Prompt, which keeps the output UTF-8; the second line stores the key as the list the
stack reads). Both key files are ignored by git and never leave the computer:

```bat
npx supabase gen signing-key --algorithm ES256 > supabase\signing_keys.json
venv\Scripts\python.exe -c "import json,pathlib; p=pathlib.Path('supabase/signing_keys.json'); k=json.loads(p.read_text(encoding='utf-8-sig')); p.write_text(json.dumps(k if isinstance(k,list) else [k]),encoding='utf-8')"
```

Then start the stack and save its local addresses and keys where the tests read them (repeat the
second line whenever the stack is recreated):

```bat
npx supabase start
npx supabase status -o env > .env.supabase.local
set RUN_WINDOWS_CAPTURE_TESTS=1
venv\Scripts\python.exe -m pytest -m integration -q
```

`npx --no-install supabase migration up --local` applies new migrations to a running local
stack. If the Edge Function tests fail with HTTP 503, run
`docker start supabase_edge_runtime_AlgoGuard`. The Windows capture tests need Npcap and an
administrator terminal. `SUSTAINED_SECONDS=900` runs the sustained-load test for 15 minutes
instead of 90 seconds (`SUSTAINED_REPORT=<file>` saves its figures). Run the lane on an idle
PC: the sustained-load test measures an upload-delay budget, so it skips itself with a message
when more than half the CPU is already in use (DEF-25).

Two release checks are opt-in because they take longer and need more than the lane:

- `set RUN_CLEAN_INSTALL=1` builds the analyst package from the last commit, installs it into an
  empty folder with `SETUP_ALGOGUARD.cmd` (packages from the internet) and uses it as an analyst
  would (`tests/integration/test_clean_install.py`; `CLEAN_INSTALL_MODEL=<model .joblib>` and
  `CLEAN_INSTALL_REPORT=<file>` are optional).
- `set RUN_UI_WALK=1` with `UI_WALK_PYTHON=<python.exe of an environment with playwright>` walks
  every page in Microsoft Edge at 1366 px and 390 px (`tests/integration/test_ui_walk.py`;
  `UI_WALK_OUTPUT=<folder>` keeps the report and screenshots).

Run both before a release; Stage 6D's results are in `test-results/iter6_6d/`.

GitHub Actions runs the lint, type, offline and polling checks for every push to `main`.

**Settings for tests and automation.** These override `.env` and are mostly used by the test
suite and disposable installations: `ALGOGUARD_STATE_DIR`, `ALGOGUARD_DATABASE_PATH`,
`ALGOGUARD_SAVED_MODEL_FOLDER`, `ALGOGUARD_DEPLOYED_MODEL_PATH`, `ALGOGUARD_REPORT_FOLDER`,
`ALGOGUARD_CAPTURE_FOLDER`, `ALGOGUARD_SECRET_KEY`, `ALGOGUARD_SECURE_COOKIES`,
`ALGOGUARD_HOST`, `ALGOGUARD_PORT`, `FLASK_DEBUG`, `ALGOGUARD_ADMIN_USERNAME`,
`ALGOGUARD_ADMIN_EMAIL`, `ALGOGUARD_ADMIN_PASSWORD` (legacy local mode) and
`ALGOGUARD_MIN_STACKING_ACCURACY`, `ALGOGUARD_MIN_STACKING_F1`, `ALGOGUARD_MIN_STACKING_ROC_AUC`
(training quality gate, in percent).

## 10. Where the decisions are recorded

`AlgoGuard Iterations (plan).md` is the plan and `AlgoGuard Migration (backlog).md` the task
list. Each stage's changes, evidence and limits are in `docs/migration/`: 05a–05f for the move
to the cloud, 06a (evidence and attack scores), 06b (administrator visibility and retention),
06c (recovery and hardening) and 06d (release). Test cycle records are in `docs/testing/`.

The Iteration 5 records name a few stage-only helpers that were removed after Iteration 6:
`README_STAGE_5A.md`, `check_5b.cmd`, `check_5d.cmd`, `scripts/prepare-local-5a.ps1` and
`measure_latency.py`. Their one lasting step, the local signing key, is in section 9; the files
themselves remain in git history (`git show e5fce19:check_5d.cmd`, for example).
