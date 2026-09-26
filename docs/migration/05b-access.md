# Stage 5B — Identity, Schema, and Authorization

**Stage:** Iteration 5, Stage B
**Reference:** `AlgoGuard Migration (backlog).md` §5B
**Status:** 5B.1–5B.5 implemented and verified locally on 2026-09-26.
Cloud application integration remains Stage 5D.

**Stage exit criterion.** *Cloud access is protected from its first usable
schema, with authenticated API transactions and approved user-to-node
assignments.*

---

## 1. Scope

5B creates the real cloud schema and the permissions that govern it. The
Flask application still runs on local SQLite throughout: nothing in this
stage is wired into the app, which happens in 5D. What 5B produces is a
database that would be safe to point an application at.

| Task | Deliverable | Status |
|---|---|---|
| 5B.1 | Versioned cloud schema migrations | Delivered — §2, §3 |
| 5B.2 | Auth configuration and protected account roles | Delivered — §4 |
| 5B.3 | Approved enrollment and account administration | Verified — §6.5 |
| 5B.4 | Access enforcement across the relational schema | Verified — §6.6 |
| 5B.5 | Repository contracts and transactional API functions | Verified — §6.7–6.8 |

---

## 2. 5B.1 — Migration tooling

### 2.1 Why there is a runner at all

The Supabase CLI owns the migration file format and the local workflow.
It does not verify that an already-applied file still contains what it
contained when it was applied, and it does not serialise two people
pushing at once. 5B.1 requires both, so `cloud_migrate.py` adds exactly
those two things and leaves everything else to the CLI:

- **Checksums.** Each file is hashed (SHA-256, with line endings and
  trailing blank lines normalised so a Windows checkout does not look
  like tampering). A file edited after it was applied stops the run with
  the message to write a new migration instead.
- **Serialisation.** Each file is applied inside one transaction that
  first takes `pg_advisory_xact_lock`. A second runner waits rather than
  interleaving DDL, and re-checks under the lock in case the first runner
  applied the file while it waited.
- **Rollback.** One file, one transaction. A failure leaves nothing from
  that file behind. Migration files therefore contain no `BEGIN`/`COMMIT`
  and no `CREATE INDEX CONCURRENTLY`; `tests/test_cloud_migrate.py`
  enforces both against the real migrations directory.

**Transaction-scoped, not session-scoped locks.** The maintainer
`DATABASE_URL` points at the transaction pooler (5A.1). A session
advisory lock there can be taken on one backend and released on another.
A transaction-scoped lock is held for exactly the transaction that does
the work, which is the only unit the pooler preserves.

### 2.2 Two ledgers, kept in step

The CLI records applied versions in
`supabase_migrations.schema_migrations`; the runner records version, name
and checksum in `private.algoguard_migration`. Drift between them would
mean a migration applied twice or skipped, so:

- when the runner applies a file, it writes to **both** tables, so
  `supabase db push` will not re-apply it;
- when the runner starts and finds versions the CLI already applied (the
  usual case after `supabase db reset` locally), it **adopts** them —
  recording the file's current checksum and printing which ones. A later
  edit to an adopted file is still caught.

### 2.3 Remote safety

Applying to anything other than the local stack requires `--yes`. An
unparseable DSN counts as remote, so the failure mode is an extra
confirmation rather than an unattended change to the cloud pilot.

---

## 3. 5B.1 — Schema

### 3.1 Deny-by-default comes first

`20260922010000_baseline_deny_by_default.sql` runs before any table
exists. It removes the API roles' privileges on `public`, and — the part
that matters — strips the **default** privileges, so every table created
by later migrations is born unreachable. Both layers are used throughout:
privileges removed *and* RLS enabled with no policies. A mistake in one
is not enough to expose evidence.

A `private` schema, absent from `[api] schemas` in `supabase/config.toml`,
holds migration bookkeeping and the authorization helpers added in 5B.2.

### 3.2 Table map

| SQLite (Iteration 4) | Cloud | Scope |
|---|---|---|
| `admin` | `profile` + `user_role` | global; credentials live in Auth |
| — | `node`, `node_membership` | new: installation identity and approved binding |
| `network_traffic` | `network_traffic` | node |
| `prediction` | `prediction` | node |
| `alert` | `alert` | node |
| `capture_session` | `capture_session` | node |
| `system_log` | `system_log` | node, or NULL for maintainer actions |
| `report` | `report` | node, or NULL for cross-node (Administrator) |
| `report_alert` | `report_alert` | inherited |
| `training_run` | `training_run` | global (maintainer-only) |
| `detection_model` | `detection_model` | global |
| `model_deployment` | `model_deployment` | global |
| — | `ingest_event` | new: outbox deduplication anchor |
| — | `model_manifest`, `deployment_activation_lock` | new: 5C publication |

### 3.3 Port rules

- Primary keys and counters are `BIGINT`: SQLite `INTEGER` is 64-bit and
  a Postgres `integer` would narrow the range at import.
- Identity columns are `GENERATED BY DEFAULT`, so 5E's import carries the
  original identifiers and every existing relationship across.
- Legacy timestamps keep their names, their `text` type and their
  canonical UTC text; legacy `0/1` flags stay integer with a `CHECK`.
  New lifecycle columns use `timestamptz`.
- JSON-bearing columns stay `text`. `jsonb` would reject any legacy row
  that is not valid JSON, turning a fidelity problem into a failed
  cutover.
- State columns are `text` + `CHECK`, not enums: enum values cannot be
  removed, and `ALTER TYPE ... ADD VALUE` fights the one-file-one-
  transaction rule.
- `profile` has no password column. Auth owns credentials; copying
  `admin.password_hash` into the cloud would create a second, unmanaged
  credential store.

### 3.4 Cross-node links are refused structurally

Every node-scoped table carries `node_id` and a `UNIQUE (pk, node_id)`.
Child rows reference the **pair**, so a prediction cannot point at
another node's traffic even with full privileges and a wrong policy. 5B.4
adds the policies; this is the layer underneath them.

Known and accepted: `report` and `system_log` allow a NULL `node_id` for
cross-node and maintainer rows. A composite foreign key with a NULL
column is satisfied vacuously, so the scope of those rows is a policy
decision (5B.4), not a constraint one.

---

## 4. 5B.2 — Auth and protected roles

### 4.1 Nobody signs themselves up

`supabase/config.toml` sets `enable_signup = false` under `[auth]` and
`enable_signup = true` under `[auth.email]`. **Correction from live evidence:**
CLI 2.117.0 maps the latter to `GOTRUE_EXTERNAL_EMAIL_ENABLED`; setting it
false also refuses existing users' password login with HTTP 422
`email_provider_disabled`. The running container now has
`GOTRUE_DISABLE_SIGNUP=true` and `GOTRUE_EXTERNAL_EMAIL_ENABLED=true`.
Public signup refusal and existing-user login are tested independently.
`minimum_password_length` is raised
from the template's 6 to 12, with
`password_requirements = "lower_upper_letters_digits_symbols"`.

Accounts come from two places instead:

- **The first Administrator:** `bootstrap_admin.py`, over the trusted
  maintenance connection. It generates a password per installation and
  prints it once — there is no shared default password to become the
  credential everybody knows.
- **Everyone after that:** the checked Edge Function in 5B.3.

`bootstrap_admin.py` touches two systems (Auth over HTTPS, then the
database), so a run can die between them. Every step is keyed on the
email address and the Auth user id, so re-running completes the missing
half rather than creating a second account. `tests/test_bootstrap_admin.py`
exercises that recovery path with a stand-in session and connection.

### 4.2 Tokens are verified, not trusted

`token_verification.py` checks access tokens against the project's
published JWKS. The reason for asymmetric signing is the whole point of
the task: with the legacy shared HS256 secret, every installation able to
*verify* a token is equally able to *mint* one, so an analyst laptop
would hold the key to impersonate anybody.

The verifier refuses, with a test for each:

| Refusal | Why it is in the list |
|---|---|
| `HS256` and every other symmetric algorithm | Algorithm confusion: sign a forged token with the published public key as an HMAC secret |
| `alg: none` | The oldest JWT bug there is |
| An `oct` key in the published key set | Defence in depth if the allowlist is ever widened |
| Missing or unknown `kid` | Unknown triggers at most one refresh, rate-limited |
| Wrong `iss` or `aud` | A token from another project or service |
| Missing `exp`, `iat`, `sub`, `aud`, `iss` | Each is required explicitly, not assumed present |

Expiry is reported as `TokenExpired`, separately from `TokenRejected`, so
5D can refresh once and stop capture if that fails. A JWKS that cannot be
fetched raises `SigningKeysUnavailable`: "we could not check" must never
be reported as "we checked and it was bad".

Key rotation needs no deployment: an unknown `kid` costs exactly one
refresh, verified by `tests/test_token_verification.py`, which also
proves a stream of forged key ids does **not** become a stream of
outbound requests.

**Recorded lifetimes.** `jwt_expiry = 3600` (one hour); refresh token
rotation on, with a 10-second reuse interval for a lost response. 5D
holds access and refresh tokens in process memory only; the browser
cookie carries an opaque session id, never a token. Restart therefore
requires an online login, which is the behaviour the plan's operating
table already specifies.

**No signing secret on an analyst installation.** `.env.example` gains no
new setting: the issuer and JWKS URL are derived from the project URL
that is already there.

### 4.3 The database decides what you may do

Three helpers in `private` — `current_profile_id()`, `has_role()`,
`is_administrator()` — read `public.user_role`. They are `SECURITY
DEFINER` (so a policy calling them cannot recurse into the policy that
protects `user_role`) with `set search_path = ''` and every object
written out in full, because a definer function with a caller-controlled
search path is a privilege-escalation hole.

They are granted to `authenticated` so policies can call them, and live
in a schema the Data API does not serve, so nobody can call them as an
RPC to ask about other people.

Two structural guards back this up, each true even if a policy is wrong:

- `user_role` rejects a grant whose `granted_by` is the same profile
  (self-promotion), and any grant naming somebody who is not *currently*
  an Administrator. `granted_by IS NULL` means trusted maintenance, which
  the API roles have no privileges to reach.
- `profile.auth_user_id` cannot be reassigned once set. Re-pointing a
  profile at a different Auth user would silently transfer everything
  attributed to that person, including their evidence.

**Accepted consequence.** An Administrator cannot change roles on their
own account at all, not even to add one: that needs another
Administrator or trusted maintenance. Self-service privilege change is
the thing the rule exists to prevent, and the exception would be
indistinguishable from the attack.

### 4.4 Asymmetric signing: where it is switched on

- **Cloud pilot:** in the dashboard (Auth → JWT keys), migrating the
  project to ES256.
- **Local stack:** in **cmd.exe**, run `npx supabase gen signing-key --algorithm
  ES256 > supabase/signing_keys.json`. CLI 2.117.0 emits one JWK object,
  but its config loader requires an array: wrap that object in `[...]`.
  `check_5b.cmd` generates and wraps the ignored key automatically if absent.
  `signing_keys_path` is now enabled in `supabase/config.toml`; manual startup
  requires the same local setup. The key never enters an analyst package.

CLI **2.117.0** was checked on 2026-09-25: `gen signing-key` exists and emits
the key to stdout. Its `-o` option selects an output format, not a destination
file. The older command using `-o supabase/signing_keys.json` is invalid.
No private signing key was generated during the blocked 2026-09-25 run.
An ignored ES256 key was generated locally on 2026-09-26.

The local stack now publishes an ES256 public JWK. The verifier continues
to refuse HS256. A legacy stack without public keys skips the three asymmetric
integration checks; such skips cannot close Stage 5B.

---

## 5. Permission matrix

Anonymous callers have no application-table privileges. Trusted maintenance
uses its direct connection; the hosted service credential is limited to the
checked administration path by application design and never reaches clients.
`read` below requires an active profile and current protected role where noted.

| Object | Analyst | Administrator | Policy / restriction |
|---|---|---|---|
| `profile` | Read self | Read all | `profile_read`; identity mutations only through checked administration |
| `user_role` | Read self | Read all | `role_read`; no direct mutations or self-promotion |
| `node` | Read approved assigned nodes | Read all | `node_read`; enrollment/decisions use checked functions |
| `node_membership` | Read own, including pending | Read all | `membership_read`; no direct mutations |
| `capture_session` | Read/insert assigned; update own lifecycle/count fields | Read all; same write scope | `node_read`, `node_insert`, `session_update`; scope/owner columns immutable to API |
| `network_traffic` | Read/insert assigned | Read all; insert assigned | `node_read`, `node_insert`; append-only |
| `prediction` | Read/insert assigned | Read all; insert assigned | Same; node+owner relationships constrained |
| `alert` | Read/insert assigned; update status/description | Read all; same write scope | Same plus `alert_update`; identity immutable |
| `system_log` | Read/insert assigned, own attribution | Read all including NULL-node; insert assigned | `log_read`, `log_insert`; append-only |
| `report` | Read/insert assigned; delete own | Read all including NULL-node; same write scope | `node_read`, `node_insert`, `report_delete`; cross-node creation reserved for maintenance/future checked path |
| `report_alert` | Read accessible report+alert; insert/delete own report links within assigned node | Read all accessible links; same write scope | `report_link_read/insert/delete`; NULL-node API inserts denied; independent existence FKs close NULL composite-FK holes |
| `ingest_event` | Read/insert assigned, own attribution | Read all; same insert scope | `node_read`, `node_insert`; no update/delete; 5B.5 adds full-group validation |
| `training_run` | Read safe metadata | Same | `training_read`; column grants omit stored path/error details |
| `detection_model` | Read safe metadata | Same | `model_read`; artifact path/error details not granted |
| `model_deployment` | Read safe metadata | Same | `deployment_read`; artifact path not granted |
| `model_manifest` | Read published/active/superseded | Same | `manifest_read`; no API publication mutations |
| `deployment_activation_lock` | None | None | RLS enabled, zero API grants/policies |
| `training_summary`, `model_summary`, `deployment_summary` | Read | Read | Explicit `security_invoker=true`; underlying column grants and RLS preserved |
| `storage.objects` in private `models` bucket | Read objects with a readable manifest | Same | `model_object_read`; no authenticated insert/update/delete policy |
| Private migration/request ledgers | None | None | Unexposed schema; no API grants |

Function permissions are explicit: `request_enrollment` and
`administration_authorized` are executable by authenticated callers;
`administration_command` is service-role-only and checks the current actor.
Authorization helpers have per-function authenticated EXECUTE in the unexposed
`private` schema. Anonymous callers have none. The identity owner has explicit
RLS policies only on identity tables and its request ledger. Other definer
functions cannot inherit an unrestricted schema search path.

Ordinary writes require an active protected role, an approved membership, and
an approved node. Administrator read-all does not satisfy write checks. Owner
fields must identify the caller, and composite node/owner foreign keys refuse
cross-node and cross-owner flow relationships. Evidence/audit records are
append-only; DELETE is deliberately limited to owned reports and their links.
Raw hidden reads return `[]`; mutation errors reveal no hidden row contents.

### 5B.3 Enrollment and hosted account administration

`node_enrollment.py` publishes one persistent UUID using an atomic link, even
under concurrent startups. Invalid existing identity files fail explicitly.
`select_node` defaults to the local installation, permits an explicit approved
alternative, and never substitutes a different node when local approval is absent.
This client convenience does not authorize writes; current database records do.

`request_enrollment` creates a pending node/membership idempotently. It returns
only the caller's membership state, never another node's identifying details.
Membership states are pending/approved/rejected/revoked. The account function
supports approval, rejection, revocation, atomic reassignment, and one preferred
membership alongside multiple approved memberships. Repeated enrollment cannot
undo an Administrator's rejection or revocation.

`account_admin` verifies the bearer with Auth `/user`, checks
`administration_authorized`, and calls the service-only `administration_command`.
The SQL function checks the actor's active protected Administrator record again,
including on every retry. JWT role metadata never supplies authorization.
Malformed, unauthorized, and conflicting requests receive controlled 400/401/403/409
responses; temporary upstream failures use 503. No raw upstream error is returned.

Account creation uses a request UUID and a private request ledger without
passwords. An Auth user carries a server-owned `algoguard_request_id` marker.
After an interrupted creation, the Edge Function recovers only an account with
the same email and marker; it never adopts an unrelated account. A completed
retry returns the original result. Retrying does not change the password.
Recovery scans are bounded to 4,000 users (20 pages), with ten-second HTTP
timeouts; reaching the scan limit is an explicit temporary failure.

The `algoguard_identity_owner` role has no login/BYPASSRLS and privileges only
for identity tables and the private request ledger, with explicit RLS policies.
Live testing showed the platform-owned Auth schema cannot be granted to this
role by `postgres`. The follow-up `20260926011000` migration reads the verified
PostgREST subject from request settings; Auth recovery uses the hosted admin API.
The already-applied enrollment migration was retained unchanged.

---

## 6. Evidence

### 6.1 Verified before delivery

Checked against a throwaway PostgreSQL 16 instance and in-process test
runs. The local stack is PostgreSQL 17, so §6.2 is the confirming run.

- All five migrations apply from empty, in order, with no errors.
- No table in `public` has RLS off; `anon` and `authenticated` hold no
  privilege on any of them; a table created *after* the baseline is also
  born unreachable.
- A prediction cannot reference another node's traffic (foreign key
  violation); a second active `model_deployment` is refused; a partial
  flow group in `ingest_event` is refused; the activation lock admits
  exactly one row.
- `private.is_administrator()` is false for a token whose claims say
  `"role": "administrator"` when no record exists, true once the record
  is inserted, and false again the moment it is deleted — same token
  throughout.
- Self-grant and grant-by-a-non-Administrator are both refused (SQLSTATE
  42501); `profile.auth_user_id` cannot be reassigned; a deactivated
  profile resolves to NULL.
- `anon` cannot execute the helpers at all; `authenticated` can.
- 61 offline tests pass: 26 for the migration runner, 19 for token
  verification (including algorithm confusion, rotation and refresh
  rate-limiting), 16 for the bootstrap script.
- `ruff check .` clean across every new file.

### 6.2 Local verification attempt — 2026-09-25 (Asia/Manila)

**Historical failed attempt; resolved in §6.4.** Docker Desktop was launched
normally and through `docker desktop start`. Its backend repeatedly failed
while initializing the Ingest server, trying to rename
`%LOCALAPPDATA%/Docker/run/sailor-ingest.sock` to
`sailor-ingest.sock.stale`: **The file cannot be accessed by the system.**
The `dockerDesktopLinuxEngine` pipe was never available. The same failure
occurred outside the workspace sandbox. No Docker data reset, volume removal,
or cloud operation was performed.

The initial `check_5b.cmd` could not execute correctly with LF line endings.
After repairing it, the first full attempt measured **399 passed, 1 failed,
22 deselected in 60.96 s** in the fast lane; lint passed. The one failure
was the algorithm-confusion test constructing its attack token: installed
PyJWT 2.15.0 rejects JWK text as an HMAC signing key. The test now constructs
the hostile HS256 token with standard-library HMAC so it still exercises
AlgoGuard's rejection, without weakening the verifier.

Verification harness repairs:

- CRLF batch-file checkout enforced by `.gitattributes`.
- Repo-pinned Supabase CLI added to PATH; batch shim calls use `call`.
- Database reset explicitly uses `--local`.
- Failed status requests no longer replace the local credentials file.
  Valid local credentials must be regenerated after Docker is available.
- Per-command exit codes are saved in `check-output/00-summary.txt`; any
  command failure produces a nonzero script exit. Integration skips are
  explicitly not acceptance evidence. JWKS requests have a ten-second limit.
- `check-output/` is ignored; the missing `SUPABASE_API_URL` guidance was
  added to `.env.maintainer.example`.

Final rerun of `check_5b.cmd` (script exit **1**, correctly reporting failure):

| Check | Measured result |
|---|---|
| Dependency installation | Passed; PyJWT 2.15.0 and cryptography 50.0.1 installed |
| Stack stop/start | Failed: Docker engine pipe unavailable |
| `supabase db reset --local` | Failed before connecting; no migration applied |
| Local credential refresh | Failed; local env lacks API_URL |
| Fast pytest lane | **400 passed, 22 deselected in 46.51 s** |
| `python -m ruff check .` | **All checks passed** |
| `cloud_migrate.py --status` | Failed: connection refused at 127.0.0.1:54322 |
| Integration pytest lane | **22 skipped, 400 deselected in 2.44 s**; missing local configuration, not a pass |
| JWKS | Unavailable: connection refused at 127.0.0.1:54321; signing mode not measured |
| Signing-key CLI help | Passed; command and ES256 support confirmed |

Raw logs and per-command exit codes are in `check-output/`. The integration
process exits zero when everything skips; the script still exits one for the
failed infrastructure checks. No live acceptance claim is made from those skips.

- [ ] `supabase db reset` — all five migrations apply.
- [x] `python -m pytest -q` — 400 passed, 22 deselected in 46.51 s.
- [x] `python -m ruff check .` — all checks passed.
- [ ] `python cloud_migrate.py --status` against the local stack.
- [ ] Edit an applied migration; `cloud_migrate.py --dry-run` refuses;
      restore the file.
- [ ] `python -m pytest -m integration -v`.
- [ ] Which mode the local stack signs in, and the JWKS response.

### 6.3 Historical blockers and their resolution

- Docker startup originally blocked local verification. The requested initial
  gate passed in §6.4 before work on 5B.3 began.
- `README_STAGE_5A.md` records a previous baseline migration failure changing
  default privileges for `supabase_admin` (SQLSTATE 42501). This attempt could
  not reach PostgreSQL to reproduce it or inspect the applied ledger. The
  existing migrations were therefore left untouched.

- ES256 signing is now enabled; §6.4 records actual JWKS/token verification
  without signing-mode skips.
- The live runner now applies new migrations and passes concurrency, rollback,
  and checksum refusal tests. A complete runner rebuild remains part of the
  final stage verification.

---

### 6.4 Recovered local stack — 2026-09-26

The prior Docker blocker is resolved. The separate `AlgoGuard5A` stack was
stopped with backup enabled to free the shared ports; all subsequent work uses
the root `AlgoGuard` disposable stack. No pilot project was contacted.

The first baseline migration failed with SQLSTATE 42501 and rolled back before
any application table existed. Because it had never been applied to this stack
or the pilot, that unapplied file was corrected to alter defaults for `postgres`
only. Platform-owned `supabase_admin` defaults are outside the maintainer role's
authority. All application DDL must use `postgres`. Global PUBLIC function
EXECUTE was also revoked: a per-schema revoke cannot subtract a global default
([PostgreSQL documentation](https://www.postgresql.org/docs/18/sql-alterdefaultprivileges.html)).
Applied migrations remain immutable; later changes use new versions.

The successful `check_5b.cmd` run exited **0**:

- All five migrations applied through `supabase db reset --local`.
- **400 offline tests passed**, 25 deselected, **123.39 s**.
- **25 integration tests passed, no skips**, 400 deselected, **6.09 s**.
- Lint passed; runner status adopted and verified all five CLI versions.
- JWKS publishes an **EC / ES256** key; real Auth tokens verify successfully.
- Live runner tests prove two concurrent applies execute once, a failing DDL
  file rolls back, and an edited migration **copy** makes `--dry-run` exit 2.
  The applied source files are never modified for the checksum test.
- Public signup and anonymous signup fail while admin-created email users log
  in. The email-provider correction in §4.1 is backed by these real requests.
- Existing test defects were fixed: rollback before switching autocommit;
  query parameters passed separately through the local-only HTTP guard;
  disposable Auth cleanup also runs when login fails.

Passing logs are preserved locally in `check-output/5b1-2/`.

### 6.5 Enrollment verification — 2026-09-26

`check_5b.cmd` exited **0** after all seven migrations applied from reset:
**402 offline tests passed** (29 deselected, **128.25 s**),
**29 integration tests passed without skips** (402 deselected, **7.02 s**),
and lint passed. Logs: `check-output/5b3/`.

Real user tokens exercise repeated pending enrollment, two approved memberships
and default switching, atomic reassignment, rejected/revoked states, account
deactivation, anonymous/analyst/demoted-admin refusal, direct service-RPC refusal,
self-role change refusal, duplicate requests, mismatched request UUIDs, invalid
input, and recovery after an Auth-only partial creation. A focused test also
proves concurrent local startups retain the same persisted installation UUID.

### 6.6 Relational/API access verification — 2026-09-26

`check_5b.cmd` exited **0** after all eight migrations applied from reset:
**402 offline tests passed** (34 deselected, **127.58 s**),
**34 integration tests passed without skips** (402 deselected, **10.16 s**),
and lint passed. Logs: `check-output/5b4/`.

Two actual analysts and an Administrator exercise HTTP SELECT/INSERT/UPDATE/DELETE
and forged node, owner, and relationship IDs. Hidden reads return empty arrays;
denied writes disclose no private endpoint/alert text. Administrator observation
does not permit ordinary writes to another node. Tests cover sessions, traffic,
predictions, alerts, reports, report joins, logs, protected tables, self-promotion,
safe invoker views, revoked membership with an unchanged token, and Storage.
Storage bytes are readable only while the corresponding manifest is readable;
ordinary users cannot upload/delete models. A catalog assertion rejects any
public view lacking `security_invoker=true`.

### 6.7 Transaction and repository contract

`cloud_repository.py` uses only authenticated HTTPS and has no database driver,
privileged credential, token persistence, or local business-store fallback.
Every instance carries an immutable Auth UUID, profile BIGINT, installation UUID,
and bearer token. Token values are excluded from representations. The server
derives the current profile independently and rechecks membership for writes.

The operation inventory for `services/database_service.py` is:

| Existing operations | Cloud boundary / integration destination |
| --- | --- |
| initialize_database, migrations, seed_default_admin | Maintenance only: cloud_migrate/bootstrap_admin; never analyst startup |
| get_admin_by_username, list_admins, create_admin | Auth email login in 5D; protected profile/role reads and account_admin commands |
| create/update/list/get training runs; insert/save/list/get model results | Maintainer publication in 5C; analyst reads use training_summary/model_summary |
| record_deployment, get_active_deployment | 5C serialized publication and protected deployment_summary/model_manifest reads |
| insert traffic/prediction/alert, mark alert created, store_classified_flow | store_flow_batch; callers must not split a persistent flow into independent requests |
| insert/finalize/list capture sessions | Scoped capture_session insert/update/read; 5D supplies terminal lifecycle and replay reconciliation |
| list_alerts, get_latest_prediction | Scoped ordered repository pages and equality filters |
| get_detection_stats, count_traffic_by_source | detection_statistics / traffic_source_counts invoker RPCs |
| log_system_event, list_system_logs, get_log_filter_options | Scoped append-only system_log insert/read; 5D adapts UI filtering |
| insert_report and report-alert evidence | Protected report/report_alert tables; 5D must add transactional report composition before enabling its UI |

Pages contain immutable tuples of rows and an optional next offset, with stable
primary-key ordering, 1–100 rows per page, and offsets limited to 1,000,000.
Concurrent inserts may shift offset pages; this is not a snapshot/export API.
The default node filter is a convenience; removing it never bypasses RLS.
Requests have a ten-second socket timeout, redirects/proxies disabled, and a
4 MiB response bound. Errors expose only validation, authentication, permission,
not_found, conflict, transient, or protocol categories. Only transport failures,
429, and 5xx are retryable; 5D owns bounded retries and synchronized refresh.

`store_flow_batch` accepts 1–50 events, each at most 64 KiB. Every event carries
an immutable UUID, timezone-qualified event time, model/deployment, optional
capture, traffic values, prediction, and optional alert. Legacy JSON remains
text and timestamps are converted to canonical UTC text. The server normalizes
and validates values, checks relationships, and writes traffic, prediction,
optional alert, required audit, and deduplication anchor in one transaction.
Any failed group rolls back the entire batch. Advisory transaction locks in
sorted UUID order serialize retries; a changed payload under the same UUID
conflicts. Ownership/node identity are part of the hash.

Acknowledgements carry the event UUID, exact BIGINT IDs serialized as decimal
strings, a replay flag, and `persistence=committed`. Python parses IDs without
floating-point conversion. A missing, mismatched, or noncommitted response is
never an acknowledgement. This synchronous repository contract is the server
commit boundary; 5D will separately expose durable-pending and dropped states.

The event trigger also validates direct ledger writes against normalized child
rows and a required audit record. Ordinary functions run as SECURITY INVOKER;
identity exceptions have a non-login, non-superuser, non-BYPASSRLS owner, pinned
search paths, qualified names, current-role checks, and narrow execution grants.

Focused atomic-flow verification passed **12 tests in 4.79 s**, covering lost
acknowledgements, concurrent retries, changed payloads, whole-batch rollback,
forged identities/relationships, malformed input, direct-ledger tampering,
pagination, and an ID greater than JavaScript's exact integer range. The first
combined rerun passed **43 integration tests** and failed **5** because the local
function service returned `503 name resolution failed`; this is infrastructure
failure evidence, not stage acceptance. A full stack restart/rerun follows.

### 6.8 Final combined verification — 2026-09-26

`check_5b.cmd` exited **0** after a clean restart and all nine migrations applied:

- **412 offline tests passed**, 48 deselected, **132.30 s**.
- **48 integration tests passed**, no skips, 412 deselected, **25.50 s**.
- Ruff passed; every recorded command exit code is zero, including Auth JWKS
  verification and migration status.
- Node polling tests: **10 passed**, no skips, **196.3977 ms**.
- Live bootstrap generated a policy-compliant password, signed in, and repeated
  without creating another Auth user/profile/role. Passwords are excluded from
  outcome representations. Narrow SECURITY DEFINER owners/search paths passed
  catalog verification.

The transient function-service DNS failure disappeared after the clean stack
restart. No test was skipped or weakened to make the verification pass.

The CLI rejected `db reset --local --version 0` before changing the database
because no migration numbered zero exists. A separate runner exercise cleared
only the disposable local application schemas and migration ledger, retaining
the platform services, then applied **all nine files with cloud_migrate.py**.
All applied successfully and runner status reported them current. The first
API run then passed 45 tests and failed 3 administration calls: recreating public
removed platform-provided service_role schema USAGE. Migration
`20260926031000_service_rpc_schema_usage.sql` makes that narrow dependency
explicit without granting table access. After application, all **48 integration
tests passed, no skips, in 21.14 s** against the runner-built schema. A catalog
regression check and final ten-migration gate were added next.
No cloud project, legacy SQLite database, or real account was changed.

Final ten-migration `check_5b.cmd` run exited **0**: **412 unit tests passed
in 126.79 s**, **49 integration tests passed without skips in 21.04 s**, and
lint/JWKS/migration-status checks passed. The integration run deselected 426
offline cases because 14 independent Stage 5C cache tests were added after the
unit lane had collected; those 14 passed separately in 7.40 s and are not part
of the Stage 5B acceptance count. Raw Stage 5B logs are archived in
`check-output/5b-final/`. The SQLite application remains unchanged.

## 7. Carried into later stages

- **5C** uses `deployment_activation_lock`: activation takes
  `SELECT ... FOR UPDATE` on the singleton row, so the database enforces
  one active deployment.
- **5D** wires `token_verification.py` into the request path, holds
  tokens in memory only, and treats `TokenExpired`,
  `SigningKeysUnavailable` and `TokenRejected` as three different
  situations.
