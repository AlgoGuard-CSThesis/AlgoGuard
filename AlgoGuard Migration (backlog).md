# AlgoGuard Migration (backlog)

Revised 2026-09-11. Companion to
[AlgoGuard Iterations (plan).md](<AlgoGuard Iterations (plan).md>).

**Forty tasks across ten stages.** Iteration 5 establishes a secure two-node
cloud pilot; Iteration 6 validates interpretation, administration, and release.
This revision replaces the previous task numbering. All tasks remain planned.

**One stage, one verification document, one manual commit.** Each stage leaves a
runnable application and passing relevant checks. Cloud foundations are tested
independently while the SQLite app remains the default through 5C. Stage 5D
enables the isolated cloud pilot; 5E rehearses import/cutover; 5F accepts the pilot.
Shared production use follows those gates.

The plan defines architecture and operating defaults; these checklists make them
implementable. The document paths below are future deliverables, not files
already created by this revision. Suggested labels: migration, security, testing,
research, ui, docs.

## Stage map

| Stage | Tasks | Depends on | Verification document |
| --- | --- | --- | --- |
| 5A — Configuration and test infrastructure | 5A.1–5A.4 | Current local baseline | `docs/migration/05a-foundations.md` |
| 5B — Identity, schema, and authorization | 5B.1–5B.5 | 5A | `docs/migration/05b-access.md` |
| 5C — Model publication and delivery | 5C.1–5C.4 | 5B | `docs/migration/05c-models.md` |
| 5D — Application integration and persistence | 5D.1–5D.5 | 5C | `docs/migration/05d-integration.md` |
| 5E — Existing data and recovery | 5E.1–5E.4 | 5D | `docs/migration/05e-cutover.md` |
| 5F — Two-node pilot verification | 5F.1–5F.3 | 5E | `docs/migration/05f-pilot.md` |
| 6A — Research validation and attack scores | 6A.1–6A.4 | 5F | `docs/migration/06a-evidence.md` |
| 6B — Administrator visibility and retention | 6B.1–6B.3 | 6A | `docs/migration/06b-operations.md` |
| 6C — Recovery and operational hardening | 6C.1–6C.4 | 6B | `docs/migration/06c-recovery.md` |
| 6D — Packaging, documentation, and final QA | 6D.1–6D.4 | 6C | `docs/migration/06d-release.md` |

---

## Iteration 5, Stage A — Configuration and test infrastructure

**Exit:** the current local app remains usable, configuration is explicit, and a
disposable Supabase stack can exercise the complete future application path.

### 5A.1 Introduce typed configuration without changing local behavior

Load `.env` predictably and centralize settings for Flask, training, databases,
artifacts, and capture. Preserve process-environment overrides for tests and
automation; document precedence and avoid freezing fixture values at import.

- [ ] Empty optional configuration retains today's local defaults.
- [ ] Parse and validate types, ranges, and required combinations.
- [ ] Move direct configuration reads out of application modules, including
  `services/traffic_source_service.py`; capture-port exclusion still works.
- [ ] Tests redirect all database, model, and spool paths safely.

### 5A.2 Separate analyst and maintainer configuration

Create analyst `.env.example` and a separate maintainer configuration example.
Local and cloud-pilot operation are explicit modes during migration.

- [ ] Analyst settings contain project URL, publishable key, and local options;
  no database password, service/secret key, or JWT signing secret is required.
- [ ] Privileged tools load a separate credential source, excluded from packaging
  and version control. Logs redact tokens, credentials, and signed URLs.
- [ ] Cloud mode cannot silently fall back to the legacy business database.
- [ ] Document local state locations: outbox, node identity, model cache, and
  temporary user sessions.

### 5A.3 Provision isolated cloud and local test infrastructure

Use the local Supabase CLI stack with Docker Desktop/WSL as necessary. Plain
PostgreSQL alone cannot validate Auth, Data API, Storage, or API permissions.

- [ ] Local Auth, Data API, PostgreSQL, Storage, and Edge Function tests work.
- [ ] A separate pilot project uses Singapore initially; measure actual endpoint
  latency. Development tests never use production data or credentials.
- [ ] Maintenance migrates local/pilot databases with explicit TLS and connection
  mode settings; those credentials never enter analyst configuration.
- [ ] Create the private model bucket with access denied until 5B policies are
  installed. Test fixtures are disposable and distinguishable from real data.

### 5A.4 Establish baseline evidence and test isolation

- [ ] Record current Python, Node, and lint results and actual runtimes.
- [ ] Preserve fast unit tests and add an isolated local-stack integration lane.
- [ ] Reset integration state or serialize scoped fixtures; tests do not depend
  on ordering or impersonate ordinary users through owner credentials.
- [ ] Write `docs/migration/05a-foundations.md` with setup, architecture, evidence,
  and reproducible test commands before the manual stage commit.

---

## Iteration 5, Stage B — Identity, schema, and authorization

**Exit:** cloud access is protected from its first usable schema, with
authenticated API transactions and approved user-to-node assignments.

### 5B.1 Create versioned cloud schema migrations

Port the eleven application tables and migration history deliberately. Add
node, membership, protected role, event-deduplication, and manifest structures
required by later stages, with deny-by-default permissions.

- [ ] Trusted tooling applies repeatable/checksummed migrations, serializes
  concurrent migration attempts, and rolls back failed schema changes.
- [ ] Preserve intended keys, relationships, nullability, indexes, and SQLite
  64-bit numeric ranges. Required IDs and counters use BIGINT.
- [ ] Legacy timestamps retain canonical UTC text and integer flags; new
  lifecycle/authorization times use `timestamptz` with explicit serialization.
- [ ] Profiles preserve historical attribution through legacy IDs and an optional
  unique Auth UUID. Cloud profiles contain no password hashes.
- [ ] Every exposed table starts with explicit least-privilege grants and RLS;
  unimplemented access paths remain denied.

### 5B.2 Configure Auth and protected account roles

- [ ] Disable and test public sign-up. Trusted maintenance creates the first
  Administrator without a shared default password.
- [ ] Configure asymmetric signing/JWKS; verification tests cover algorithm,
  issuer, audience, expiry, tampering, unknown keys, and key rotation.
- [ ] Protected current role records govern authorization. Token metadata may
  mirror display roles but cannot override a removed privilege.
- [ ] Record access/refresh lifetimes and memory-only token handling. Cloud login
  uses email/password; historical usernames remain display names.
- [ ] No JWT signing secret is required on analyst installations.

### 5B.3 Implement approved enrollment and account administration

A local UUID identifies an installation. Administrator approval binds users to
nodes; hostname, IP address, or a submitted node ID does not grant access.

- [ ] Define pending enrollment, approval, reassignment, and revocation states.
  Repeated startup/enrollment does not duplicate a node.
- [ ] Support multiple approved memberships, with the local node as default.
- [ ] A checked Edge Function creates accounts and changes memberships only for
  a caller whose current protected role is Administrator.
- [ ] Analysts, anonymous callers, and demoted admins fail direct function calls;
  invalid input and duplicate requests receive controlled responses.
- [ ] Privileged keys remain hosted/in trusted tooling. Partial Auth/profile
  creation is recoverable; retries do not create duplicate accounts.

### 5B.4 Enforce access across the complete relational schema

- [ ] Analysts read/write assigned nodes. Administrators read all nodes while
  ordinary operational writes remain scoped to their assigned node.
- [ ] Traffic, predictions, alerts, sessions, logs, reports, and report joins have
  direct or enforced inherited scope; direct API writes cannot link other nodes.
- [ ] Profiles, roles, memberships, training results, manifests, Storage, views,
  and function execution all have explicit policies/grants.
- [ ] Test SELECT/INSERT/UPDATE/DELETE and forged user/node/relationship IDs
  through the real API with two users, including self-promotion attempts.
- [ ] Hidden reads return no rows; errors expose no private contents. Protected
  current records revoke stale access; helpers avoid recursive RLS and unsafe
  search paths. Policies and grants live in version-controlled migrations.

### 5B.5 Define repository contracts and transactional API functions

Inventory current service operations and map them to authenticated reads or SQL
functions called through HTTPS. Replacing a connection helper is insufficient.

- [ ] Define explicit user/node context, stable results, pagination, validation,
  timeouts, and application-level error categories.
- [ ] A flow transaction creates traffic, prediction, optional alert, and required
  audit records atomically. Batch failure leaves no partial flow groups.
- [ ] Immutable event UUIDs deduplicate retries, including lost acknowledgements;
  an existing UUID with different content is rejected.
- [ ] Ordinary functions use `SECURITY INVOKER`. Privileged exceptions have
  narrow owners, qualified objects, safe search paths, caller checks, and minimal
  execute grants. Constraints still protect direct table-write paths.
- [ ] Auth/API/RLS tests pass; write `docs/migration/05b-access.md` with permission
  matrix and evidence before the stage commit.

---

## Iteration 5, Stage C — Model publication and delivery

**Exit:** a clean authorized client can fetch a verified compatible model before
the application starts using shared deployment records.

### 5C.1 Define the model manifest and compatible runtime

- [ ] Store immutable object path, SHA-256, model/deployment IDs, workflow and
  feature-schema versions, and compatible Python/dependency information.
- [ ] Lock training/inference dependencies including scikit-learn, NumPy, SciPy,
  pandas, and joblib; record the lock identifier in the manifest.
- [ ] Analysts and application Administrators cannot publish/change manifests;
  authorized reads expose no private training-machine path.
- [ ] Preserve historical deployments without activating incompatible artifacts;
  the maintainer retrains when an old artifact cannot pass current requirements.

### 5C.2 Publish immutable artifacts and serialize activation

- [ ] Failed existing quality gates upload nothing. Passing runs publish an
  immutable object and verify remote bytes before activation.
- [ ] Activation locks a permanent singleton row in a PostgreSQL transaction,
  updates replacement history, and enforces at most one active deployment.
- [ ] Two independent simultaneous publishers preserve exactly one active model
  and consistent history, including an initially empty deployment table.
- [ ] Upload success/activation failure preserves the previous model and leaves
  an identifiable orphan. Reconcile ambiguous responses; cleanup never deletes
  an active or in-use artifact.

### 5C.3 Implement authorized download and atomic cache promotion

- [ ] User-authorized Storage access obtains a short-lived URL; clients without
  a valid authorized identity cannot retrieve private models.
- [ ] Check manifest compatibility and SHA-256 before `joblib.load`; download
  to a temporary path, then atomically promote verified bytes.
- [ ] Refuse partial, corrupted, incompatible, and wrong-manifest artifacts.
  Expected hashes come from protected manifests, not caller-supplied values.
- [ ] Reuse verified cached bytes without redundant downloads. A cache failure
  does not silently substitute a different active deployment.
- [ ] Expose metadata for pinning a model for a capture's lifetime. Possessing
  cached bytes alone grants no offline login permission.

### 5C.4 Verify the distribution lifecycle

- [ ] Exercise empty cache, repeat load, expired URL, corruption, interrupted
  download, concurrent publication, and activation failure.
- [ ] A failed update preserves the verified model already used by a capture,
  while future-session startup clearly reports the failed update.
- [ ] The analyst package needs no privileged key or training command.
- [ ] Write `docs/migration/05c-models.md` with integrity assumptions, failure
  behavior, and evidence before the stage commit.

---

## Iteration 5, Stage D — Application integration and persistence

**Exit:** the full application works in an isolated cloud pilot and obeys the
bounded offline contract. Existing-installation cutover remains a later gate.

### 5D.1 Integrate cloud login and scoped application reads

- [ ] Login/logout, profiles, account management, enrollment, and role checks
  use cloud identity; historical profiles have no login.
- [ ] Browser cookies hold an opaque HttpOnly/SameSite session identifier;
  tokens stay in process memory. CSRF still guards cookie-authenticated writes.
- [ ] Bearer API authentication is independent. Shared mutable SDK state cannot
  replace one user's token with another's during concurrent requests.
- [ ] Dashboard, alerts, logs, reports, filters, and prediction workflows use
  authenticated repository operations with explicit local-node defaults.
- [ ] Controlled UI/API states handle network failures without recursive database
  logging failures. Restart needs online login, even with a cached model.

### 5D.2 Build the bounded local outbox and upload worker

- [ ] Use a separate owner-restricted SQLite spool. Each immutable event records
  UUID, original user, node, session, deployment, and timestamps.
- [ ] Initial limits: 1,000 handoff events, 100 MiB total spool including journals,
  batches up to 50 flow events, one-second flush, ten-second request deadlines.
- [ ] Distinguish in-memory, durable pending, synced, dropped, and rejected.
  Reserve the 300-flow session cap before enqueue; retries use no extra slots.
  Lifecycle/error summaries have separate bounded reserved capacity.
- [ ] Retry transient failures with capped exponential backoff/jitter, reconcile
  lost acknowledgements through server deduplication, and stop treating permanent
  permission/validation failures as retryable.
- [ ] Overflow/disk failure stops additional persistent flow acceptance and
  reports loss while classification continues. Durable events survive restart;
  memory-only events are never labelled saved.

### 5D.3 Integrate capture lifecycle, refresh, and synchronization

- [ ] CSV, PCAP, live capture, and manual prediction share the atomic event
  contract; cloud latency does not block the capture inference loop.
- [ ] Manual/API responses include an event UUID and explicit persistence status;
  database IDs may be absent until acknowledgement. Queued work is never reported
  as cloud-committed, and callers/tests adopt the documented response change.
- [ ] Pin a verified deployment per capture. Reload reattaches; polling/upload
  refresh operations serialize for that user.
- [ ] Close capture immediately on stop; local drain has a five-second deadline.
  Capture status is terminal independently of pending synchronization.
- [ ] At expiry without refresh, stop locally and require online login.
  Logout/account switch stops the old capture and clears its tokens.
- [ ] Restart/reconnect reconciles final metadata and pending events without
  reopening a closed capture or replaying under another user's identity.
- [ ] Revocation/permanent rejection is actionable. Pending events remain bounded
  by seven days/size limits and exportable without privileged fallback access.

### 5D.4 Exclude AlgoGuard's cloud connections narrowly

- [ ] Identify app-owned API/Auth/Storage connections and monitored maintainer
  database connections, including reconnect and capture-fallback behavior.
- [ ] Preserve web-port exclusion; do not exclude all HTTPS or a shared cloud IP.
- [ ] Show that cloud upload/download traffic does not feed its own classification
  loop while unrelated HTTPS to the same provider remains visible.
- [ ] Record the Windows attribution method, races/limitations, and excluded
  packet counts. Unresolved exclusion limitations block pilot claims.

### 5D.5 Verify the integrated cloud application

- [ ] All pages/sources work against the actual local/pilot API with real Auth
  users. No application path requires privileged database access.
- [ ] Test network loss, expiry, disk/queue full, stop deadline, lost response,
  refresh concurrency, and restart with pending events.
- [ ] Maintain the old baseline until cutover; pilot mode cannot mix stores or
  apply schema migrations with a user's credentials.
- [ ] Run relevant Python, Node, lint, and browser checks; write
  `docs/migration/05d-integration.md` before the stage commit.

---

## Iteration 5, Stage E — Existing data and recovery

**Exit:** existing data is accounted for and recovery is demonstrated, including
records written after switching to the cloud.

### 5E.1 Inventory and consistently back up legacy installations

- [ ] Inventory source installations, schema versions, counts, account IDs,
  deployment references, and artifact hashes without printing credentials.
- [ ] Take consistent SQLite backups and copy referenced artifacts; verify
  backups open and required relationships resolve.
- [ ] Map source-installation/source-ID pairs so identical local IDs cannot collide.
- [ ] Preserve records by default; unresolved attribution is recorded for
  administrator-only handling instead of guessed or discarded.

### 5E.2 Implement repeatable legacy import

- [ ] A maintainer importer preserves dependency order, relationships, timestamps,
  flags, numeric ranges, and report-alert evidence.
- [ ] Local accounts become historical non-login profiles; optional mappings to
  fresh Auth users are explicit. Never copy password hashes into cloud tables.
- [ ] Attribute operational records to enrolled legacy nodes; global research
  and deployment records retain their distinct access policy.
- [ ] Import/resume is idempotent by source identity. Check sequences and counts;
  repeated imports do not create duplicates.
- [ ] Preserve deployment history and publish the active compatible model through
  5C rather than activating a path copied from another machine.

### 5E.3 Rehearse controlled cutover

- [ ] In an isolated project, stop legacy writers, take the final snapshot,
  import/reconcile, verify counts/relationships, then enable cloud writes.
- [ ] Compare representative predictions, alerts, reports, filters, and historical
  profile names before and after migration.
- [ ] Import failure leaves the old app usable and cloud activation disabled;
  partial import is resumable or cleanly reversible in the rehearsal.
- [ ] Record the authoritative store at each step and the exact point after
  which code rollback alone is insufficient.

### 5E.4 Rehearse recovery before and after cloud writes

- [ ] Before the first cloud write, demonstrate restore of the old application
  and consistent snapshot.
- [ ] After sample cloud writes, freeze writers, export cloud changes and local
  pending events, then restore forward or reconcile into a compatible recovery
  database; verify no lost or double-counted records.
- [ ] Record reverse-conversion limits. Preserve unsupported fields in exports
  rather than calling an old SQLite snapshot a lossless rollback.
- [ ] Restore-test cloud backup/export together with artifacts; write
  `docs/migration/05e-cutover.md` before the stage commit.

---

## Iteration 5, Stage F — Two-node pilot verification

**Exit:** two installations share the system safely, with measured performance
and documented limits. Research claims remain limited until 6A.

### 5F.1 Run the simultaneous two-node acceptance scenario

- [ ] Two Windows installations retain distinct UUIDs across restart, require
  approved membership, and default to their own node's ordinary views.
- [ ] Each downloads a verified model into an empty cache and runs CSV, PCAP,
  live capture, and manual/API prediction without local training.
- [ ] Direct cross-node read/write, relationship, membership, role, and manifest
  probes fail as specified. An Administrator can read both nodes.
- [ ] Revoke access with an unexpired token present; protected current records
  block subsequent cloud operations.

### 5F.2 Measure throughput, delay, and resource limits

- [ ] Record hardware, traffic rates, network latency, and acceptance budgets
  before tuning. Include slow and unavailable endpoints.
- [ ] Measure packet loss, flow throughput, event-to-verdict lag, local spool
  latency, backlog age, sync delay, and total disk/RAM use.
- [ ] Test the 300-flow cap with pending writes, growth across sessions, bounded
  shutdown, unrelated HTTPS visibility, and session summary behavior.
- [ ] Measure projected shared storage/backup growth against actual capacity;
  changes to defaults require evidence and corresponding document/test updates.

### 5F.3 Close Iteration 5 with pilot evidence

- [ ] Repeat relevant suites and cutover/recovery smoke checks.
- [ ] Record actual totals, expected error responses, limitations, and remaining
  research gates; no unexplained UI/server failures remain.
- [ ] Write `docs/migration/05f-pilot.md` and an Iteration 5 summary linking all
  six stage records before the manual stage commit.

---

## Iteration 6, Stage A — Research validation and attack scores

**Exit:** score presentation and research claims match independent evidence.

### 6A.1 Validate packet-feature fidelity and capture latency

- [ ] Select reference flows and predeclare tolerances for all fifteen features,
  covering direction, units, protocol/service/state, and flow closure.
- [ ] Compare matched packet-derived/reference features, recording exclusions,
  unsupported cases, tool versions, and dataset provenance.
- [ ] Measure lag including idle/long-flow closure under declared loads; separate
  inference time from end-to-end detection and synchronization delay.
- [ ] Supported inputs pass tolerances. Missing representative evidence leaves
  a research release gate unmet rather than a claimed success.

### 6A.2 Evaluate generalization and probability calibration

- [ ] Define training, calibration/selection, and untouched final evaluation sets;
  verify nested CSV overlap cannot enter an independent accuracy claim.
- [ ] Report false positives, recall, F1, ROC-AUC, reliability curves, and Brier
  score with sample sizes and representative-data limitations.
- [ ] Any calibrator avoids final-test leakage and produces a newly gated
  artifact. Assessment does not automatically require adding a calibrator.
- [ ] Keep the six candidates, individual-model ranking, and Stacking-only
  deployment policy. Document any proposed quality-gate change separately.

### 6A.3 Store attack probability and versioned score bands

- [ ] Manual and monitor paths extract class 1 probability by class value into
  nullable `attack_probability` in [0, 1]; predicted-class confidence stays separate.
- [ ] One function maps Low below 0.50, Medium from 0.50, High from 0.70, and
  Very high from 0.90, storing `attack_score_band` and band-policy version.
- [ ] Add columns through numbered SQL migrations; preserve old
  `severity_level` values as legacy labels without inventing historical scores.
- [ ] Boundary, null, range, class-order, and confident-Normal tests pass.
  Changed configurable thresholds require a new recorded policy version.

### 6A.4 Present scores consistently and document evidence

- [ ] Feed and alerts use shared stored/result bands and accessible labels/colors;
  session-only feed rows need no database round trip to obtain a band.
- [ ] Explain attack score rather than impact/severity; old unknown scores render
  neutrally and no band is named Critical.
- [ ] README/research notes explain calibration limits, training/replay overlap,
  supported feature fidelity, and latency context.
- [ ] Relevant suites/browser checks pass; write
  `docs/migration/06a-evidence.md` before the stage commit.

---

## Iteration 6, Stage B — Administrator visibility and retention

**Exit:** administrators observe node health/evidence while retention is bounded,
inspectable, and safe for related records.

### 6B.1 Add cross-node alerts, logs, and reports

- [ ] Administrators select one/all nodes; rows identify origin and distinguish
  event time from delayed synchronization.
- [ ] Analysts see only assigned data and default to their local node; direct
  API access obeys the same restrictions as visible controls.
- [ ] Node/time/status filters, joins, indexes, and pagination stay responsive
  at the realistic row counts measured in 5F.

### 6B.2 Show node health without remote capture controls

- [ ] List last-seen, app/model versions, capture status, and reported pending,
  dropped, or rejected synchronization counts.
- [ ] Heartbeats need a valid user and membership; last-seen uses server receipt
  time. Stale receipt means stale status regardless of the last reported health.
- [ ] Offline startup does not register anonymously. Logout stops authenticated
  heartbeats; stale nodes remain visible.
- [ ] No remote start/stop or unattended device-credential path is introduced.

### 6B.3 Implement audited retention and capacity reporting

- [ ] Initial targets: 30 days for operational flows/predictions/alerts/logs/closed
  sessions, 90 days for reports; report-linked evidence survives with retained
  reports. Event/server timestamps have explicit retention roles.
- [ ] Keep training/deployment manifests, active/in-use model files, and at least
  one verified rollback artifact.
- [ ] Local pending events expire after seven days within the 100 MiB total
  limit; expiration/rejection is visible, never silently counted as synced.
- [ ] Cleanup has a dry-run preview, bounded batches, audited authorization, and
  relationship-safe ordering. Preserve active sessions; quarantine invalid
  client times and expose report holds/capacity pressure.
- [ ] Rehearse cleanup/restore on fixtures before enabling maintenance scheduling;
  write `docs/migration/06b-operations.md` before the stage commit.

---

## Iteration 6, Stage C — Recovery and operational hardening

**Exit:** failures, session changes, and model updates have verified outcomes.

### 6C.1 Exercise authentication and authorization lifecycle failures

- [ ] Test expiry during polling/upload, concurrent refresh, logout with pending
  events, account switch, revoked membership, demotion, and key rotation.
- [ ] Valid refresh allows UI reattachment; unauthorized uploads stop. Pending
  events never replay under another user's token or privileged fallback.
- [ ] Offline cold restart requires online login before capture. Secrets are
  absent from cookies, logs, URLs, and error screens.

### 6C.2 Exercise durable-write and capture-stop recovery

- [ ] Inject failure before commit, after commit before acknowledgement, malformed
  events, partial batches, and duplicate UUIDs with changed content.
- [ ] Kill/restart with pending events: server results remain unique and local
  capture/sync state reconciles, including late final metadata.
- [ ] Disk full, stalled network, full queues, and denied writes respect capacity
  and stop deadlines; UI distinguishes dropped/pending/rejected/synced.
- [ ] Audit/error reporting stays bounded during prolonged failure and cannot
  recursively fill its own spool or crash capture.

### 6C.3 Exercise model updates and backup restoration

- [ ] Test interrupted cache promotion, corruption, version mismatch, missing
  objects, expired URLs, and manifest/object permission attacks.
- [ ] Concurrent activation preserves one active deployment; running captures
  retain their pinned model and new sessions resolve the current manifest.
- [ ] Restore cloud metadata/private artifacts consistently; orphan cleanup only
  deletes proven unreferenced uploads.
- [ ] Repeat post-cutover recovery with both nodes and pending outboxes, recording
  any unsupported reverse conversion.

### 6C.4 Run sustained load and targeted traffic checks

- [ ] Use declared 5F budgets and 6A evidence under sustained capture, sync,
  successive sessions, and network disruption.
- [ ] Check app-owned connections after reconnect/DNS changes while unrelated
  HTTPS to the same provider remains captured.
- [ ] Confirm storage, cleanup, sync delay, and memory remain bounded; changes
  to defaults update examples and acceptance checks.
- [ ] Write `docs/migration/06c-recovery.md` with evidence/limits before the
  manual stage commit.

---

## Iteration 6, Stage D — Packaging, documentation, and final QA

**Exit:** a clean Windows installation reaches monitoring without local training
or privileged cloud credentials, with accurate research limitations.

### 6D.1 Consolidate analyst and maintainer entry points

- [ ] Analyst distribution starts in cloud mode; remove the temporary legacy
  business-database path from normal analyst startup.
- [ ] Keep legacy import/recovery tooling for maintenance. The SQLite outbox
  remains explicitly separate from the old business store.
- [ ] Training stays a complete maintainer tool; analyst pages offer actionable
  administrator help for missing models or enrollment.
- [ ] Packaging excludes privileged settings, historical databases, test accounts,
  credentials, and artifacts not intended for analyst distribution.

### 6D.2 Rewrite installation and operating documentation

- [ ] README/user manual cover Windows, Npcap/capture privileges, dependencies,
  `.env`, online email/password login, enrollment, and monitoring.
- [ ] Explain session-only, durable pending, and synced records; offline limits,
  expiry, node health, and score interpretation.
- [ ] Troubleshooting covers connectivity, membership, missing models, hash/version
  failures, queue/disk limits, rejected writes, and retention.
- [ ] Maintainer docs cover migrations, bootstrap, publishing, import, backup,
  cutover, recovery, and separate credentials. Keep useful environment overrides
  for tests/automation.

### 6D.3 Verify installation from a clean Windows machine

- [ ] Follow the analyst guide with no database/cache and no training command;
  configure, enroll, log in, and monitor.
- [ ] Editing configuration, node ID, or direct requests cannot obtain another
  node's data or create privileged accounts.
- [ ] Verify restart/login, cache reuse, every source, interrupted connectivity,
  score labels, and absence of privileged keys in installed files.
- [ ] Record hardware/versions/setup problems, repair guide or package, and
  repeat affected checks.

### 6D.4 Complete final QA and the Iteration 6 record

- [ ] Run relevant lint, Python, Node, migration, Auth/API/RLS/Storage tests,
  and UI walks at 1366 px and 390 px across every page/source.
- [ ] Recheck malformed JSON, CSRF, role/node isolation, outages, artifacts,
  concurrent publication, recovery, and clean-install acceptance.
- [ ] Negative tests return their specified errors; no unexplained failed
  requests, console errors, or server tracebacks remain.
- [ ] Attach feature fidelity, independent evaluation, calibration assessment,
  performance, and recovery evidence. Unmet research gates limit release to the
  documented pilot scope.
- [ ] Write `docs/migration/06d-release.md` and an Iteration 6 summary linking its
  four stage records before the manual stage commit.

---

## Scope retained for this revision

Administrator observation is included; remote capture control is future work.
Online login after restart is required. Existing records are preserved by default.
Outbox and retention limits are initial defaults to validate, not measured capacity.
Feature fidelity and representative evaluation cannot be replaced by replaying
nested training samples.

No tasks are marked complete by this planning revision. Implement and verify one
authorized stage at a time using its full acceptance checklist.
