# AlgoGuard Development Iterations (plan)

Written 2026-09-10. Revised 2026-09-11 after review against the repository and
Supabase documentation. This is the current plan. Implementation tasks are in
[AlgoGuard Migration (backlog).md](<AlgoGuard Migration (backlog).md>).

Iterations 1–4 describe completed capabilities. Iterations 5–6 plan the migration
and release. This revision changes documentation only: no migration stage is
marked complete.

## Method and working rules

An iteration is a reporting milestone containing several verified stages.
**One stage, one verification record, one manual commit.** Each stage ends with a
runnable application and passing relevant checks. Each iteration also gets a
summary referring to its stage records. Work does not continue into the next
stage automatically.

Build and test cloud foundations while the existing SQLite application remains
the runnable baseline. Introduce the cloud application through an explicit pilot
configuration, without mixing local and cloud business records in one run. Cut
over existing installations only after import and recovery rehearsals pass.
Remove this temporary dual implementation from normal analyst startup at release.

The backlog contains **40 tasks across ten stages**. Future stage documents under
`docs/migration/` record changes, verification evidence, remaining limitations,
and recovery procedures. Code rollback and data recovery are distinct operations.

---

# Part I — Completed capabilities and historical evidence

These are thematic groupings, not a contemporaneous iteration diary. The git log
supports the milestones below, but present-day behavior includes later fixes.
Do not claim every capability existed at the earliest commit, or that historical
development followed the new stage-and-commit rule.

| Group | Repository evidence | Interpretation |
| --- | --- | --- |
| 1 — Research and training | `cd4bd23` (2026-07-07), `f85c41d` (2026-07-19), `04c7ddd` and `ee44006` (2026-07-21) | Training and model selection evolved across several revisions. |
| 2 — Web application and persistence | `bdd4d2a` (2026-07-12), `f85c41d` (2026-07-19), later UI/security fixes | Accounts, persistence, and application workflows developed together. |
| 3 — Deployment and replay monitoring | `ee44006` (2026-07-21), later hardening including `6374a85` (2026-09-09) | Quality-gated deployment predates the latest transaction/session safeguards. |
| 4 — Packet capture | `948605e` (2026-09-01), subsequent September fixes | Real packet inputs were added and then hardened. |

## Iteration 1 — Model training and comparison engine

**Objective.** Train and compare supervised learners on labelled network flows.

**Current delivered capability.** The offline training tool trains Random Forest,
Gradient Boosting, AdaBoost, K-Nearest Neighbors, and Naive Bayes, plus a Stacking
Ensemble using all five with Logistic Regression as the final estimator.
Candidates use fresh estimators and the same stratified train/test split.
Imputation, scaling, and encoding are fitted inside the training pipeline and
saved with the classifier. This prevents preprocessing from learning from the
held-out rows; it does not establish generalization to another network.

The five valid individual models receive an equally weighted, min-max normalized
ranking over accuracy, precision, recall, F1, ROC-AUC, false positive rate, CPU
time, peak RAM increase, and artifact size. Lower resource use and false positive
rate are better. Ties use F1, recall, ROC-AUC, FPR, RAM, model size, then name.
This research ranking does not select the deployed model.

The bundled CSVs are nested, stratified, non-replacement samples of 5,000, 10,000,
and 20,000 rows from the UNSW-NB15 training partition, with fifteen features and
a Normal/Attack label. Identifiers and `attack_cat` were removed. Replaying
overlapping training data is a demonstration, not independent accuracy evidence.

**Status: implemented.** Historical candidate sets differed from the current
`stacking-five-v3` workflow.

## Iteration 2 — Web application, persistence, and access control

**Objective.** Provide detection workflows and a durable record of results.

**Current delivered capability.** Flask serves detection while training runs in
the command-line tool. SQLite holds accounts, training results, deployments,
traffic, predictions, alerts, reports, logs, and capture sessions. Additive
migrations preserve rows. Administrator and Analyst accounts use password hashes
and signed Flask sessions, with a random first-run password unless seed
credentials are supplied.

The application includes manual prediction and its JSON API, Alert History,
System Logs with filters, reporting, and account management. Python tests isolate
database/artifact paths; Node tests cover browser polling.

**Status: implemented.** Training moved out of the web tier during development;
this describes the current architecture, not the entire historical sequence.

## Iteration 3 — Deployment pipeline and Live Monitor

**Objective.** Make deployment deliberate and monitoring observable.

**Current delivered capability.** Only Stacking can deploy, after passing
configurable accuracy, F1, and ROC-AUC thresholds, each defaulting to 70 percent.
Deployment publishes a unique artifact and atomically changes the active
database reference. Earlier artifacts remain available to in-flight readers.
SQLite currently serializes independent publishers with `BEGIN IMMEDIATE`;
PostgreSQL needs an explicit replacement for that behavior.

The Live Monitor replays labelled flows, shows verdicts/statistics, and reattaches
after a browser reload. Storage modes are session-only, attacks-only, and every
flow. Persistent flow records are capped at 300 per session while classification
continues.

**Status: implemented**, with later deployment, cleanup, and API hardening.

## Iteration 4 — Packet capture and real-time flow tracking

**Objective.** Classify flows derived from recorded and live packets.

**Current delivered capability.** A bidirectional 5-tuple tracker computes fifteen
features from packet timestamps. Flows close on TCP teardown with an ACK drain,
after 15 seconds of idleness, or in 120-second slices. CSV, PCAP, and Scapy/Npcap
interface capture share classification. Packet sources retain real endpoints;
CSV replay uses cosmetic addresses because the samples contain no real endpoints.

Live mode reports packet counts, inference time, and detection lag. AlgoGuard's
own web-port traffic is excluded in the capture filter and per packet.

Prior working notes report an 80-test packet milestone followed by 196 pytest
cases, ten Node tests, lint, and browser QA. These are historical results, not
checks rerun for this revision. Approximately 60 ms detection lag from one run is
an observation, not a throughput guarantee or acceptance target.

**Status: implemented, with research validation outstanding.** Packet-derived
features approximate the original dataset extraction. Feature-fidelity validation
and representative latency measurement are release work in 6A. Implementation
completion does not validate live IDS accuracy.

## Current baseline relevant to migration

The SQL is centralized in `services/database_service.py`: eleven application
tables plus `schema_migration`, seven explicit indexes, integer keys/flags, and
text UTC timestamps. Map those types and relationships deliberately.

The deployment loader first queries SQLite, then loads a local joblib path. A
shared record cannot keep using one machine's path. The monitor synchronously
stores a traffic/prediction/alert group for each eligible flow; its inference
timer excludes that storage time. Unchanged inference time alone cannot prove
the migration has no capture impact.

Confidence is the predicted-class probability as a percentage; new alerts
default to High. Configuration is scattered, including the capture port in
`services/traffic_source_service.py`. Scope follows these behaviors, not a fixed
placeholder count or a requirement to preserve every function signature.

---

# Part II — Target architecture and operating decisions

## Local capture and authenticated cloud access

Keep Flask, capture, feature extraction, and inference on Windows. Supabase
provides PostgreSQL, Auth, and private model storage. **Administrator observation
is the scope of this revision**; remote start/stop and an unattended device
service are future work. Only capture needs the local interface, so this choice
does not prohibit a future hosted console.

Analyst installations use HTTPS **Data API requests with a publishable key and
the signed-in user's access token**. SQL functions invoked through that API
perform multi-table transactions. Direct PostgreSQL connections, including
`psycopg`, belong to trusted migrations, import, and maintainer tooling. A public
API key is not a PostgreSQL password. This is a data-access refactor, not a
drop-in connection-helper replacement.
[Connection methods](https://supabase.com/docs/guides/database/connecting-to-postgres)
and [database functions](https://supabase.com/docs/guides/database/functions)
describe the underlying mechanisms.

Introduce a repository boundary with explicit user/node context, stable results,
and defined errors. Preserve useful contracts, but change signatures where
identity or asynchronous persistence requires it. Relational joins remain in
PostgreSQL through authorized queries or SQL functions.

## Credentials, accounts, and nodes

Analyst `.env` files contain the project URL, publishable key, and local settings.
They contain no database password, service/secret key, or JWT signing secret.
Maintainer credentials have a separate source excluded from the analyst package.
Local node identity, model cache, pending events, and temporary user sessions
remain necessary; the migration does not eliminate local state.

Use asymmetric JWT signing and JWKS verification with an algorithm allowlist,
issuer, audience, expiry, and key-rotation handling. Browser cookies hold an opaque
HttpOnly/SameSite session identifier; access and refresh tokens stay in process
memory. Cookie-authenticated mutations retain CSRF protection. API callers use
bearer tokens. Secure cookies apply to HTTPS; supported local HTTP binds to
loopback. Restart requires online login.
[Supabase JWT verification](https://supabase.com/docs/guides/auth/jwts)

Disable public sign-ups. Trusted maintenance creates the first Administrator.
A narrowly scoped Edge Function creates later accounts and approves memberships
after checking the caller's current protected Administrator role. Token metadata
may mirror roles for display, but protected current role/membership records
govern authorization so a stale token cannot retain removed privileges.
Use email/password cloud login; historical usernames remain display names.

Each installation generates a persistent UUID. Administrator-approved enrollment
binds users to nodes. An analyst cannot claim another node by editing that UUID
or a request. One user may have multiple explicit memberships; the local node is
the default. Membership authorizes submission for a node, but does not attest
that a hostile client physically captured the submitted traffic.

RLS and least-privilege grants ship with the initial cloud schema. Analysts
access assigned nodes only; Administrators read all nodes while ordinary
operational writes remain limited to assigned local nodes. Checked privileged
paths handle account/membership administration; only trusted maintenance can
publish deployments. Protect traffic, profiles, reports, joins, and audit records
as well as predictions and alerts. Enforce scope transitively across relationships,
using write policies and constraints against cross-node links. Views and functions
must preserve those restrictions.
[Supabase RLS](https://supabase.com/docs/guides/database/postgres/row-level-security)

Ordinary SQL functions use `SECURITY INVOKER`. Necessary privileged helpers get
narrow ownership, a fixed safe search path, qualified objects, explicit caller
checks, and minimal execute grants. Test direct API reads/writes as a hostile
analyst, including forged IDs, membership changes, and role promotion. Hidden reads
return no rows; write errors disclose no private record contents.

## Model publication and delivery

Deliver model distribution before the shared pilot. Manifests record immutable
object path, SHA-256, model/deployment IDs, workflow/feature-schema versions, and
compatible Python/dependency versions. Lock training and inference dependencies,
including scikit-learn, NumPy, SciPy, pandas, and joblib. Reject incompatible
artifacts before deserialization.
[Scikit-learn model persistence](https://scikit-learn.org/stable/model_persistence.html)

After the existing quality gate passes, the maintainer uploads an immutable
private object, verifies remote bytes, then activates its manifest in a database
transaction. Serialize activation by locking a permanent singleton row; enforce
at most one active deployment with a database constraint/index. Upload and
database commit are separate operations. Failed activation leaves a tracked
orphan for cleanup and preserves the previous active model.

Clients request an authorized short-lived download URL, verify compatibility and
the hash against the protected manifest, then atomically promote a temporary
download. A hash establishes integrity relative to the trusted manifest; it is
not independent proof of authorship if both manifest and object can be replaced.
Protect both write paths. Current captures pin their verified deployment; updates
apply next session. Never silently substitute a different model after validation
fails.

## Persistence and offline behavior

Use a bounded memory handoff and a separate local SQLite **outbox** for pending
events. The spool is not the old business database and does not provide offline
cloud reports. Each event has an immutable UUID, original user, node, session,
deployment, and event time. Mark it queued only after local durable storage,
and synced only after acknowledgement. Server uniqueness plus atomic SQL writes
make retry safe when a response is lost.

Manual prediction/API responses carry the event UUID and an explicit persistence
status. Database row IDs may be unavailable until synchronization; queued work
must not be reported as a committed cloud record. Record this response-contract
change in the API guide and adapt callers during 5D.

Initial engineering defaults, to validate under load:

- 1,000 events in the memory handoff; a 100 MiB total outbox footprint including
  SQLite journals; batches of at most 50 flow events; a one-second flush interval.
- Ten-second network operation deadlines and capped exponential retry with
  jitter. Refresh once on expiry; permission/validation failures are not transient.
- Keep the 300 eligible flow-record cap per session. Reserve slots before enqueue
  so pending records count toward it. Retrying an event uses no extra slot.
- Reserve bounded capacity for lifecycle/error summaries. If flow capacity or
  disk storage fails, stop accepting persistent flow records, expose loss counts,
  and keep classification running within its authorization window.
- Stop capture immediately, drain local work for at most five seconds, and show
  capture status separately from synchronization status. Never wait indefinitely
  for the cloud. Memory-only events can be lost on crash and are never called saved.
- Durable events survive stop/restart; reconcile late events and final session
  metadata without duplicating results or reopening a terminal capture state.
  Pending events expire after seven days, with explicit loss reporting.

| Situation | Supported behavior |
| --- | --- |
| Network loss during an authorized capture | Continue with the pinned model and bounded outbox until stop or access-token expiry. |
| Access token expires and refresh is unavailable | Stop capture locally, retain durable pending events, and require online re-login. |
| Browser reload with a valid in-process session | Reattach; polling and writer refresh operations serialize for that user. |
| Application restart while offline | Show offline/login state. No new authenticated capture or cloud reports; a cached model does not authorize login. |
| Reconnect | Reauthenticate and recheck membership. Replay under the original user's valid authorization, never another account. |
| Logout or account switch | Stop the old capture, clear tokens, retain pending records under their original owner. |
| Revoked membership or permanent rejection | Stop affected uploads, show an actionable status, retain bounded exportable records, and never use privileged fallback credentials. |

A user's pending events may flush after capture stops, once that user is
authorized again. This supports bounded interruptions, not unattended offline
operation. Owner-restricted local files and redacted logs protect pending data.

## Existing data, cutover, recovery, and retention

Preserve business records by default. Take consistent SQLite backups and inventory
model files. A repeatable maintainer importer maps source-installation/source-ID
pairs, preserves foreign keys, checks identity sequences, and reconciles counts.
Different local installations may reuse IDs. Historical accounts become non-login
profiles, optionally linked through an explicit map to fresh Auth users. Do not
copy password hashes into cloud tables. Attribute legacy operational records to
enrolled nodes; unresolved attribution remains administrator-only.

Rehearse in an isolated project. At cutover, stop legacy writers, take a final
snapshot, import/reconcile, verify, then enable cloud writes. There is one
authoritative destination at a time. Before cloud writes, restore the old
application/snapshot if necessary. Afterwards, freeze writes, export cloud changes
and pending outboxes, and restore forward or reconcile into a compatible recovery
database. Verify records before resuming. If reverse conversion cannot represent
new fields, retain the export and restore forward; an old SQLite file is not a
lossless rollback.

Initial retention targets are 30 days for operational flows, predictions, alerts,
logs, and closed capture sessions, and 90 days for reports. Evidence referenced
by retained reports survives with those reports. Keep training/deployment
manifests, active/in-use artifacts, and at least one verified rollback artifact.
The pilot does not automatically purge records. Add previewable, audited cleanup
in 6B after recovery rehearsal; validate real storage and backup growth. These
targets are project defaults, not a promise about hosted capacity.

---

# Part III — Planned iterations

## Iteration 5 — Secure cloud foundations and a two-node pilot

**Objective.** Deliver protected shared data, verified model delivery, recoverable
migration, and bounded interruption behavior.

Stages run in order. Through 5C, cloud components are tested independently and
the current local application remains the default. Stage 5D enables the complete
cloud path in an isolated pilot; 5E rehearses import/cutover; 5F accepts the
two-node pilot. Live-accuracy claims remain limited until 6A passes.

| Stage | Deliverable | Acceptance before the next stage |
| --- | --- | --- |
| 5A — Configuration and test infrastructure | Typed configuration, separate credentials, local Supabase stack, baseline tests | Local app passes; Auth, API, Storage, and PostgreSQL tests use disposable data. |
| 5B — Identity, schema, and authorization | Migrations, profiles, approved node membership, RLS, API functions, account administration | Two real test users cannot cross node boundaries through direct reads/writes. |
| 5C — Model publication and delivery | Immutable objects, concurrent activation protection, manifests, verified downloads | A clean authorized client fetches a model; corruption/incompatibility fail; publishers preserve one active model. |
| 5D — Application integration and persistence | Login, API-backed pages, outbox, lifecycle handling, cloud-traffic exclusion | All sources/pages work; outages follow the operating table; stop is bounded. |
| 5E — Existing data and recovery | Repeatable import, historical identities, cutover and recovery rehearsal | Counts/relationships survive; pre-write rollback and post-write recovery both work. |
| 5F — Two-node pilot verification | Simultaneous installations, security probes, performance/storage measurements | Acceptance covers all sources, isolation, downloads, retries, restart, and recovery. |

**Exit criteria.** Two enrolled installations classify and submit under their own
users. Direct hostile-client tests cannot cross assignments. Existing data is
accounted for, models load from verified manifests, and interruption has bounded
consequences. Analyst installations have no privileged cloud credentials.
Integration tests exercise the real Auth/API/RLS path.

## Iteration 6 — Interpretation, administration, and release

**Objective.** Make scores interpretable, provide administrator visibility and
retention, and verify a reproducible analyst installation.

| Stage | Deliverable | Acceptance before the next stage |
| --- | --- | --- |
| 6A — Research validation and attack scores | Feature-fidelity report, independent evaluation, calibration assessment, score display | Claims match evidence; supported features pass measured tolerances; probability is never labelled impact. |
| 6B — Administrator visibility and retention | Cross-node evidence, node health, bounded audited cleanup | Analysts remain scoped; stale/sync/storage states are visible; cleanup preserves retained evidence. |
| 6C — Recovery and operational hardening | Auth lifecycle, update/recovery drills, sustained-load/failure checks | Revocation, lost responses, restart, disk failures, and traffic exclusion meet the contract. |
| 6D — Packaging, documentation, and final QA | Maintainer-only training, Windows guide, clean-machine walkthrough, final report | A new analyst configures, enrolls, logs in, and monitors without training or privileged keys. |

### Research and score-display contract

Store `attack_probability` in [0, 1], indexed by class value, separately from
predicted-class confidence. Store `attack_score_band` and its policy version on
predictions/alerts; use the same result object for session-only feed rows.
Initial bands: **Low** below 0.50, **Medium** from 0.50, **High** from 0.70, and
**Very high** from 0.90. A confident Normal result has a low attack score.
No band is called Critical or described as impact, verified danger, or calibrated
likelihood without supporting evidence.

Keep historical `severity_level` values as legacy labels. Unknown old scores
remain unavailable, not fabricated. Use one shared mapping with accessible text
and consistent colors across the feed and Alert History.

Use a final evaluation set independent of training, calibration, and threshold
selection. Document overlap checks for the nested CSVs. Compare packet-derived
features against matched reference flows, covering all fifteen features,
direction, units, timeout behavior, and differences. Predeclare tolerances and
gate supported packet inputs on the feature report.

Record false positives, recall, F1, ROC-AUC, reliability curves, and Brier score
on representative held-out data. Calibration assessment is required; adding a
calibrator depends on evidence and produces a new gated artifact.
[Probability calibration](https://scikit-learn.org/stable/modules/calibration.html)

Measure event-time-to-verdict lag, packet loss, throughput, queue age, local write
latency, and sync delay under declared loads, including idle/long-flow closure
delays. If representative labelled packet evidence is unavailable, record the
release gate as unmet and retain an explicitly limited research pilot.

---

# Verification and scope controls

Use the local Supabase CLI stack for integration: plain PostgreSQL alone does
not test Auth, Data API, Storage, and their permissions. Keep fast unit tests
separate from isolated-stack API tests. Maintenance uses a distinct privileged
credential; application tests use actual low-privilege users, not database-owner
or service-role impersonation.
[Local development](https://supabase.com/docs/guides/local-development)

Check numeric ranges, nulls, dates, filters, and transaction failures during the
SQL port. Use BIGINT where SQLite's 64-bit integer behavior is needed. Keep legacy
timestamps as canonical UTC text and integer flags for compatibility; use
`timestamptz` for new membership, heartbeat, manifest, and sync timestamps with
explicit serialization. Convert legacy text columns in separate future work.
Only maintenance applies migrations and policies.

At each stage run relevant lint, Python, Node, migration, and integration checks;
add browser QA when workflows change. Final QA covers every page at 1366 px and
390 px, all sources, malformed APIs, role/node isolation, fresh installation,
and defined failures. Expected failure tests should return specified errors;
the requirement is no unexplained errors. Record actual totals and runtimes.

Singapore is the initial region choice; measure network latency from intended
Manila installations rather than assuming 30–50 ms. Record performance budgets
before tuning or acceptance, not after observing results.

Cloud self-exclusion must identify AlgoGuard-owned API/Auth/Storage connections,
and monitored maintainer database connections, as narrowly as practical. Do not
exclude all HTTPS or a shared cloud IP. Test that unrelated HTTPS to the same
provider remains visible. If precise exclusion is unavailable, record the limit
and leave the pilot gate unmet rather than claim complete exclusion.

## Deferred work and assumptions

- Administrator observation is included; remote control, hosted console, and
  unattended device credentials are separate future work.
- Startup/login require connectivity. Offline cold start and continuous capture
  after authorization expiry are separate features.
- Outbox and retention limits are initial engineering defaults to validate in
  5F/6B. Changes update both documents and tests.
- Feature-fidelity and representative evaluation remain release requirements;
  replay confidence cannot replace missing research evidence.
- Preserve existing records by default. Destructive cleanup is a later explicit
  operational action, not part of this documentation revision.

## What changed in the 2026-09-11 revision

1. Replaced the direct-PostgreSQL/public-key mismatch with authenticated Data API
   access and transactional SQL functions; direct credentials stay in trusted tools.
2. Required JWKS verification, current protected role/membership checks, approved
   enrollment, and complete relational authorization.
3. Moved security and model delivery before the shared pilot; expanded the work
   into ten stages and 40 tasks.
4. Added immutable publication, concurrent activation control, compatible-runtime
   manifests, and explicit upload/commit failure handling.
5. Chose a bounded durable outbox, duplicate-safe retries, separate capture/sync
   state, stop deadlines, and limited offline authorization.
6. Added data import, historical identity mapping, cutover, and recovery after
   cloud writes; removed the implication that an old SQLite file is sufficient.
7. Replaced severity claims with attack-score bands and made feature fidelity,
   independent evaluation, calibration assessment, and latency evidence release work.
8. Added targeted cloud-traffic exclusion, full Supabase integration tests,
   retention targets, and failure acceptance criteria.
9. Clarified history, required local state, stage-level manual commits, and links.
