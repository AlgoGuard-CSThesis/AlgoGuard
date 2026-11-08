# Stage 6C — Recovery and operational hardening

Status: implemented and verified on the local Supabase stack on 2026-11-02. **No migration** was
added in this stage, so the hosted pilot needs no database change for 6C (its pending 6A/6B
migrations are unchanged; see Open items). Every failure below was caused on purpose and its
outcome checked against the operating contract (`05d-integration.md` §2) and the pilot budgets
(`05f-pilot.md` §2).

## Outcome

| Sub-stage | Result |
|---|---|
| 6C.1 Sign-in and authorization failures | Expiry during upload or polling, eight requests refreshing at once, logout with unsent records, account switch, demotion, deactivation, signing-key rotation and revocation, and an offline cold restart all behave as the contract says. Two gaps were fixed: a computer that **slept past its token's expiry** stopped its capture and signed out although its refresh token still worked, and a token the cloud **refused before it expired** (a revoked signing key) ended the sign-in instead of being renewed once. No password, token or secret key appears in cookies, logs, URLs, stored records or error screens. |
| 6C.2 Durable writes and crash recovery | **DEF-10 fixed:** up to four upload requests run at once. Failures before commit, lost acknowledgements, requests cut off mid-body, malformed records, changed duplicates, a killed uploader, a process killed mid-write, a genuinely full disk, a stalled network and a local disk error each end with every record stored once or kept locally with an honest status, never lost and never duplicated. Audit entries stay inside their reserve during long outages. The sustained run found and the stage fixed a race that could stop local saving until restart. |
| 6C.3 Model updates and backups | Missing model files, expired download links, interrupted downloads, and manifest or model-file tampering by app users all fail safely. A running capture keeps its model while new sessions use a newly activated one. The cloud backup now also covers the 6B tables (format 2), a new maintainer tool removes only proven-unreferenced model uploads, and post-cutover recovery was repeated with two computers holding unsent records. |
| 6C.4 Sustained load and traffic checks | At pilot-like latency, a 25 records/s stream with successive capture sessions and a 30 s outage met every pilot budget (acknowledgement p95, throughput, outage recovery, memory, storage) with no record lost or duplicated. AlgoGuard's own connections stay out of live capture across reconnects and a DNS change. |

## 6C.1 — Authentication and authorization lifecycle

Code: `cloud_auth.py` (`repository(..., refused=)`), `cloud_sync.py` (`_renew`), `cloud_monitor.py`
(`_check_authorization`), `cloud_app.py` (`renew_sign_in`, logout cookie). Tests:
`tests/test_auth_lifecycle.py` (offline, against the fake service) and
`tests/integration/test_auth_lifecycle.py` (real Auth on the local stack).

### Changes

- **A refused token is renewed once.** When the cloud answers 401 to a token that has not expired on
  this computer, the page, the monitor poll and the upload worker renew the sign-in with its refresh
  token (one refresh, under the identity's lock). The page or poll then repeats: a page reloads, and
  a JSON call returns 503 "Your sign-in was renewed. Retrying." (the Live Monitor retries 503s). If
  the renewed token is refused too within 30 s, the sign-in ends; there is no loop. If renewal
  cannot reach the cloud, the sign-in stays and the page shows "The cloud is unavailable".
- **Sleep past expiry.** The capture loop used to end the sign-in as soon as the token's expiry had
  passed. It now asks for a refresh first; only when that fails does the capture stop and the user
  have to log in again, as before.
- **Logout cookie.** The cookie-deletion header now carries the same `HttpOnly` and `SameSite=Lax`
  flags as the session cookie.

### Evidence

| Backlog item | Test | Result |
|---|---|---|
| Expiry during upload | I `test_expiry_during_upload_and_polling_refreshes_once`: inside the refresh margin, the worker renews before uploading; Supabase Auth shows one refresh token spent | Passed |
| Concurrent refresh | Same test: eight threads find the sign-in expired at once; one refresh, one token for all (Auth: 3 issued, 2 revoked) | Passed |
| Expiry during polling | Same test: an expired sign-in is refreshed by the monitor poll, which stays attached; O `test_a_computer_that_slept_past_expiry_renews_and_keeps_its_capture` | Passed |
| Logout with pending events, account switch | I `test_logout_with_pending_events_and_account_switch_keep_each_users_records`: user A's two unsent records never upload while user B is signed in on the same computer, B cannot see or export them, and they upload under A's profile after A signs in again | Passed |
| Revoked membership | 5D/5F evidence unchanged: I `test_lost_acknowledgement_restart_and_revocation`, `test_cloud_app_end_to_end_on_the_local_stack` (uploads stop, records stay pending and exportable, capture refused) | Passed in the lane |
| Demotion and deactivation | I `test_demotion_and_deactivation_take_effect_with_the_same_token`: the same token loses the Retention and Computers pages and gets 403 from `retention_preview`; after deactivation the next renewal ends the sign-in, and the record stays exportable | Passed |
| Key rotation | O `test_a_rotated_signing_key_keeps_sessions_and_new_logins_verify`, `test_a_revoked_signing_key_renews_the_sign_in_and_uploads_resume`, `test_the_upload_worker_renews_a_refused_token_without_ending_the_session`, `test_a_cloud_that_refuses_the_renewed_token_too_ends_the_sign_in`, `test_renewal_waits_out_a_cloud_outage_without_signing_out`. The fake service rotates and revokes its ES256 key as Supabase does (the old key keeps verifying until revoked). | Passed |
| Valid refresh allows reattachment; unauthorized uploads stop; no replay under another user | Tests above; the outbox reads only the signed-in owner's records, so a token can never carry another user's events | Passed |
| Offline cold restart needs online login before capture | I `test_offline_cold_restart_requires_an_online_login_before_capture`: with the model cached and a record pending, a restart with the cloud unreachable refuses login (no session) and capture (401); back online, login is required again and the record then uploads | Passed |
| Secrets absent from cookies, logs, URLs, error screens | O `test_secrets_never_reach_cookies_logs_urls_or_error_screens`; I `test_secrets_stay_out_of_cookies_logs_urls_records_and_error_screens`: the password, two generations of access and refresh tokens and the maintainer secret key were searched for in every response body and header, every cloud URL requested, every log record, the spool file, and the user's stored logs, traffic, events and alerts. None found; every cookie is `HttpOnly; SameSite=Lax`. | Passed |

**Limit.** The client fetches the project's signing keys again at most every 30 s. A key rotated
within 30 s of the previous fetch makes a refresh in that window fail as an unknown key, ending that
sign-in (records stay). Fetches happen only at startup and on an unknown key, so this needs a
rotation seconds after a restart.

## 6C.2 — Durable writes and capture-stop recovery

Code: `cloud_sync.py` (`UPLOAD_PARALLELISM`, `_store_parallel`, `_upload`), `cloud_outbox.py`
(`PENDING_LIMIT`). Tests: `tests/test_parallel_uploads.py`, `tests/test_durable_writes.py`,
`tests/integration/test_durable_recovery.py` with `tests/integration/fault_proxy.py` (a loopback
relay that breaks chosen requests) and `upload_child.py` (a separate process that is killed).

### DEF-10: several upload requests at once

One pass reads up to four batches of 50 (the server's batch limit is unchanged) and sends them at
once on separate threads, waiting for all of them before the spool is read again, so two requests
never carry the same event. Each batch is acknowledged or rejected on its own; a failed batch never
undoes another's receipts. The most serious failure decides what happens next: a refusal
(authentication or permission) before a local storage error, and either before a temporary one. A
pass still sends at most ten batches. `SyncWorker(parallelism=1)` keeps the old behaviour available.

### Found by the sustained run: saving stopped after a harmless race

The first 15-minute run (6C.4) stopped saving after about 3.5 minutes and refused every later record
as "Not stored" until the end of the run. Before each record, the spool writer adds up the sizes of
the files in its folder. The upload worker's own transactions create and delete a short-lived
rollback journal there. When the journal disappeared between "list the folder" and "read its size",
the `FileNotFoundError` was taken for a disk failure, and a disk failure stops saving until the
application restarts. Nothing was marked saved and then lost: the dashboard said "local storage
stopped (disk)" and the records were reported as not stored. A scratch stress of 25 records/s with
a second thread committing transactions reproduced it in about 20 s; with the fix it ran 3 minutes
without a stop. A file that vanishes while the folder is measured is now skipped
(`cloud_outbox.Outbox._directory_bytes`); O `test_a_journal_removed_while_the_spool_is_measured_is_not_a_disk_failure`
fails without the fix. The race has existed since 5D; the pilot records do not show whether it ever
happened there.

### Evidence

| Backlog item | Test | Result |
|---|---|---|
| Failure before commit, lost acknowledgement, request cut off mid-body | I `test_failures_before_commit_and_lost_acknowledgements_store_each_event_once`: four batches go at once; one never reaches the cloud, one commits but its answer is dropped, one is cut off halfway, one succeeds. Afterwards 50 synced, 150 pending, 100 on the server; the retry replays the dropped batch and the cloud then holds exactly 200 events, 200 predictions and 200 traffic rows | Passed |
| Malformed events, partial batches, duplicate UUID with changed content | I `test_malformed_and_changed_duplicate_events_are_rejected_alone` (a 50-event batch with one malformed event and one changed duplicate: 48 synced, the two rejected as `validation` and `conflict` and kept for export, the cloud's original unchanged); I `test_store_failures_inside_the_database_roll_back_the_whole_batch` | Passed |
| Kill and restart with pending events; late final metadata | I `test_a_killed_uploader_restarts_without_duplicates_and_closes_its_capture_late`: all four batches of 200 events commit, then the process is killed before any answer arrives, with the capture's final summary still queued. The restarted process replays them (200 events, 200 traffic rows, no duplicates) and then closes the capture with its late summary (completed, 200 flows, 4,000 packets, 3 dropped) | Passed |
| Kill during local writes | I `test_a_process_killed_while_writing_leaves_a_consistent_spool`: the spool passes `integrity_check`; every committed row is whole and unique | Passed |
| Disk full | O `test_a_genuinely_full_spool_stops_saving_and_resumes_when_space_returns`: SQLite itself refuses new pages ("database or disk is full"); saving stops with honest `dropped` results, saved records stay pending, and saving resumes without a restart once space returns | Passed |
| Stalled network and stop deadline | I `test_a_stalled_network_keeps_stop_bounded_and_records_pending_until_it_returns`: with every cloud request hanging, Stop returns in under 6 s, records show as pending, and all of them sync and the capture closes once the network answers | Passed |
| Full queues, denied writes | 5D evidence unchanged (`test_session_cap_reserves_slots_before_enqueue`, `test_handoff_overflow_and_conflicting_identity_are_explicit`, revocation tests); O `test_a_local_disk_error_while_acknowledging_is_retried_without_duplicates` | Passed |
| UI distinguishes dropped, pending, rejected, synced | O `test_the_dashboard_tells_dropped_pending_rejected_and_synced_apart` | Passed |
| Audit bounded during prolonged failure | O `test_audit_entries_stay_within_their_reserve_during_a_long_outage`: 1,000 audit entries keep 96 and count 904 as dropped, capture summaries keep their own 32 slots, the spool stays under 1 MiB, and an unwritable spool makes audit return False rather than raise | Passed |
| Parallel uploads | O `test_parallel_uploads.py`: four requests in flight, every event in exactly one request, a lost answer in one batch replays only that batch, a refusal outranks a temporary error while other receipts are kept, at most ten batches a pass | Passed |

## 6C.3 — Model updates and backup restoration

Code: `model_delivery.py`, `cloud_recovery.py`, new `cleanup_model_objects.py`. Tests:
`tests/test_model_delivery.py`, `tests/test_cloud_recovery.py`,
`tests/integration/test_model_recovery.py`, `tests/integration/test_backup_recovery.py`.

### Changes

- **Interrupted downloads.** A download ended by a crash or power loss leaves a `.part` file of up to
  512 MiB. The next load removes `.part` files older than 10 minutes.
- **Bounded cache.** After a new release verifies, the cache keeps it and the release in use before
  it, and removes older copies (the manifest decides what is active; a later start downloads again).
- **Missing model file.** Storage answers 400 when a published object is missing; the app showed
  "Check the submitted values." It now says "The published model file is unavailable. Contact
  maintenance." Sign-in and membership refusals still reach the sign-in handling.
- **Backup format 2.** `cloud_recovery.py` now exports and restores `public.node_health` and
  `private.retention_run` (the 6B health reports and cleanup audit). Format 1 packages still verify
  and restore without them. `RECOVERY_EXCLUDED` names the two tables a target creates itself
  (`private.algoguard_migration`, `public.deployment_activation_lock`); the lane fails if a future
  table is in neither list.
- **`cleanup_model_objects.py`** (maintainer only; a dry run unless `--yes`). An object in the
  `models` bucket is removed only when no manifest names it, or only `draft`/`orphaned` manifests do,
  when it and those manifests are older than 24 hours, and when no active deployment names it. Each
  object is checked again just before removal, deletion goes through the Storage API, and manifest
  rows are kept as the record.

### Evidence

| Backlog item | Test | Result |
|---|---|---|
| Interrupted cache promotion | O `test_an_interrupted_download_leaves_no_lasting_part_file` (a stale `.part` is removed, a fresh one kept); 5C `test_failed_download_never_promotes_or_deserializes` | Passed |
| Corruption, version mismatch | 5C `test_cached_corruption_and_failed_update_keep_pinned_model`, `test_incompatible_manifest_refused_before_download`, `test_runtime_mismatch_prevents_deserialization` | Passed |
| Missing objects | I `test_a_missing_model_file_fails_safely_and_recovers_when_restored`: a fresh cache refuses with the new message and promotes nothing; the pinned copy keeps working; restoring the same bytes makes the release downloadable again | Passed |
| Expired URLs | I `test_an_expired_download_link_is_refused` (Storage refuses a 1 s link after 2.5 s); O `test_failed_download_never_promotes_or_deserializes[expired]` | Passed |
| Manifest and object permission attacks | I `test_manifest_and_model_object_attacks_are_refused`: an analyst and an administrator account each try to insert or change a manifest, activate a deployment, call `activate_model_publication`, overwrite the active model (POST with `x-upsert`, PUT) and delete it; all are refused and the manifest, deployment and bytes are unchanged. 5B `test_private_storage_requires_readable_published_manifest` covers reads | Passed |
| Concurrent activation keeps one active deployment | 5C `test_independent_publishers_serialize_and_retry_does_not_reactivate`; the pinned-capture test checks one active manifest after a mid-capture publication | Passed |
| Running captures keep their model; new sessions resolve the current manifest | I `test_a_running_capture_keeps_its_model_while_new_sessions_use_the_new_one`: a new model is published mid-capture; every event of the running capture, before and after, is stored with the old deployment, and the next session pins the new one | Passed |
| Restore metadata and private artifacts consistently | I `test_every_application_table_is_backed_up_or_deliberately_excluded`; 5E `test_post_cutover_forward_recovery_...` now restores format 2 | Passed |
| Orphan cleanup deletes only proven unreferenced uploads | I `test_cleanup_removes_only_proven_unreferenced_uploads`: of seven objects, only an old orphaned one and an old unreferenced one are removed; a recent draft, a recent stray, an old superseded (rollback) one and the active model stay; one claimed by a retried publication between plan and removal is re-checked and kept | Passed |
| Post-cutover recovery with both nodes and pending outboxes | I `test_two_computers_with_pending_records_restore_and_replay_under_their_own_users`: two computers, each with a committed-but-unacknowledged record, an unsent record, a queued capture summary and a health report, plus a cleanup audit row. Export, restore (and a replayed restore), and both spools restored separately; each spool holds one owner's records, the other user's token cannot replay them, and replay under the owner returns the original prediction for the committed record and stores the unsent one | Passed |

**Unsupported reverse conversion.** Recovery is forward only: cloud records cannot be converted back
into the SQLite business database, as recorded in 5E. Rolling the app back to `sqlite` mode does
not carry records written in cloud mode; recover them with `cloud_recovery.py` instead.

## 6C.4 — Sustained load and targeted traffic checks

Test: `tests/integration/test_sustained_load.py` (90 s phase in the lane; a 15-minute phase recorded
below). Every cloud request passes through the fault relay with 0.2 s added before forwarding and
again before answering. With it, one request at a time drains a 300-event burst at roughly the
pilot's 65 events/s, so the comparison is like for like.

**DEF-10, measured as the defect was** (one 300-event capture at maximum speed; cloud receipt minus
event time):

| Upload requests | p50 | p95 | max | Last receipt after first event |
|---|---|---|---|---|
| One at a time (before) | 3.00 s | 5.31 s | 5.64 s | 6.37 s |
| Four at once (now) | 2.81 s | **3.57 s** | 3.57 s | 4.03 s |

The pilot's capture 47 recorded p95 5.04 s one at a time; across six runs here, one at a time gave
p95 5.25–6.34 s and four at once 3.13–4.41 s, always within the 5 s budget.

**Sustained phase, 15 minutes** (`test-results/iter6_6c/sustained_6c4.json`): 25 records/s handed
to the upload queue as a capture would, 78 successive paced capture sessions of 10 s alongside, and
the cloud unreachable for 34 s from minute five.

| Budget (5F) | Target | Result | Verdict |
|---|---|---|---|
| p95 upload acknowledgement, online | ≤ 5 s | 3.27 s (p50 1.65 s, max 5.11 s, n = 22,315) | Met |
| Sustained acknowledged events/s per client (L1) | ≥ 20 | 26.5 over the whole 15 minutes, outage included (23,860 events) | Met |
| Outage recovery | all synced ≤ 2 min after reconnect, 0 lost, 0 duplicates | the 1,260-record backlog synced within 10.7 s; records made just after reconnecting waited behind it (p95 10.6 s); 0 lost, 0 duplicates | Met |
| RAM, peak | ≤ 1 GB | 331 MiB (start 237; mean 274 MiB in the first third, 302 MiB in the last) | Met |
| Local state growth | ≤ 50 MB | spool 9.7, 13.5 and 13.6 MiB at one third, two thirds and the end; state folder peak 13.8 MiB | Met |

All 24,460 records the run produced (including the DEF-10 bursts) are in the cloud exactly once,
with one traffic and one prediction row each; nothing is pending, rejected or dropped other than
by the per-capture cap. Threads returned to 7 after peaking at 15 during parallel uploads. The
registry of AlgoGuard's own connections held 168 recently closed entries at the end and evicted none.
Memory rose by about 28 MiB between the first and last thirds; the test process also holds the load
generator's record of 22,534 event ids, and a longer run would be needed to show a plateau.

**First 15-minute run.** It stopped saving after about 3.5 minutes because of the race described
under 6C.2, and was repeated after the fix.

### Found by the sustained run: upload receipts would have filled the spool

The second 15-minute run met every budget, but the spool file reached 28.4 MiB after 24,439
uploads, about 1.2 KiB per uploaded record. On acknowledgement a record keeps a small receipt
for seven days and its payload is cleared, but the cleared space stays inside pages that later
records never reuse. The flow budget is about 49.5 MiB, so at 25 records/s saving would have
stopped as "quota" after about 35 minutes, and a heavy week of ordinary use (3,000 records a day)
would have reached about half of it. The spool now keeps at most the newest 10,000 receipts
(`SYNCED_KEEP`), removing the oldest first so whole pages are freed and reused; pending and
rejected records are never touched. The dashboard's **Synced** figure therefore counts at most the
last 10,000 uploads from this computer (it always covered only the last seven days), and now reads
"committed in the cloud (this computer's recent uploads)"; the cloud remains the record of
everything stored.
O `test_synced_receipts_are_capped_so_the_spool_stays_bounded`.

### Capture exclusion after reconnects and a DNS change

W `test_owned_connections_stay_excluded_after_reconnects_and_a_dns_change` (Windows, Npcap
loopback, `RUN_WINDOWS_CAPTURE_TESTS=1`): the project host name resolves to 127.0.0.1 and then, after
a simulated DNS change, to 127.0.0.2. Three owned requests to each open six new, registered
connections; all 67 of their packets were excluded and none was classified, while six plain
connections to the same two addresses and port were captured as flows. The 5D Windows check of
unrelated HTTPS to the hosted provider (`scripts/check_capture_exclusion.py`) is unchanged.

## Verification

Run on Windows on 2026-11-02 with the local Supabase stack, Python 3.14.6 and the pinned model
runtime:

- Offline suite (`pytest`): **662 passed**, 100 deselected (the integration lane), 516.8 s.
- Integration lane (`pytest -m integration`, `RUN_WINDOWS_CAPTURE_TESTS=1`): **100 passed**, 404.5 s
  (80 before this stage), including the 90 s sustained phase and the Windows capture checks.
- 15-minute sustained run: passed (`SUSTAINED_SECONDS=900`, report
  `test-results/iter6_6c/sustained_6c4.json`).
- `ruff check .`: passed. `npm run typecheck` (pyright): 0 errors.
- The baseline offline run before any change had one failure, `test_stop_is_bounded_even_when_local_storage_stalls`
  (3.3 s against its 3 s limit) while Docker Desktop was starting; it passed three times on a quiet
  machine and in the final run.

## Open items

- **Hosted pilot.** These measurements use the local stack with added latency, not the Tokyo pilot.
  DEF-10, DEF-17 and DEF-19 are re-tested on the pilot in test cycle 2. The maintainer still applies
  the pending 6A/6B migrations (`python cloud_migrate.py --status`, then `--yes`) and publishes local
  model 60; 6C adds no migration.
- **Local write speed.** Each event is its own SQLite transaction with full sync, so a maximum-speed
  burst is also limited by the disk; grouping commits would help bursts further.
- **Signing-key refetch window** (6C.1 limit above).
