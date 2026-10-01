# Stage 5D — Application integration and persistence

Status: **implementation and verification complete; ready for the user's manual
commit.** Updated 2026-09-27. `check_5d.cmd` exited **0** on Windows with no
skipped tests; application and maintenance capture-exclusion evidence is
recorded in §5. Changes are intentionally uncommitted at the user's request.
Existing-installation cutover remains Stage 5E; two-node acceptance remains 5F.

## 1. What this stage delivers

`ALGOGUARD_DB_MODE=supabase` now runs the isolated cloud pilot. `app.py`
serves `cloud_app.create_cloud_app()` in that mode and never opens, creates, or
migrates the SQLite business database; `sqlite` mode is unchanged and remains
the default until cutover. One process never mixes the two stores.

| Module | Role |
| --- | --- |
| `cloud_app.py` | Flask application for the pilot: login, every page, JSON API, export |
| `cloud_auth.py` | Email/password login, JWKS-verified identities, refresh, memory sessions |
| `cloud_repository.py` | Authenticated Data API contract (extended: ordering, ranges, search, RPCs) |
| `cloud_outbox.py` | Owner-restricted SQLite spool, bounded handoff, persistence states |
| `cloud_sync.py` | Upload worker: batches, backoff, dedup reconciliation, audit entries |
| `cloud_monitor.py` | Live Monitor for cloud mode: pinned model, nonblocking persistence |
| `cloud_connections.py` | Registration and capture exclusion of AlgoGuard's own sockets |
| `maintenance_connections.py` | Exact cross-process database socket leases and transparent maintenance relay |
| `model_delivery.ActiveModelStore` | Process cache of the verified model; manifest checked every time |
| Migrations `20260927010000`–`030000` | Capture lifecycle, report composition, audit and log filters |

Templates are shared with the SQLite application (`config.CLOUD_MODE` branches),
plus cloud-only `accounts.html`, `nodes.html`, `reports.html`,
`report_detail.html`, and `cloud_message.html`.

## 2. Operating contract as implemented

| Situation (plan table) | Behaviour |
| --- | --- |
| Network loss during an authorized capture | Capture continues with its pinned model. Persistent flows become durable pending; uploads retry with capped exponential backoff (1 s doubling to 60 s, 50–100 % jitter). |
| Access token expires, refresh unavailable | The upload worker refreshes 60 s before expiry while online. If expiry is reached anyway, the capture stops locally, the identity is cleared, pending records stay, and online login is required. A failed refresh is not retried for 15 s while the token is still valid, so a stalled network cannot make every request wait for a refresh timeout. |
| Browser reload with a valid session | The page reattaches to the running capture. Refresh is serialized by a per-identity lock shared by page requests, polling, and the upload worker, so a rotated refresh token is spent once. |
| Application restart | Every session is forgotten (tokens are memory-only); the browser is sent to login. A cached model never permits offline login. Durable pending records survive. |
| Reconnect | Membership is rechecked (`current_node_access`, every 15 s and before each capture) and records replay only under the identity of the user who produced them. |
| Logout or account switch | The old capture stops, its tokens are cleared, remote logout uses `scope=local` (other installations' sessions for the same user are unaffected), and pending records keep their original owner. |
| Revoked membership or permanent rejection | Uploads stop with the `blocked` status on the dashboard and monitor; a running capture stops; records remain **pending** (not rejected) and exportable. The user stays signed in to see this, and **Retry uploads** clears the block after an administrator restores access. |

Persistence states reported by the UI and API: `session_only`, `in_memory`
(not yet saved), `durable_pending` (on this computer), `synced` (cloud
acknowledged `committed`), `dropped` (session cap, full handoff, or local
capacity/disk), `rejected` (validation/conflict; kept for export). Only
`synced` is described as saved to the cloud.

Initial limits, unchanged from the plan: 1,000-event handoff, 100 MiB total
spool including journals, batches of at most 50, one-second flush, ten-second
request deadlines, 300 reserved flow slots per capture (reserved before
enqueue; a resubmitted UUID uses no slot), seven-day expiry. Summaries have
separate reserved capacity: 32 capture summaries and 96 audit entries, each at
most 4 KiB, outside the flow quota. A quota stop resumes automatically once use
falls below 80 %; a disk error stops persistent acceptance until restart.

### 2.1 API contract change

`POST /predict` (cookie with `X-CSRF-Token`, or `Authorization: Bearer`)
returns the classification plus:

```json
{"event_uuid": "…", "persistence": "durable_pending", "synced": false,
 "prediction_id": null, "alert_id": null, "alert_expected": true}
```

Database IDs appear only after acknowledgement: `GET /api/events/<event_uuid>`
returns `persistence`, `synced`, and, once synced, `prediction_id`/`alert_id`.
Monitor status rows carry `event_uuid`/`persistence`; the session carries
`synchronization` counts, `upload` state, the pinned `deployment`, and
`capture_id`. Bearer tokens are accepted only on JSON endpoints; HTML pages use
only the opaque browser session. Callers and tests were updated.

## 3. Acceptance coverage

Evidence key — **O**: offline suite in this repository (fake Supabase service,
real app/auth/repository/outbox/worker code over HTTP); **I**: integration lane
against the local Supabase stack; **S**: scratch PostgreSQL 16 run of all
migrations with SQL behaviour checks (supplementary, not acceptance); **B**:
Chromium walk; **W**: Windows evidence script.

### 5D.1 Integrate cloud login and scoped application reads

| Item | Evidence | Status |
| --- | --- | --- |
| Login/logout, profiles, accounts, enrollment, role checks use cloud identity; historical profiles have no login | O `test_login_uses_cloud_identity…`, `test_deactivated_and_historical_profiles…`, `test_enrollment_on_login_and_administrator_approval`, `test_administrator_account_management…`; I `test_cloud_app_end_to_end_on_the_local_stack`, `test_bearer_identity_and_current_roles` | O passed; I passed |
| Opaque HttpOnly/SameSite cookie; tokens in memory; CSRF on cookie writes | O `test_login_uses_cloud_identity…`, `test_cookie_writes_require_csrf…`, `test_opaque_session_has_no_tokens…` | O passed |
| Independent bearer API; no shared token state across concurrent requests | O `test_concurrent_bearer_requests_never_swap_identities`; I end-to-end bearer section | O passed; I passed |
| Dashboard, alerts, logs, reports, filters, prediction use authenticated repository operations with local-node defaults | O `test_every_page_reads_through_the_cloud_and_never_opens_sqlite`; I `test_new_repository_queries_parse_in_postgrest`, end-to-end; B | O/B passed; I passed |
| Controlled network-failure states without recursive logging; restart needs online login | O `test_network_failure_pages_are_controlled…`, `test_restart_requires_online_login_even_with_a_cached_model` | O passed |

### 5D.2 Bounded local outbox and upload worker

| Item | Evidence | Status |
| --- | --- | --- |
| Separate owner-restricted spool; immutable event identity | O `test_cloud_outbox.py`; Windows ACL via `icacls` (owner and SYSTEM only) | O passed; Windows ACL verified (owner and SYSTEM only) |
| Limits and reserved capacity | O `test_cap_reserves_before_enqueue…`, `test_flow_capacity_leaves_reserved_lifecycle_space`, `test_session_cap_reserves_slots_before_enqueue` | O passed |
| Distinct persistence states; memory never labelled saved | O `test_manual_prediction_reports_queued_state…`; B feed labels | O/B passed |
| Backoff, lost-ack reconciliation, permanent failures not retried, one bad event isolated | O `test_lost_acknowledgement_is_reconciled…`, `test_one_invalid_event_does_not_reject_its_batch`; I `test_lost_acknowledgement_restart_and_revocation` | O passed; I passed |
| Overflow/disk failure stops persistence, classification continues; durable events survive restart | O `test_full_local_storage_stops_saving…`, `test_restart_with_pending_events_replays_only_under_the_original_user` | O passed |

### 5D.3 Capture lifecycle, refresh, and synchronization

| Item | Evidence | Status |
| --- | --- | --- |
| CSV, PCAP, live, and manual share the event contract; no blocking | I `test_cloud_app_stack.py`: real Auth, private model download, manual/CSV/PCAP/live classification and cloud receipts; O outage/stop tests | O/I/W passed: CSV, manual, PCAP, and actual Windows live capture |
| Event UUID and explicit persistence in responses | O `test_manual_prediction_reports_queued_state…`; I end-to-end | O passed; I passed |
| Pinned deployment per capture; reattach; serialized refresh | O `test_csv_capture_pins_deployment…`, `test_concurrent_refresh_spends_the_refresh_token_once` | O passed |
| Immediate close, ≤5 s local drain, terminal status independent of sync | O `test_stop_is_bounded_even_when_local_storage_stalls` (stalled writer: stop returned in < 3 s with a 1 s drain) | O passed |
| Expiry stops locally and requires login; logout/account switch stops capture | O `test_expiry_without_refresh…`, `test_logout_stops_the_capture…` | O passed |
| Restart/reconnect reconciles without reopening or cross-user replay | O restart test; S terminal-state checks; I `test_capture_terminal_state_and_late_summary`, `test_capture_lifecycle_and_reports_through_the_repository` | O/S passed; I passed |
| Actionable revocation; bounded, exportable pending records | O `test_revoked_membership_stops_capture…`; I end-to-end revocation section | O passed; I passed |

### 5D.4 Exclude AlgoGuard's cloud connections narrowly — see §5

| Item | Evidence | Status |
| --- | --- | --- |
| App-owned API/Auth/Storage/JWKS connections identified, including reconnect and capture fallback | O `test_cloud_connections.py` (registration before SYN, TLS adoption, owned openers for repository, model download, and JWKS) | O passed |
| Monitored maintainer database connections identified across processes and reconnects | I/W `test_separate_maintenance_process_is_excluded`; O `test_maintenance_connections.py` | Passed; unrelated database traffic stays visible |
| Web-port exclusion kept; no HTTPS-wide or shared-IP exclusion | O `test_live_capture_excludes_owned_cloud_traffic_but_keeps_other_https` | O passed |
| Upload/download traffic excluded while unrelated HTTPS to the same provider stays visible | W `scripts/check_capture_exclusion.py` | W passed |
| Attribution method, races/limitations, excluded packet counts recorded | §5 below; counts from W | Method and Windows counts recorded |

### 5D.5 Verify the integrated application

| Item | Evidence | Status |
| --- | --- | --- |
| All pages/sources against the real local API with real Auth users; no privileged access | I `test_cloud_app_stack.py` (real login, published model, all pages including Administrator accounts, manual, CSV, PCAP, live, bearer, revocation, logout) | I/W passed |
| Network loss, expiry, queue/disk full, stop deadline, lost response, refresh concurrency, restart | O tests listed above | O passed |
| Old baseline maintained; no store mixing; no user-credential migrations | O `test_app_entry_point_serves_the_cloud_app_without_touching_sqlite`, full legacy suite; migrations only via CLI/`cloud_migrate.py` | O passed |
| Python, Node, lint, browser checks; this document | §4 and §6 | Passed; user will commit manually |

## 4. Earlier supplementary verification — 2026-09-27

Run in a Linux container without Docker registry or PyPI access, so the model
lock could not be installed. The container has Python 3.11.15 with
scikit-learn 1.8.0, NumPy 2.4.4, pandas 3.0.2, SciPy 1.17.1, and joblib 1.5.3.
A scratch-only pytest plugin (not committed) substituted these for the
Python 3.14.6 lock so that model-training tests could run. Scapy and psycopg2
were unavailable. **These numbers are therefore not the pilot-runtime
evidence.**

- Offline suite: **431 passed, 21 skipped, 30 deselected, 82.2 s**. Skips are
  modules that need Scapy, psycopg2, or requests. The Stage 5D-focused files
  (`test_cloud_app.py`, `test_cloud_connections.py`, `test_cloud_outbox.py`,
  `test_cloud_auth.py`, `test_cloud_repository.py`) passed **58 tests in
  60.7 s**.
- Ruff: passed. Node polling tests: **11 passed** (one new cloud-mode test).
- Scratch SQL (PostgreSQL 16.13 with stub `auth`/`storage`/`extensions`
  schemas): all **14 migrations applied** in order, one transaction each, and
  every behaviour check passed as the `authenticated` role with PostgREST-style
  claims. The checks cover the capture lifecycle, reports, idempotent audit,
  filter options, cross-node refusal, administrator observation, and
  revocation. A deliberate mutation (removing the audit conflict mapping) was
  detected. This is supplementary: plain PostgreSQL does not test Auth, the
  Data API, or Storage.
- Chromium walk at 1366 px and 390 px against the fake service: login,
  dashboard, predict, monitor (CSV, persist all), alerts, report creation and
  detail, logs, nodes, accounts, and logout. **11 of 11 checks passed**, with
  no console errors, failed requests, HTTP errors, or horizontal overflow.
  Feed rows moved from Pending to Synced, and the Saved column fits at 1366 px.

Container evidence (logs, SQL scripts, screenshots) is archived locally in
`check-output/5d-container/`.

## 5. Capture exclusion (5D.4)

**Attribution method.** Every HTTP(S) connection the analyst application
makes — Data API, Auth token/refresh/logout, JWKS, Edge Function, Storage
signing and model download — goes through `cloud_connections.owned_opener()`.
Its connection factory binds each socket to an ephemeral port *before*
`connect()`, registers `(local port, remote IP, remote port)`, then connects;
the SYN is sent after registration, so there is no startup race. TLS wrapping
re-points the entry at the `SSLSocket` that owns the descriptor. Closed sockets
remain registered for a bounded 120 s for late FIN/RST and
retransmissions. `LiveCaptureSource` excludes a TCP packet only on an exact
match, in Python, for filtered and unfiltered (fallback) captures alike; the
web-port BPF/Python exclusion is unchanged. No Windows process lookup is
needed, so there is no polling interval and no extra privilege.

**Limitations.**

- DNS lookups for the project host are made by the Windows DNS Client
  service, not by AlgoGuard, and remain visible as ordinary UDP 53 flows.
- Closure is observed lazily, so retention starts when the registry next
  checks that entry. If the server closes first (no local TIME_WAIT), another
  local process could reuse the same four-tuple within the retention window,
  and its packets would be excluded. This is rare, and bounded to 120 s.
- Maintainer database tools use `maintenance_connections.connect_database`.
  A transparent loopback relay records its listener and exact outbound socket
  in `.algoguard/connections/leases.sqlite3` before connecting. Capture reads
  committed leases on each candidate packet, with no polling interval. The
  registry contains no credentials; its directory is restricted to the Windows
  account and SYSTEM. Both processes must use the same account and
  `ALGOGUARD_STATE_DIR`. TLS remains between libpq and the database, preserving
  the original hostname and configured certificate policy. The relay supports
  reconnects/cancellation connections and fails closed if attribution cannot
  be written. Leases refresh every 15 seconds and expire 120 seconds after
  their last refresh; a crashed process therefore leaves only bounded stale
  exclusions. Exact ephemeral-port reuse within that window remains a limit.
  The relay allows at most eight simultaneous connections and the registry
  holds at most 4,096 leases. Single-host TCP connection strings are supported;
  multi-host and Unix-socket strings are rejected explicitly.
- Third-party database clients and maintenance HTTP clients outside the
  analyst application's owned opener remain ordinary visible traffic. The
  exclusion requirement covers AlgoGuard's monitored database tools, not all
  applications that happen to reach the provider.
- A registry holds at most 4,096 entries. The oldest entry is evicted and
  counted, which is far above the pilot's request rate.

**Recording the evidence.** On the pilot machine, from an Administrator terminal
with Npcap installed:

```powershell
python scripts\check_capture_exclusion.py --interface "<capture interface>" --requests 5
```

It requests the public JWKS document five times through the owned opener and
five times over plain sockets to the same host and port, then prints packet
counts (`packets_excluded_cloud`, `packets_captured`, dropped, DNS flows) and
exits 0 only if no owned connection was classified and at least one unrelated
connection was. Paste the JSON here:

```text
Windows run, 2026-09-27, default Npcap interface, pilot HTTPS endpoint:
owned_connections: 5
unrelated_connections: 5
packets_captured: 1454
packets_excluded_total: 89
packets_excluded_cloud: 89
packets_dropped: 0
owned_flows_classified: 0
unrelated_flows_classified: 5
passed: true
```

The Windows check passed: all five unrelated connections to the same provider
remained visible, and no owned connection was classified. The complete report
is saved in `check-output/5d/13-capture-exclusion.txt`. This verifies the
analyst-process attribution path.

The separate maintenance-process check also passed on Windows/Npcap loopback:
two real PostgreSQL connections (including reconnect) were excluded, with
**131 excluded packets**, no owned flow classified, and an unrelated connection
to the same PostgreSQL endpoint visible. The fixture runs only against the
disposable local stack. See `tests/integration/test_maintenance_capture.py` and
`check-output/5d/15-capture-sources.txt`. TLS relay tests verify a trusted server
certificate succeeds and a wrong hostname is refused.

## 6. Local acceptance procedure

1. Start Docker Desktop, then run `check_5d.cmd` from the repository root.
   Results go to `check-output\5d\`; any failed step or skipped integration
   test makes it exit non-zero.
2. Run §5's exclusion check and record its report. The one-shot check also
   enables the real Windows live-source and separate-process database tests.
3. Application acceptance uses real local Auth users, private Storage, and the
   API: manual prediction, CSV, PCAP, and actual Windows live capture all pass
   through the cloud application and verify committed receipts. An isolated
   remote two-installation smoke run, including an administrator approving the
   other installation, is Stage 5F.
4. Record the totals and runtimes from `00-summary.txt` here, then commit the
   stage manually.

```text
Final Windows run, 2026-09-27 (Python 3.14.6 and the pinned model runtime):
check_5d.cmd: exit 0; every step in 00-summary.txt returned 0.
Clean reset: all 14 migrations applied successfully.
Unit tests: 484 passed, no skips, 62 deselected, 267.51 s.
Lint: all checks passed.
Node: 11 passed, no skips, 151.9635 ms.
Real local-stack integration: 62 passed, no skips, 484 deselected, 89.12 s.
Focused 5D integration: 6 passed, no skips, 42.41 s.
Migration status and JWKS checks: passed.
Windows analyst and separate-process database traffic exclusion: passed (§5).
Windows spool ACL: inheritance disabled; only owner and SYSTEM grants.
```

Final logs are in `check-output/5d/`, including `19-windows-acl.txt` for the
permission check. Earlier intermediate results are in `check-output/5d-pre-final/`.
The first unit run passed 480 tests but timed out waiting for 400 predictions
in the storage-full test. That test now directly proves classification continues
after capacity is reached, without imposing a hardware-dependent throughput
threshold. A later intermediate run passed all Python tests but had a lint
formatting failure and a batch-script exit-code error caused by editing the
running script. The final unchanged-script run above supersedes both failures.

PCAP acceptance replayed four generated flows and verified four cloud receipts.
Live acceptance used actual Npcap loopback packets, verified cloud persistence,
and checked terminal stop within six seconds (five-second local-drain budget
plus request overhead). Fixtures create and clean up only disposable local-stack
records; no application migration or model publication was applied remotely.

## 7. Decisions made where the plan left a choice

- **Revocation does not sign the user out.** Uploads and capture stop, but the
  session stays so the user can read the status and export records.
- **Expiry reached without refresh signs the user out**, because the operating
  table requires online re-login.
- **Logout uses `scope=local`**, so the same user's other installations keep
  their sessions.
- **Cloud mode requires a loopback `ALGOGUARD_HOST`**, because the console is
  plain HTTP. This partly resolves the 5A follow-up about non-loopback hosts.
- **Audit entries** (login, logout, monitor start/stop, reports,
  administration) go through the reserved outbox capacity and the new
  `append_system_log` RPC, keyed by a client UUID so retries never duplicate
  them. Each flow's `flow_accepted` audit row is still written atomically by
  `store_flow_batch`.
- **Account and membership forms carry a request UUID** rendered with the page,
  so a double submission replays rather than duplicates.
- **Reports are node-scoped** to this installation; cross-node report views are
  6B.

## 8. Recovery and rollback

- **Code rollback:** set `ALGOGUARD_DB_MODE=sqlite` and restart. The SQLite
  application and its database are untouched by cloud mode. This does not
  recover records written in cloud mode; after cutover, follow the forward
  recovery procedure in [Stage 5E](05e-cutover.md) before changing authority.
- **Pending records:** use the dashboard's **Export pending records** (JSON
  with events and queued summaries) while signed in. The spool at
  `.algoguard/outbox/outbox.sqlite3` is owner-restricted and can also be copied
  by the same Windows account. Do not delete it while records are pending.
- **Node identity:** `.algoguard/node-id`. Deleting it enrolls a new node that
  needs fresh approval, and it orphans pending records owned by the old node
  ID. Keep it with the spool.
- **Model cache:** `.algoguard/models/`. It is safe to delete; the next start
  downloads and verifies the active model again.

## 9. Carried forward

- **5E:** import and cutover. The pilot keeps writing only new records.
- **5F:** two-node measurements, including the 300-flow cap under pending
  writes, sync delay, and backlog age, measured from Manila against the Tokyo
  pilot (~104 ms RTT).
- **6B:** cross-node views, node health, and audited retention. Automatic
  cleanup of synced spool rows (payloads are cleared on acknowledgement; rows
  expire after seven days) belongs there.
- **6C:** sustained-load and DNS-change exclusion checks.
- **6D:** removing the SQLite path from analyst startup.
