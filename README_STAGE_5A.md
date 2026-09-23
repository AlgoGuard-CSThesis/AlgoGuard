# AlgoGuard Cloud Migration — Progress Guide (Stage 5A)

**Last updated:** 2026-09-24
**Audience:** teammates picking up or reviewing this work
**Reference:** `AlgoGuard Migration (backlog).md` — this guide covers Stage 5A only.
**Evidence doc:** `docs/migration/05a-foundations.md` — the formal stage record.

---

## TL;DR — where we are

**Stage 5A: Configuration and test infrastructure** is verified locally.
This stage does **not** touch real application data or wire the app to the
cloud — it's entirely prep work: cleaning up configuration, and standing up
disposable infrastructure to safely test cloud features later.

| Task | Status |
|---|---|
| 5A.1 — Typed configuration | ✅ Done |
| 5A.2 — Analyst/maintainer config split | ✅ Done |
| 5A.3 — Cloud + local test infrastructure | Private `models` bucket recorded as created 2026-09-22; all four local integration tests passed 2026-09-24 |
| 5A.4 — Baseline evidence + test isolation | Unit and local-stack evidence recorded — see `docs/migration/05a-foundations.md` §5 |

**Local application and Stage 5A integration checks pass.** The isolated 5A
stack validates the service foundations; it does not validate Stage 5B migrations.

**Measured baseline:** `323 passed, 17 skipped` in 35.3 s; `ruff check .` clean;
10/10 Node tests. The suite grew from 197 at 5A.1 to 340 collected, the increase
being new coverage for `config`, `redaction`, `maintainer_env` and the local
stack helpers. Every skip is a declared optional dependency, never an error.

**Verification on this checkout:** 339 Python tests and all 10 Node tests passed
on 2026-09-23; the Stage 5A lint check passed. On 2026-09-24, the four local-stack
integration tests passed in 15.77 seconds after Docker and local credentials were
set up. The private pilot bucket is already recorded as created in §4.

**Separate Stage 5B issue:** starting the root project currently fails in
`20260922010000_baseline_deny_by_default.sql` with SQLSTATE `42501` when it tries
to change default privileges for `supabase_admin`. The 5A setup below isolates
the foundation tests from that unfinished migration; the migration was not changed.

The Flask app **still runs on local SQLite** and behaves exactly as before.
Nothing here is live yet.

---

## 1. What changed in the codebase

### 1.1 Centralized configuration (`config.py`)

Previously, environment variables were read ad hoc across `app.py`,
`services/database_service.py`, `services/deployment_service.py`,
`train.py`, and `services/traffic_source_service.py`. All of that is now
centralized in a new **`config.py`** at the repo root.

- `AppConfig` — a typed, frozen dataclass with every setting the app uses
  (Flask/web config, filesystem paths, admin bootstrap, deployment
  quality-gate thresholds, and a forward-looking cloud-mode switch).
- `load_config(env=None)` — builds a fresh config from `os.environ` (or a
  custom mapping). Always re-reads current env; never cached.
- `get_config()` — a cached singleton, for hot-path reads.
- `reset_config_cache()` — clears that cache. **Must be called after any
  test mutates `os.environ`**, or you'll read stale values (see §3, "gotchas").
- `redact_for_logging(text)` — strips passwords/tokens/signed-URL params
  out of a string before logging it. Now lives in **`redaction.py`** and is
  wired into the error paths of both maintainer scripts. It moved out of
  `config.py` because importing `config` runs `load_dotenv()` on the
  *analyst* `.env`, and maintainer tooling must not pull analyst config into
  its process just to reach a helper. `config.py` re-exports it.
- `ConfigError` — raised (not silently swallowed) for an invalid
  `ALGOGUARD_DB_MODE`, an unrecognized boolean, a non-integer or
  out-of-range `ALGOGUARD_PORT`, or `"supabase"` without the required
  Supabase URL/key. **This is deliberate**: invalid configuration must never
  quietly degrade into a default nobody asked for.

Every default in `config.py` was checked line-by-line against the old
hardcoded/fallback values, so an empty `.env` reproduces today's exact
local behavior.

**Two validation fixes since the first pass:**

- Booleans accepted only the literal `"1"`, so `ALGOGUARD_SECURE_COOKIES=true`
  silently evaluated to `False`. They now accept `1/true/yes/on` and
  `0/false/no/off`, and reject anything else outright — a security flag that
  fails quietly to "off" is the worst possible default.
- `ALGOGUARD_PORT` silently fell back to 5000 on a malformed value, hiding
  typos like `500O`. It now validates as an integer in 1–65535.

**Cloud mode is validated but refused.** Nothing reads `db_mode` yet — the
repository layer is 5D work. Accepting `ALGOGUARD_DB_MODE=supabase` and then
running on SQLite anyway would be precisely the silent fallback the check
exists to prevent, so `load_config()` raises for that mode until 5D. Remove
the refusal in 5D.1, not before.

### 1.2 Split analyst vs. maintainer configuration

Two new file **pairs** exist at the repo root:

| File | Committed to git? | Contains |
|---|---|---|
| `.env.example` | Yes | Template: public Supabase URL/key, local app options. No secrets. |
| `.env` | **No** | Your real values, copied from the example. |
| `.env.maintainer.example` | Yes | Template: DB password/pooler URL, Supabase secret key, JWKS URL. |
| `.env.maintainer` | **No** | Real privileged credentials. Maintainer machines only. |

**Rule of thumb:** anything an ordinary analyst install needs goes in
`.env`. Anything privileged (DB password, secret key, direct migration
access) goes in `.env.maintainer` and is never read by the Flask app itself.

**Enforced by `maintainer_env.py`.** The split used to be documentation
only: `check_db_connection.py` and `measure_latency.py` both called a bare
`load_dotenv()`, which loads the *analyst* `.env` — so `DATABASE_URL` had to
be written into the file that ships to analysts for those scripts to work.
Both now import `maintainer_env`, which loads `.env.maintainer` by explicit
path and never falls back. The Flask app must never import that module.

**TLS is now explicit** (`maintainer_env.resolve_sslmode`): `require` for
remote targets, `prefer` only for loopback, overridable with
`ALGOGUARD_DB_SSLMODE`. Both scripts previously hardcoded `prefer`, which
was the right fix for the local Docker stack (§3) but wrong to apply to the
cloud pilot — `prefer` silently continues in plaintext if TLS negotiation
fails, which over the public internet means this file's password on the
wire in the clear.

**Maintainer dependencies** now live in `requirements-maintainer.txt`
(`psycopg2-binary`, `python-dotenv`, `requests`), not in the analyst
requirements. The integration lane needs them too.

### 1.3 `.gitignore` fix

Added `!.env.maintainer.example` so the *template* is committable while the
wildcard `.env.*` rule still blocks the real `.env` and `.env.maintainer`
files. Also removed a stray typo line and a duplicate entry.

**Before committing anything, always sanity-check with:**
```powershell
git status
```
Real credential files (`.env`, `.env.maintainer`) should **never** appear
in the list — if they do, stop and check `.gitignore` before proceeding.

---

## 2. Cloud + local infrastructure

### 2.1 Cloud pilot project

- **Project:** `AlgoGuard`, Supabase project ref `gswyqmznjwbonassteco`
- **Region:** Tokyo (`ap-northeast-1`) — **deviation from the plan**, which
  specified Singapore for lower latency from Manila. Accepted for this
  pilot; see latency numbers below.
- **Connection type:** always use the **pooler** connection string
  (transaction mode, port `6543`), not the direct connection (port `5432`).
  The pooler behaves better from a laptop/local dev machine.
- **Measured latency** (Kalibo, PH → Tokyo pilot project, 10 samples):

  | Metric | Value |
  |---|---|
  | Min | 99.6 ms |
  | Median | 103.6 ms |
  | Avg | 103.5 ms |
  | Max | 109.6 ms |

  This is ~2–3x the plan's ~30–50ms estimate — but that estimate was for
  Singapore specifically. Not a problem for infrequent/admin operations;
  worth revisiting if a later stage assumes near-instant round trips in a
  synchronous request path.

### 2.2 Local Supabase stack (Docker)

A full local Auth/Data API/Postgres/Storage/Edge Functions stack runs via
Docker. Its database and credentials are separate from the cloud pilot project.
No cloud login, project linking, or remote deployment is needed for these tests.

**One-time setup on each Windows machine:**
1. Install and open [Docker Desktop](https://docs.docker.com/desktop/setup/install/windows-install/)
   with its WSL 2 backend. Complete its first-run prompts and wait until the
   engine is running. `docker info` must show a working server before continuing.
2. In the repository root, install the pinned project dependencies. The Supabase
   CLI is a development dependency in `package-lock.json`; use `npx supabase`
   rather than installing it globally. Node.js 20 or newer is required.
   ```powershell
   npm ci
   .\venv\Scripts\python.exe -m pip install -r requirements-dev.txt -r requirements-maintainer.txt
   ```
   If this clone has no virtual environment yet, create it first with
   `py -m venv venv`.
3. Prepare and start the isolated Stage 5A stack from the repository root:
   ```powershell
   .\scripts\prepare-local-5a.ps1
   npx supabase start --workdir .local/supabase-5a
   ```
   The helper copies `supabase/config.toml` and the smoke function to the ignored
   `.local/supabase-5a` directory, using project ID `AlgoGuard5A`. It does not copy
   later-stage migrations or seed files. `supabase init` is not needed.
   This stack uses the normal local ports below; stop another Supabase stack
   using those ports before starting it.
   First run downloads several GB of Docker images — can take 5–15+
   minutes, longer if you hit registry rate-limiting (see gotchas below).

**Local stack endpoints** (yours will show these when `supabase start`
finishes, or via `supabase status`):
- Studio (browser dashboard): `http://127.0.0.1:54323`
- API base: `http://127.0.0.1:54321`
- Direct DB connection: `postgresql://postgres:postgres@127.0.0.1:54322/postgres`

**To get current local credentials into a file** (regenerate any time —
these are throwaway per-install dev defaults, safe to view in plaintext):
```powershell
npx supabase status --workdir .local/supabase-5a -o env > .env.supabase.local
```
This file is auto-ignored by git (`.env.*` pattern).

### 2.3 Integration test suite (new)

Added `tests/integration/` — a separate test lane, **not** run by default
with the normal `pytest -q` command, since it requires the local Docker
stack to be running.

- `tests/integration/conftest.py` — loads credentials from
  `.env.supabase.local`.
- `tests/integration/test_local_supabase_stack.py` — proves each local
  service actually works (not just that containers are up):
  - **Data API (PostgREST):** creates a disposable table, inserts/selects
    a row over HTTP, drops the table.
  - **Auth:** signs up a disposable user (`algoguard.test+<uuid>@algoguard.invalid`),
    deletes it via the admin API afterward.
  - **Storage:** creates a private disposable bucket, uploads/downloads a
    small object, verifies bytes match, deletes both.
  - **Edge Functions:** calls a minimal smoke-test function
    (`supabase/functions/smoke_test`) and checks the response.
- `pyproject.toml` — registers the `integration` marker **and** excludes it
  from the default run via `addopts = "-m 'not integration'"`. (An earlier
  draft of this guide said `pytest.ini`; the settings went into
  `pyproject.toml` instead, and the exclusion was missing — so `pytest -q`
  was still *collecting* the integration tests and they skipped only
  because credentials were absent. The doc and the behavior now agree.)

**Serve the smoke function locally in a separate terminal:**
```powershell
npx supabase functions serve smoke_test --no-verify-jwt --workdir .local/supabase-5a
```
Leave this terminal running. The verification bypass applies only to this local
health-check function, which returns a fixed response and accesses no data.
`functions deploy` targets a remote project and is not part of this setup.

**Run the Stage 5A integration suite from another terminal in the repo root:**
```powershell
.\venv\Scripts\python.exe -m pytest -m integration tests/integration/test_local_supabase_stack.py -v -rs
```
Expected result: **4 passed**, not skipped. Missing `.env.supabase.local` values
cause skips; regenerate it only after the local start command succeeds. Targeting
this file keeps the check scoped to 5A while later-stage tests are under development.

When finished, stop the function server with Ctrl+C, then run
`npx supabase stop --workdir .local/supabase-5a`. Local database state is preserved; do not use `--no-backup`
or `db reset` as routine setup steps.

**Run the normal fast unit suite** (unaffected, still the default):
```powershell
python -m pytest -q
```

All test data created by the integration suite is prefixed
(`zz_migration_smoke_...`, `zz-smoke-...`, `algoguard.test+...@algoguard.invalid`)
so it's unmistakably disposable, and every test cleans up after itself.

---

## 3. Gotchas we hit (so you don't have to debug them again)

| Symptom | Cause | Fix |
|---|---|---|
| `pytest` not recognized in PowerShell | Venv `Scripts` not fully on PATH | Use `python -m pytest ...` instead of bare `pytest` |
| `ModuleNotFoundError: No module named 'config'` when running pytest | Bare `pytest` doesn't add cwd to `sys.path`; `python -m pytest` does | Always invoke via `python -m pytest` |
| Config test flakiness — e.g. `assert {5000} == {5077}` | `get_config()`'s cached singleton doesn't see `os.environ` changes made mid-test-run | Added an **autouse fixture** in `tests/conftest.py` that calls `reset_config_cache()` before/after every test |
| `train.py` CLI default not picking up a second `monkeypatch.setenv` in the same test | Same caching issue, but *within* one test body — the between-test fixture doesn't help there | Switched `_default_admin_username()` to call `load_config()` (uncached) instead of `get_config()`, since it's a rare, correctness-sensitive call, not a hot path |
| `npm install -g supabase` silently does nothing | Supabase CLI explicitly blocks global npm installs | Use Scoop (Windows) — see §2.2 |
| `supabase start` fails with `429 Too Many Requests` / DNS lookup errors mid-pull | AWS public ECR registry rate-limiting or a transient DNS hiccup | The CLI's built-in retry/backoff usually recovers on its own after a few minutes; no action needed unless it never recovers |
| `supabase start` reports an unexpected character while parsing `.env` | A bare URL or other non-assignment line was pasted into the file | Use `NAME=value` for each setting, or prefix notes with `#`. Keep `DATABASE_URL` in `.env.maintainer`, not `.env`. The CLI reads the repository's `.env` even when starting the local stack. |
| Starting from the repo root fails in a Stage 5B migration | The CLI automatically applies migrations when creating a local database | Use the isolated 5A working directory above for foundation tests. Resolve and verify the 5B migrations separately; a passing 5A smoke run does not validate them. |
| `psycopg2` connection to local stack fails: `server does not support SSL, but SSL was required` | The local Docker Postgres doesn't run SSL; the cloud pilot does | **Don't hardcode `prefer` for both.** `maintainer_env.resolve_sslmode()` now picks `prefer` for loopback and `require` for remote. `prefer` against the cloud means "silently fall back to plaintext" — the fix that unblocks local dev is a credential leak in production |
| Pasting a `postgresql://...` connection string into a browser does nothing | It's a database connection string, not a URL — browsers can't speak the Postgres wire protocol | Use a DB client (`psycopg2`, `psql`, DBeaver) or the Studio web UI (`http://127.0.0.1:54323`), never paste a `postgresql://` string into the address bar |
| `.env`/`.env.maintainer` files not being ignored by git | Filenames were missing their leading dot (`env.maintainer` instead of `.env.maintainer`) | Rename with the leading dot; re-check `git status` |

---

## 4. What's left in Stage 5A

Nothing. The stage is closed — see `docs/migration/05a-foundations.md` §6.

Optional, and not a gate on 5B: `05a-foundations.md` §5.2 repeats the measured
baseline on the Windows dev machine. §5.1 already records a full measured
baseline, so this confirms it on the primary platform rather than
establishing it.

**Next: Stage 5B — Identity, schema, and authorization.** The `models` bucket
exists with no policies, which is deliberate: 5B starts from "nothing is
permitted" and adds explicit grants, rather than starting open and trying to
close it afterwards.

Optional follow-up: `05a-foundations.md` §5.2 repeats the measured baseline on
the Windows dev machine. §5.1 already records a full measured baseline, so this
confirms it on the primary platform rather than establishing it.

**Closed since the first pass:**
- [x] Record baseline versions and runtimes — `05a-foundations.md` §5.1:
  323 passed / 17 skipped in 35.3 s, ruff clean, 10/10 Node.
- [x] Fix `tests/test_traffic_sources.py` hard-importing scapy, which made a
  missing capture stack abort the whole run instead of skipping one module.
- [x] Formal confirmation that test fixtures are disposable and
  distinguishable from real data — `05a-foundations.md` §4.2.
- [x] Confirm tests don't depend on execution order and don't impersonate
  ordinary users through owner credentials — §4.3 and §4.4.
- [x] Write `docs/migration/05a-foundations.md`.
- [x] Document local state locations (outbox, node identity, model cache,
  temporary user sessions) — §2.4.

**After 5A closes**, next is **Stage 5B — Identity, schema, and
authorization**: the real versioned Supabase schema, Auth configuration,
and row-level security policies. That's a substantially bigger effort than
5A and should not be started until 5A's checklist is fully green.

---

## 5. Key files reference

```
AlgoGuard/
├── config.py                              # centralized typed configuration (analyst side)
├── maintainer_env.py                      # .env.maintainer loader + explicit sslmode
├── redaction.py                           # secret redaction, shared by both sides
├── pyproject.toml                         # pytest markers + integration exclusion + ruff
├── requirements.txt                       # analyst runtime
├── requirements-dev.txt                   # + pytest, ruff (fast unit lane)
├── requirements-maintainer.txt            # psycopg2, dotenv, requests (privileged + integration)
├── check_db_connection.py                 # MAINTAINER ONLY
├── measure_latency.py                     # MAINTAINER ONLY
├── .env.example                           # analyst template (committed)
├── .env                                   # analyst real values (gitignored)
├── .env.maintainer.example                # maintainer template (committed)
├── .env.maintainer                        # maintainer real values (gitignored)
├── .env.supabase.local                    # local stack credentials (gitignored)
├── supabase/
│   └── functions/smoke_test/index.ts      # disposable Edge Function smoke test
├── tests/
│   ├── conftest.py                        # unit test fixtures + config cache reset
│   └── integration/
│       ├── conftest.py                    # loads local stack credentials
│       └── test_local_supabase_stack.py   # Auth/Data API/Storage/Functions tests
└── docs/migration/
    └── 05a-foundations.md                 # Stage 5A evidence doc
```
