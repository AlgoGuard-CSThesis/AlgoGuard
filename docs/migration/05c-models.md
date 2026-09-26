# Stage 5C — Model publication and delivery

Status: implemented and verified locally on 2026-09-27. The application
continues to use its existing SQLite model loader until Stage 5D.

## Contract

The maintainer publishes only an evaluated, eligible Stacking artifact under the
existing quality thresholds. Publication records immutable object identity,
SHA-256, byte count, model/deployment identity, workflow, feature schema, Python
version, and an exact dependency lock. A client checks that protected metadata
and the downloaded bytes before any joblib deserialization. A trusted manifest
and trusted publication channel are necessary: a digest alone does not make
pickle/joblib content safe.

The local runtime currently measures Python 3.14.6, NumPy 2.5.1, SciPy 1.18.0,
pandas 3.0.3, scikit-learn 1.9.0, joblib 1.5.3, and threadpoolctl 3.6.0. These
versions are pinned in `requirements-model.lock`, installed by `requirements.txt`.
Their canonical version map plus Python version form the manifest's lock digest.
Training records actual producer versions. Publication rejects old artifacts
without matching producer provenance or the supported fifteen-feature schema;
maintenance must retrain them. This does not rewrite historical deployments.

Upload, remote byte verification, and activation are separate operations. A
durable publication identity records incomplete work before upload. Activation
locks the permanent singleton row, preserves replacement history, and relies on
the existing unique active-deployment/manifest indexes. Ambiguous responses are
reconciled by publication identity; a retry must not activate a superseded model.

Clients obtain a short-lived authorized Storage URL, stream into a temporary
file with size and digest checks, then atomically promote verified bytes. Cache
filenames derive from protected identities rather than a caller-supplied path.
An already running capture retains its verified model object. A new session
must resolve the current manifest and report update failure explicitly.

## Acceptance coverage

| Gate | Evidence |
| --- | --- |
| Locked runtime, producer provenance, feature compatibility | model_runtime checks, publication refusal tests, requirements-model.lock |
| Protected immutable manifest | Real analyst/admin mutation refusals and SQL immutability trigger test |
| Failed quality gate uploads nothing | Gate executes before Storage client construction; live test leaves publication count unchanged |
| Two independent publishers, initially empty deployment table | Separate connections activate under the permanent row lock; one active release and consistent replacement IDs |
| Upload succeeds, activation fails | Prior active model survives; orphaned publication is visible to maintenance and retry succeeds |
| Commit succeeds, acknowledgement lost | Injected post-commit failure leaves active manifest intact; same-ID retry returns it without duplication |
| Retry after replacement | Superseded publication stays superseded |
| Empty/reused cache | Actual authorized private-Storage fetch and repeat load |
| Corrupt/partial/oversized bytes, expired URL | Failure injection rejects before deserialization and leaves no promoted file |
| Failed update / cached corruption | Pinned model remains usable; future loads report failure explicitly |
| No privileged analyst configuration | Download path uses only user token and publishable key; maintainer dependencies are function-local |

## Implementation and recovery

`publish_model.py` is maintainer-only. Train with the locked runtime, then use
`--model-id <evaluated-local-model> --publication-id <stable-UUID>`; remote
publication additionally requires `--yes`. Set `SUPABASE_API_URL`,
`SUPABASE_SECRET_KEY`, and `DATABASE_URL` only in the separate maintainer source.
The tool preserves the original artifact bytes, copies the required evaluated
model metadata into cloud records, records intent, verifies remote bytes, and
activates using the permanent singleton row. Cloud model/deployment IDs come
from the protected manifest; historical IDs embedded in an artifact are not
cloud database identities.

Failed or ambiguous operations retain their publication identity and immutable
object. Retry the same UUID and source bytes. An active/superseded identity is
returned without reactivation. Different source bytes under that UUID are
refused. Retrying incomplete publication reconciles an existing remote object
before upload. Failures expose a controlled phase and safe message, never signed
URLs or upstream credential-bearing exceptions.

There is deliberately no automatic object deletion in this stage. Draft/orphaned
rows remain identifiable by publication UUID and status. Active, historical, and
in-use objects are retained. Cleanup requires a later audited procedure with
concurrency and reference checks; deleting uploaded bytes on a failed response
would be unsafe because activation may already have committed.

`model_delivery.ModelCache` resolves the current protected manifest on each new
load, even on a cache hit. Signed URLs expire after 60 seconds. Downloads have a
ten-second socket timeout and a manifest-size bound (maximum 512 MiB), and are
promoted with `os.replace` only after hash verification and fsync. A corrupt cache
fails explicitly; removing that specific file allows a fresh download. A pinned
object remains available to the existing capture. Possessing cached bytes never
authorizes offline login or a new cloud session.

## Measured verification so far

- Full offline suite: **433 passed, 52 deselected, 45.47 s**; lint passed.
- Initial cache failure suite: **14 passed in 7.40 s**; after provenance work,
  cache and migration-tool checks: **40 passed in 5.19 s**.
- Live publication/download and simultaneous-publication checks passed; the
  activation-failure retry check exposed a remote-verification failure after
  attempting to upload the existing object again. Reconciliation now checks for
  the existing immutable object before upload and still verifies all remote
  bytes. The corrected lifecycle suite passed **3 tests in 5.68 s**.
- Docker became unavailable during follow-up, and three tests errored before
  setup. After engine recovery, the incomplete stack lacked FUNCTIONS_URL and
  three tests skipped. Neither run is counted as acceptance. A complete local
  service restart restored actual Auth, API, and Storage execution.
- Expanded full integration suite: **52 passed, no skips, 433 deselected,
  14.30 s**, including the empty-table race and post-commit lost acknowledgement.

Final clean-schema verification applied all eleven migrations and passed
**52 integration tests without skips in 14.06 s**; lint passed. Logs are archived
in `check-output/5c-final/`. Stage 5C does not constitute two-machine pilot
acceptance; that remains Stage 5F.
