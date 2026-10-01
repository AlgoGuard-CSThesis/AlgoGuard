# Stage 5E — Existing data and recovery

Status: locally implemented and verified (2026-09-30). This stage adds
maintenance tools and rehearses recovery locally. It does not switch the
working installation or change its application configuration.

## Data contract

`legacy_backup.py` creates an owner-restricted SQLite snapshot with the SQLite
backup API, records schema versions/counts/account IDs/deployments, checks
foreign keys, and copies every available referenced model with its SHA-256.
Missing or out-of-scope artifacts remain explicitly inventoried. A usable legacy
restore requires all referenced artifacts. An older unsupported schema must be
upgraded on a restored copy first; never upgrade the only surviving snapshot.

`legacy_import.py` imports one immutable snapshot into one approved, empty node.
The source installation UUID and original table/key identify each record in
`private.legacy_record`. Destination keys come from PostgreSQL sequences, so
identical IDs from different installations and source IDs above JavaScript's
safe integer range cannot collide. Source keys remain available in the ledger.
Business import is one transaction. A separate committed protective gate keeps
the node unwritable even if its first import fails; failure rolls back business rows.
retrying the same source/snapshot/node/mapping verifies receipts and destination
row presence without duplicating data. Sequence gaps after rollback are harmless.
A changed snapshot under an existing source identity is refused. Rehearsal and
final cutover therefore use separate empty targets, not incremental imports.

Historical accounts have source-qualified internal names and retain their
original display names. They are inactive and cannot log in by default.
`--identity-map` optionally accepts a JSON object mapping legacy account IDs to
fresh Auth UUIDs with no existing profile. The importer does not grant roles or
memberships. Existing Auth profiles cannot be silently merged. Local password
hashes stay in the protected SQLite backup and never enter cloud tables or the
cloud review ledger.

Operational records belong to the dedicated enrolled node. A unique recorded
prediction actor supplies ownership where supported by source evidence;
missing or conflicting attribution uses an inactive historical placeholder.
Unresolved traffic, predictions, alerts, reports, and logs remain administrator
only. Reports containing unresolved alert evidence are also restricted. The
administrator-only `legacy_import_review` API view preserves sanitized original
records and source-to-destination mappings. Research runs/models/deployments
retain their existing global access rules. No role is inferred from a legacy
account's role label.

Timestamps, numeric values, flags, model history, and report-alert links are
retained. Historical deployment activation flags are archived but imported
deployments are inactive. Artifact paths become source-qualified content
references; copied files are not automatically loaded or published. Use the
Stage 5C publisher for a compatible active model. Old artifacts without the
required producer provenance must be retrained with the locked runtime.

## Controlled cutover

Use the maintainer environment and dependencies documented in Stage 5A/5C.
Database credentials and Storage secrets must stay out of the analyst `.env`.
Commands below are PowerShell examples; replace the UUIDs and paths with the
installation's recorded values. `--writers-stopped` is an operator attestation,
not a command that terminates processes.

1. Keep SQLite authoritative. Enroll and approve a dedicated empty node in an
   isolated target. Record its node UUID, the source installation UUID, code
   revision, configuration, and `.algoguard/node-id`. Stop captures, training,
   the legacy application, cloud applications, and upload workers that could
   write affected data. Preserve the local state directories.
2. Take and verify the final snapshot. Do not resume legacy writes after this
   snapshot if proceeding with cutover.

   ```powershell
   python legacy_backup.py backup database/algoguard.sqlite3 backups/final-legacy --source-id <source-UUID> --installation-root .
   python legacy_backup.py verify backups/final-legacy
   python legacy_backup.py restore backups/final-legacy backups/legacy-restore-check
   ```

3. Apply versioned cloud migrations with `cloud_migrate.py`. Import using the
   dedicated approved node; writes remain gated while historical reads work.

   ```powershell
   python legacy_import.py --writers-stopped import backups/final-legacy --node-id <node-UUID>
   ```

4. Reconcile manifest counts with source receipts, sample relationships,
   timestamps, predictions, alert states, report evidence, filters, and original
   account display names. Review all unresolved records as an Administrator.
   Retry the same import to verify idempotency. Publish an eligible evaluated
   model through `publish_model.py` as described in `05c-models.md`; when using
   a restored training database, point `ALGOGUARD_DATABASE_PATH` at that copy
   with its relocated artifact paths. Publication creates its own cloud
   model/deployment identities. Do not reactivate an imported filesystem path.
5. Explicitly enable the imported node, select cloud mode and the corresponding
   node identity, log in with approved Auth membership, and verify model download
   before starting captures or accepting API writes.

   ```powershell
   python legacy_import.py --writers-stopped enable --source-id <source-UUID>
   ```

SQLite is authoritative through reconciliation. Cloud becomes authoritative
when new cloud writes start. The first new business write, model publication,
or account/membership change that must be retained makes code rollback alone
insufficient. Enabling the gate does not edit `.env`, sign anyone in, or launch
the application. Never run both backends as writable authorities.

## Recovery

Before new cloud changes need preservation, restore into a new directory with
`legacy_backup.py restore`, select SQLite mode and that restored database, and
start the old application with its saved configuration. Artifact paths are
rewritten only in the restored copy. Keep the original backup immutable.

After cloud writes, stop every affected application, capture, publisher,
administration process, and upload worker. Freeze each imported node:

```powershell
python legacy_import.py --writers-stopped freeze --source-id <source-UUID>
python cloud_recovery.py export backups/cloud-recovery --writers-stopped --legacy-bundle backups/final-legacy --outbox .algoguard/outbox
python cloud_recovery.py verify backups/cloud-recovery
```

Repeat `--legacy-bundle` for every imported source and `--outbox` for every
installation with local pending records. The tool requires frozen imported
nodes; other writers must be stopped operationally. A repeatable-read snapshot
exports all application tables, migration mappings, administration request
receipts, and ingest deduplication receipts. Referenced published model objects
must match their protected hash and size. Missing unpublished objects are
recorded explicitly. Each supplied outbox is copied consistently, including
pending events and lifecycle summaries. Keep the export owner-restricted and
copy it to the installation's protected backup location. Keep each installation's
node-id and non-secret configuration alongside it; these are not recreated by
the outbox restore command.

Restore forward to an **empty compatible recovery database** with the same
versioned schema, policies, functions, and required original Auth UUIDs. Configure
the maintainer tools for the recovery database and matching Storage endpoint,
then run:

```powershell
python cloud_recovery.py restore backups/cloud-recovery --writers-stopped
python cloud_recovery.py restore-outbox backups/cloud-recovery --outbox-index 0 --output backups/recovered-state/outbox
```

Restore refuses differing existing business rows or object bytes. Identical
retries are safe. SQL restoration is atomic; a failed SQL restore can leave
immutable uploaded objects that a retry reuses. IDs, receipts, relationships,
artifact hashes, and every exported field are compared; identity sequences
advance without rewinding. Imported nodes stay frozen until explicitly enabled.
Restore the matching `node-id` to the selected state directory, preserve pending
spool ownership, log in again as the original user, then resume uploads. A lost
acknowledgement reuses its existing event UUID/receipt, preventing a second row.

This is an application-data recovery package, **not a Supabase platform or Auth
backup**. Restore Auth identities with their original UUIDs using the provider's
separate backup/recovery process before business restore; do not copy legacy
password hashes. Restore/reconfigure platform services, keys, roles, bucket
policies, and Edge Functions separately. The isolated database rehearsal seeds
Auth UUID references and verifies database/Storage bytes; it does not demonstrate
an entire hosted Supabase disaster recovery or session restoration.

There is no lossless conversion back to the old SQLite schema: Auth identities,
roles/memberships, node ownership, event UUIDs/receipts, cloud manifests, and
pending lifecycle state have no complete legacy equivalent. Preserve all those
fields in the export and recover forward. An old SQLite snapshot alone loses
post-cutover work. Pending events still follow the application's seven-day
retention policy; preserve the recovery package and reconcile expired events
before resuming normal cleanup after a long outage.

## Local evidence

The actual local installation was backed up into `backups/stage-5e-local/` and
restored separately into `backups/stage-5e-local-restored/`. Both directories are
ignored by Git and owner-restricted. No source database or application
configuration was changed.

- Source UUID: `c1e3483d-deb4-4dfa-9142-31020f3703d4`.
- Snapshot SHA-256: `87870669d88f016c10516fab64d9daacb2833badc2c083f6c8f3abd3372c6128`.
- Schema versions: 1, 2, 4. Accounts: 1; training runs: 2; models: 12;
  deployments: 2; captures: 1; traffic/predictions/alerts: 300 each; reports: 2;
  logs: 49; report-alert links: 0. All referenced artifacts were copied.

Synthetic tests exercise report evidence links that this installation does not
contain, two sources with identical IDs/names, 64-bit values, ambiguous ownership,
real Auth/RLS reads, failed transaction/retry, explicit write gates, real published
model bytes, new cloud writes, and an isolated forward restore. Recovery creates
and removes only a uniquely named scratch database; it never resets the source
stack.

Verification commands, with an unpaused local Supabase Docker stack and the
existing local test credentials configured:

```powershell
python -m pytest -q --tb=short
python -m ruff check .
npm.cmd run typecheck
node --test tests/monitor_poll.test.cjs
$env:RUN_WINDOWS_CAPTURE_TESTS = '1'
python -m pytest -q -m integration --tb=short
$env:DATABASE_URL = 'postgresql://postgres:postgres@127.0.0.1:54322/postgres'
python cloud_migrate.py --status
```

Stage 5E's four real-stack import/recovery integration tests passed. They cover
two installations with colliding IDs, explicit new-Auth mapping without roles,
transaction failure/retry, report and capture evidence, cloud writes, frozen
export, a separate compatible recovery database, immutable artifact restore,
pending outbox/lifecycle data, and replay of a restored ingest receipt. The
focused log is `check-output/5e-transfer-final.txt`.

The original Stage 5E run passed 505 offline tests and 16 focused backup/recovery
tests; JavaScript passed 11, Ruff passed, Pyright reported zero findings, and
all 15 versioned migrations (including this stage) reported applied. Its broad
integration run had six failures while the local Edge Runtime was stopped and
while the recovery test was being corrected. On 2026-09-30, after restarting
Edge Runtime, the complete integration lane passed (67 passed with Windows
Npcap checks enabled), and the offline lane passed 508 tests. The focused
Edge/recovery rerun passed 12 tests. Current logs are recorded in the Stage 5F
pilot report; original Stage 5E logs remain at `check-output/5e-unit-final.txt`,
`check-output/5e-backup-tests-final.txt`, `check-output/5e-transfer-final.txt`,
and `check-output/5e-integration-final.txt`.

Stage 5F remains separate: simultaneous physical installations, performance
budgets, and hosted pilot measurements are not acceptance claims of Stage 5E.
