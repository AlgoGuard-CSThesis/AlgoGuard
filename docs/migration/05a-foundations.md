# Stage 5A — Configuration and Test Infrastructure

**Stage:** Iteration 5, Stage A
**Reference:** `AlgoGuard Migration (backlog).md` §5A
**Companion:** `README_STAGE_5A.md` (setup walkthrough and gotchas)

**Stage exit criterion.** *The current local app remains usable, configuration is
explicit, and a disposable Supabase stack can exercise the complete future
application path.*

---

## 1. Scope

Stage 5A touches no application data and wires nothing to the cloud. It does two
things: makes configuration explicit and validated, and stands up disposable
infrastructure so later stages can be tested without risking real data.

The Flask application still persists exclusively to local SQLite and behaves as
it did at the close of Iteration 4.

---

## 2. Architecture delivered

### 2.1 Configuration (`config.py`)

A single typed, frozen `AppConfig` dataclass replaces the environment reads that
were previously scattered across `app.py`, `services/database_service.py`,
`services/deployment_service.py`, `services/traffic_source_service.py`, and
`train.py`.

Precedence, highest to lowest:

1. Process environment (`os.environ`) — shell exports and test fixtures
2. Values loaded from `.env` via python-dotenv (never overrides the above)
3. Built-in defaults matching Iteration 4's hardcoded behavior

`get_config()` returns a cached singleton; `load_config()` always re-reads;
`reset_config_cache()` clears the cache and is called by an autouse fixture in
`tests/conftest.py` before and after every test.

**Validation.** Invalid configuration raises `ConfigError` at load time rather
than silently degrading:

| Setting | Rule |
| --- | --- |
| Booleans | `1/true/yes/on` and `0/false/no/off` (case-insensitive). Anything else is an error. |
| `ALGOGUARD_PORT` | Must parse as an integer in 1–65535. |
| `ALGOGUARD_MIN_STACKING_*` | Non-finite or unparseable values fall back to the clamped default; valid values clamp to 0–100. |
| `ALGOGUARD_DB_MODE` | Must be `sqlite` or `supabase`. |
| `ALGOGUARD_DB_MODE=supabase` | Requires both `NEXT_PUBLIC_SUPABASE_URL` and `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY`, **and** is then refused outright until Stage 5D (see §2.3). |

### 2.2 Credential separation

| File | Committed | Loaded by | Contains |
| --- | --- | --- | --- |
| `.env.example` | yes | — | Analyst template |
| `.env` | no | `config.py` (application) | Project URL, publishable key, local options |
| `.env.maintainer.example` | yes | — | Maintainer template |
| `.env.maintainer` | no | `maintainer_env.py` (privileged tooling only) | Pooler DSN, secret key, JWKS URL |
| `.env.supabase.local` | no | `tests/integration/conftest.py` | Throwaway local-stack credentials |

`maintainer_env.py` loads `.env.maintainer` by explicit path and **never** falls
back to `.env`. The Flask application must not import it.

This closed a real defect: `check_db_connection.py` and `measure_latency.py`
previously called a bare `load_dotenv()`, which loads the analyst `.env`. That
required `DATABASE_URL` — a maintainer credential — to be written into the file
that ships to analyst installations, contradicting the separation this stage
exists to establish.

**Redaction.** `redaction.redact_for_logging()` strips passwords out of
connection strings and values out of `token`/`secret`/`key`/`password`/
`signature` query parameters. It is wired into the error paths of both
maintainer scripts. It lives in its own module rather than in `config.py`
specifically so maintainer tooling can import it without `config.py`'s
module-level `load_dotenv()` pulling analyst configuration into the process.
`config.py` re-exports it for backward compatibility.

**TLS.** `maintainer_env.resolve_sslmode()` selects an explicit libpq mode per
target: `require` for remote hosts, `prefer` only for loopback (the local Docker
Postgres serves no TLS at all). `ALGOGUARD_DB_SSLMODE` overrides both and is
validated against the libpq mode list.

This replaced a hardcoded `sslmode="prefer"` in both scripts. `prefer` means
"attempt TLS, silently continue in plaintext if the server declines" — correct
for the local stack, but against the cloud pilot it would place the database
password on the public internet in cleartext whenever negotiation failed.

### 2.3 Cloud mode is validated but refused

`db_mode` is parsed and its credential requirements enforced, but **no code path
consumes it** — the repository layer is Stage 5D work. Accepting
`ALGOGUARD_DB_MODE=supabase` and then running on SQLite anyway would be exactly
the silent fallback §5A.2 forbids, so `load_config()` raises `ConfigError` for
that mode until 5D lands. Remove the refusal in 5D.1, not before.

### 2.4 Local state locations

Required by §5A.2. "Planned" entries do not exist yet and are recorded here so
5D implements them in known places.

| State | Location | Status |
| --- | --- | --- |
| Business database | `database/algoguard.sqlite3` (`ALGOGUARD_DATABASE_PATH`) | Current |
| Active model artifact | `saved_models/deployed_model.<uuid>.joblib` (`ALGOGUARD_DEPLOYED_MODEL_PATH`) | Current |
| Per-run model artifacts | `saved_models/run_<run_id>/<model_id>.joblib` | Current |
| Training reports | `reports/training_run_<run_id>_model_results.csv` | Current |
| Packet captures | `captures/*.pcap`, `*.pcapng`, `*.cap` | Current |
| Temporary user sessions | Flask signed cookie; key from `ALGOGUARD_SECRET_KEY`, ephemeral per process when unset (restart invalidates sessions). No server-side store. | Current |
| Pending-event outbox | Separate local SQLite spool, distinct from the business database | Planned — 5D.2 |
| Node identity | Local, issued at enrollment | Planned — 5B.3 / 5D.1 |
| Model cache | Local cache of downloaded published artifacts, atomically promoted | Planned — 5C.3 |

Superseded `deployed_model.<uuid>.joblib` files and old `run_<id>/` directories
are currently never pruned. Not a 5A obligation; tracked for 5C.3, which
introduces cache promotion and will need a retention rule.

---

## 3. Infrastructure

### 3.1 Cloud pilot project

| Property | Value |
| --- | --- |
| Project ref | `gswyqmznjwbonassteco` |
| Region | Tokyo (`ap-northeast-1`) |
| Connection | Pooler, transaction mode, port 6543 |
| TLS | `require` (explicit, via `resolve_sslmode`) |

**Deviation — accepted.** The plan specified Singapore. Tokyo was provisioned
instead. Measured round-trip from Kalibo, PH (10 samples): min 99.6 ms, median
103.6 ms, mean 103.5 ms, max 109.6 ms — roughly 2–3× the plan's 30–50 ms
Singapore estimate.

**Consequence for 5D.** This is acceptable for infrequent maintenance and admin
operations. It is *not* acceptable on a synchronous capture path:
`live_monitor_service._run_worker()` currently calls `_store_flow()` inline for
every eligible flow, and at ~100 ms per round trip that would stall the monitor.
The bounded local outbox specified in §5D.2 is therefore a hard requirement, not
an optimization. Re-measure if the project is later moved to Singapore.

### 3.2 Local Supabase stack

Full Auth / Data API / Postgres / Storage / Edge Functions stack via Docker and
the Supabase CLI, with no network path to the cloud pilot. Setup steps and
platform gotchas are in `README_STAGE_5A.md` §2.2 and §3.

### 3.3 Private model bucket

| Property | Value |
| --- | --- |
| Bucket name | `models` |
| Visibility | Private (public access off) |
| Policies | **None** — access stays denied until 5B installs them |
| Location | Storage in the pilot project (`gswyqmznjwbonassteco`) |

The name is deliberately unqualified: the Supabase project already scopes it to
AlgoGuard, so `algoguard-models` would repeat itself. Stage 5C refers to this as
"the model bucket"; 5B's RLS policies will reference `models` by that name.

Deny-by-default is the point of creating it now and leaving it empty. A private
Supabase bucket with zero policies rejects every request that is not made with
the service-role key, which means 5B starts from "nothing is permitted" and adds
explicit grants, rather than starting from open access and trying to close it.

**Created 2026-09-22.** Bucket exists, public access off, no policies attached.
5B installs the first policies against this bucket; until then every request
without the service-role key is denied.

---

## 4. Test isolation

### 4.1 Two lanes

| Lane | Command | Requires | Contents |
| --- | --- | --- | --- |
| Fast unit | `python -m pytest -q` | `requirements-dev.txt` | 340 tests across 19 modules, plus the Node monitor test |
| Integration | `python -m pytest -m integration -v` | Docker, `supabase start`, `requirements-maintainer.txt` | `tests/integration/` |

### 4.0 Optional dependencies never break collection

`services/traffic_source_service` imports scapy lazily so the web application
starts, and both replay modes keep working, on a machine with no capture stack.
`tests/test_traffic_sources.py` did not honor that contract: a module-level
`import scapy.all` meant that without scapy pytest failed during **collection**
and aborted the entire run — zero results rather than every other test still
reporting. It now uses `importorskip`, matching `tests/integration/`.

Verified in both directions (§5.1): with scapy, 323 pass; without it, 289 pass
and one more module skips — the run survives either way.

The integration lane is **excluded** from the default run via
`addopts = "-m 'not integration'"` in `pyproject.toml`, not merely skipped, so
`pytest -q` is honest about what it covered. A command-line `-m` overrides
`addopts`, so `pytest -m integration` still selects exactly that lane.

> Previously the marker was registered without `addopts`, so the default run
> collected the integration tests and they skipped only because credentials were
> absent. The documented behavior and the actual behavior now match.

### 4.2 Disposability (§5A.3)

All integration fixtures are uniquely named per run and unmistakably
distinguishable from real data:

| Resource | Naming | Cleanup |
| --- | --- | --- |
| Tables | `zz_migration_smoke_<8 hex>` | `drop table … cascade` in `finally` |
| Storage buckets | `zz-smoke-<8 hex>` | object + bucket deleted in `finally` |
| Auth users | `algoguard.test+<8 hex>@algoguard.invalid` | deleted via admin API |

`.invalid` is reserved by RFC 2606 and can never resolve to a real mailbox.

### 4.3 Ordering independence (§5A.4)

Every integration test derives its resource names from a fresh `uuid4()` and
cleans up in a `finally` block. No test reads state another test created, and
none depends on execution order.

### 4.4 No impersonation through owner credentials (§5A.4)

Verified by inspection of `tests/integration/test_local_supabase_stack.py`:

- **Data API** — the `anon` publishable key is used for the insert/select that
  represents ordinary user traffic. The service-role DSN is used only for DDL
  (create table, grants, RLS policy), which is administrative by nature.
- **Auth** — signup runs with the publishable key, exactly as a real user would.
  The secret key appears only in the admin delete used for cleanup.
- **Storage** — bucket and object operations are admin-side setup, not a
  simulation of user access.

No test performs an ordinary-user action while holding owner credentials.
Once RLS lands in 5B, policy tests must use real per-user tokens; the
service-role key must never be used to assert that a policy works.

---

## 5. Baseline evidence

### 5.1 Reference environment (measured)

Commit `f0b6628` plus the Stage 5A changes, in a Linux container. Every number
below was produced by running the command, not estimated.

| Component | Version |
| --- | --- |
| Python | 3.11.15 |
| pytest | 9.0.3 |
| ruff | 0.15.11 |
| Node | 22.22.2 |
| Flask | 3.1.3 |
| scikit-learn | 1.8.0 |
| pandas | 3.0.2 |
| NumPy | 2.4.4 |
| scapy | 2.6.1 |

| Check | Result | Runtime (median of 3) |
| --- | --- | --- |
| `python -m pytest -q` | **323 passed, 17 skipped** | 35.3 s |
| `node --test tests/monitor_poll.test.cjs` | **10 passed, 0 failed** | 0.2 s |
| `ruff check .` | **All checks passed** (37 files) | 0.02 s |

Of the 17 skips, 16 are `tests/test_maintainer_env.py` (no `psycopg2` in the
reference container) and 1 is `tests/integration/`. On a machine with
`requirements-maintainer.txt` installed the first 16 run, giving 339 passed.
Nothing errors; every skip is a declared optional dependency.

The suite grew from the 197 recorded at 5A.1 to 340 collected, the increase
being `test_config.py`, `test_redaction.py`, `test_maintainer_env.py` and
`test_stack_support.py` — permanent coverage for the modules this stage added.

Lane selection verified by execution, not inference:

| Command | Collected | Deselected |
| --- | --- | --- |
| `pytest -q` | 323 unit tests | integration lane |
| `pytest -m integration` | integration lane only | all 323 |

Dependency degradation verified in both directions:

| Environment | Result |
| --- | --- |
| scapy present | 323 passed, 17 skipped |
| scapy absent | 289 passed, 18 skipped — the module skips, the run survives |

### 5.2 Development machine (Windows) — to record

§5.1 is a second data point, not a substitute for the primary development
environment, which runs Windows on Python 3.14. Run these and add a row set:

```powershell
python --version ; python -m pytest --version ; python -m ruff --version ; node --version
python -m ruff check .
Measure-Command { python -m pytest -q } | Select-Object TotalSeconds
python -m pytest -q
node --test tests/monitor_poll.test.cjs
```

| Measurement | Value |
| --- | --- |
| Python version | _pending_ |
| pytest / ruff / Node versions | _pending_ |
| `ruff check .` | _pending_ |
| Unit suite result and runtime | _pending_ |
| Node monitor tests | _pending_ |
| Integration lane (`-m integration`, stack running) | _pending_ |

### 5.3 Additional functional checks

Beyond the test suite, run against the same tree:

| Check | Result |
| --- | --- |
| `compileall` over all tracked Python | pass |
| Import of all 11 services + `app` + `config` + new modules | pass |
| `train.py <csv> --deploy` end to end (6 models, quality gate, deployment) | pass |
| Flask: auth gating, wrong password and unknown user rejected, CSRF rejection, CSRF rotation on login, stale-token rejection at logout, 6 authenticated pages, `POST /predict` | 19/19 pass |
| Open-redirect guard (`//evil.com`, `http://…`, backslash paths) | rejected |
| `ConfigError` for bad boolean, bad port, out-of-range port, incomplete and complete supabase mode | pass |
| Blank-value fallback across 14 settings | pass |
| `resolve_sslmode` across 9 DSN forms incl. keyword-form and malformed | pass |
| `redact_for_logging` across 14 credential shapes | no leaks, no over-redaction |
| Flow tracker: TCP teardown, ACK drain, idle expiry | pass |

---

## 6. Stage 5A completion

| Task | State |
| --- | --- |
| 5A.1 Typed configuration | Complete |
| 5A.2 Analyst/maintainer separation | Complete |
| 5A.3 Isolated cloud and local test infrastructure | Complete |
| 5A.4 Baseline evidence and test isolation | Complete; §5.2 optional on Windows |

**Stage 5A is complete.** The exit criterion — *the current local app remains
usable, configuration is explicit, and a disposable Supabase stack can exercise
the complete future application path* — is met, with the suite green and the
private `models` bucket in place.

§5.2 asks for the same measurements repeated on the Windows development
machine. §5.1 already records a full, reproducible measured baseline, so this
confirms the numbers on the primary platform rather than establishing them; it
does not gate 5B.

### 6.1 Fixes applied while closing the stage

The configuration centralization was extended late in the stage — capture,
report and saved-model folders moved into `config.py`, and `app.py`,
`migrate.py`, `database_service.py`, `deployment_service.py`,
`live_monitor_service.py` and `traffic_source_service.py` were changed to read
configuration at use time. That refactor left three defects, fixed here:

| Defect | Fix |
| --- | --- |
| `tests/test_traffic_sources.py` patched `monitor.CAPTURE_FOLDER`, a constant the refactor had deleted — two tests errored | Both now set `ALGOGUARD_CAPTURE_FOLDER` and reset the config cache, matching how `conftest.py` already redirects runtime paths |
| `build_capture_filter()` defaulted to `exclude_ports=()`, so calling it with no arguments excluded nothing — its docstring's "by default" was only true because `LiveCaptureSource` passed the port in itself | The default is now `None`, meaning "apply the default exclusion", resolved from current configuration. An explicit empty collection still builds an unfiltered capture |
| Two `I001` unsorted-import errors in `services/database_service.py` and `train.py` | `ruff check --fix` |

Stage 5B may begin.

---

## 7. Carried into later stages

Found during Stage 5A, out of scope here, recorded so they are not rediscovered:

| Finding | Where it belongs |
| --- | --- |
| Tokyo latency (~104 ms median) makes the synchronous `_store_flow()` call in the monitor loop unviable against the cloud. The bounded outbox is a hard requirement, not an optimization. | 5D.2 |
| Superseded `deployed_model.<uuid>.joblib` files and per-run `saved_models/run_<id>/` directories are never pruned — roughly 50 MB after two training runs. | 5C.3, which introduces cache promotion and needs a retention rule |
| `ALGOGUARD_HOST` accepts any string. A non-loopback value silently exposes the prototype; today only the default and operator discipline prevent that. | 5D.4 / 6C, alongside the cloud-exclusion work |
| The individual-model ranking penalises the best detector. See below. | 6A.4 (score presentation), or sooner if the thesis cites the ranking |

### 7.1 The individual-model ranking rewards being cheap over being correct

`evaluation_service` min-max normalizes nine metrics and averages them with
equal weight. Four of the nine — false-positive rate, CPU, RAM, model size —
reward a model for being small and for rarely predicting "Attack".

Run against the measured metrics in `research/results/model_ranking.csv`:

| Current rank | Model | Accuracy | Recall | Size |
| --- | --- | --- | --- | --- |
| 1 | KNN | 90.7% | 89.4% | 11.8 MB |
| 2 | Gradient Boosting | 92.0% | 89.4% | 0.13 MB |
| 3 | AdaBoost | 87.6% | 85.7% | 0.06 MB |
| **4** | **Random Forest** | **93.2%** | **92.6%** | 52.5 MB |
| 5 | Naive Bayes | 51.2% | 16.4% | 0.001 MB |

Two problems, and the second is the serious one:

1. **Naive Bayes is flattered by its own uselessness.** It catches 16% of
   attacks because it answers "Normal" to nearly everything. That yields a low
   false-positive rate and a 1 KB model — three of the nine metrics — so a
   classifier barely better than a coin flip does not finish far last.

2. **Random Forest has the highest accuracy AND the highest recall, and ranks
   fourth.** For an intrusion detection system recall is the cost that matters:
   it is the fraction of real attacks seen. The formula ranks the best detector
   below three worse ones because it is 52 MB — on a project whose stated goal
   is detection for resource-constrained organizations, size is a real
   constraint, but it should not outvote catching attacks.

Options evaluated on the same data:

| Scheme | Resulting order |
| --- | --- |
| Current (equal weight, 9 metrics) | KNN, GB, AdaBoost, **RF**, NB |
| Viability gate (drop recall < 50%), same formula | GB, **RF**, KNN, AdaBoost |
| Weighted 70% performance / 30% efficiency | GB, KNN, AdaBoost, **RF**, NB |
| Performance ranks, efficiency reported beside it | GB, **RF**, KNN, AdaBoost, NB |

The last two rows are the defensible ones, and the final row matches how
`research/results/` already presents this — `final_table1_performance.csv` and
`final_table2_efficiency.csv` are separate tables. The application's single
blended score is what diverges from the research method, not the reverse.

Note this affects the reported comparison only: the deployed model is always the
Stacking Ensemble, chosen by the quality gate, never by this ranking.
