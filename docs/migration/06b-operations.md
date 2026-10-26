# Stage 6B — Administrator visibility and retention

Status: implemented and verified on the local Supabase stack on 2026-10-27. Three migrations
(`20261027010000_node_health.sql`, `20261027020000_node_read_once.sql`, `20261027030000_retention.sql`) are
written and tested locally; a maintainer applies them to the hosted pilot (see Open items). Cleanup is
**manual only**: no scheduled maintenance is enabled in this stage.

## Outcome

| Sub-stage | Result |
|---|---|
| 6B.1 Cross-node alerts, logs and reports | Administrators choose this computer, one computer or all computers; every row names its computer and shows when the cloud received it next to the event time. Analysts see only their approved computers, whatever the URL says. At 130,000 alerts every page query runs in under 50 ms after one policy change. |
| 6B.2 Node health | Each signed-in, approved installation reports its version, active model, capture state and pending/dropped/rejected upload counts every 60 s. The **Computers** page (administrators) marks a report stale after 3 minutes by server receipt time. No remote start/stop exists. |
| 6B.3 Audited retention and capacity | A **Retention** page (administrators) previews what a cleanup would remove and keep, shows database capacity and clocks to check, and runs a cleanup in bounded, audited, replay-safe batches. Local unsent events expire after 7 days and show as expired, never as synced. Cleanup and restore were rehearsed on fixtures. |

## 6B.1 — Cross-node alerts, logs and reports

Code in 2a8e75e.

- **Scope selector.** Alerts, System Logs and Reports take `?node=`: empty means this computer, `all` means every
  computer the account can read, and a computer's id means that computer. Analysts see only the computers they
  hold an approved membership of. A hand-edited URL for any other computer shows nothing, with a notice that
  the computer is not visible to this account. Row security in the database enforces the same rule for direct
  API calls; the selector only offers what the database would return anyway.
- **Origin and timing.** Each row names its computer ("this computer" for the local one). Rows also show when
  the cloud received them. When that is more than a minute after the event's own time, the row adds "N min
  after the event", so a delayed upload is never mistaken for a late detection.
- **Changes stay local.** An administrator can view another computer's alerts but cannot review them from here
  ("View only"), and can select only this installation's own alerts for a report.
- **Performance.** `test-results/iter6_6b/perf_6b1.md` loads 130,000 alerts with their traffic and predictions
  on three nodes, plus 130,000 logs and 2,000 reports. This is the upper end of the 5F estimate of the pilot's
  remaining room. It then times the ten queries these pages send. Row policies that called `can_read_node`
  once per row took 26.7 s for page 200 of all alerts, and 9.6 s for an analyst asking for another node.
  `20261027020000_node_read_once.sql` evaluates the same rule once per query (`private.readable_node_ids()`
  as an InitPlan). Every query then takes 0.5–43.2 ms. The read rule itself is unchanged, and the
  row-security tests in the integration lane pass.

## 6B.2 — Node health without remote capture controls

Code in 1bf8dd0.

- **What is reported.** `report_node_health(p_node_id, p_health)` accepts the app version (`0.6.0+<commit>`),
  the active deployment, the capture state, and the pending, dropped and rejected upload counts. It
  rejects any other key. The database stamps `received_at` with its own clock, which also becomes the computer's
  `last_seen_at`.
- **Who may report.** Only a signed-in account with an approved membership of an approved computer. Before
  sign-in an installation reports nothing, so offline startup never registers anonymously. Logging out stops
  the heartbeat. A refused report never stops uploads, and the next attempt waits for the normal interval.
- **Stale means stale.** The Computers page shows each computer's last report. A report received more than 180 s
  ago is marked Stale, whatever capture state it last claimed. Stale and revoked computers stay listed.
- **No remote control.** The page has no start/stop controls, and no unattended device credential was added.

## 6B.3 — Audited retention and capacity reporting

Code in this stage's commit: `20261027030000_retention.sql`, the `/retention` route and `templates/retention.html`,
`cloud_outbox.py`, and the tests `tests/test_retention.py` and `tests/integration/test_retention.py`.

### Timestamp roles

| Role | Columns | Used for |
|---|---|---|
| Server receipt (decides age) | `network_traffic.recorded_at`; `prediction`, `alert`, `report`, `system_log` `.created_at`; `ingest_event.received_at`; `capture_session.closed_at` | Retention cutoffs, staleness, "Received" in lists |
| Installation clock (shown only) | `ingest_event.event_time`, `alert.detected_at`, `prediction.prediction_timestamp`, `system_log.timestamp` | What people read as the event time |

A wrong installation clock can therefore neither delete evidence early nor keep it forever. Clocks that look
wrong are **quarantined for review**, not trusted. The preview counts, per computer, events whose own time is
more than 5 minutes after the cloud received them or more than 8 days before. These records are still removed
by receipt time like any other.

### Policy

- **Operational records: 30 days.** Flows (traffic, prediction, alert, the flow's own log rows and its event
  anchor), standalone log entries, and closed capture sessions that no stored flow refers to.
- **Reports: 90 days.** A report that is kept keeps every alert it cites, together with that alert's whole flow,
  even when the flow is older than 30 days. A flow is removed only after no report links it any more.
- **Never touched:** capture sessions that are still open, model manifests, deployments, training records,
  stored model files (including the verified rollback artifact), node health and memberships. The preview lists
  the model releases kept by status.
- **Cleanup audits** (module "Retention" in System Logs) are kept as long as reports (90 days).
- The database accepts 30–3650 operational days and 90–3650 report days; the app uses the initial 30/90.

### How a cleanup runs

1. **Preview (dry run).** `retention_preview()` returns what would be removed (reports, flows, alerts among them,
   other logs, closed sessions) and what is held: alerts kept by reports, and open sessions. It also returns
   the suspect clocks per computer, the database size and the size of each table, row counts, the oldest record
   per table, and the model releases kept. The page shows the database's share of the plan's 500 MB, with a
   warning at 50% and a pressure notice at 80%. A preview changes nothing.
2. **Apply in bounded batches.** `retention_apply(request_id, batch_size)` removes at most `batch_size` of each
   kind per call, in an order that never breaks a relationship: expired reports and their links, then whole
   flows (event anchor, the flow's logs, alert, prediction, traffic), then standalone logs, then closed sessions.
   Only one cleanup runs at a time (advisory lock). Each batch returns what it removed and whether more remain.
   The page sends up to 20 batches of 500 per click, then says "More remain" if there are any.
3. **Audit and replay.** Only administrators may preview or apply; analysts get 403 from the database, and the
   page redirects them. Every batch writes a System Log entry naming the administrator and the counts, for
   example "Removed 412 flows (37 alerts), 90 log entries, 0 reports and 1 closed capture sessions; policy 30/90
   days". Every batch is also recorded in `private.retention_run`. A repeated request (same id, same
   settings) returns the first result instead of deleting again. The same id with different settings is a 409
   conflict. Batch ids derive from the form's id, so submitting the form twice does nothing new.
4. **Least privilege.** The cleanup functions belong to `algoguard_retention_owner`. This role cannot log in,
   is not a superuser, and does not bypass row security. It may select and delete on the eight operational
   tables, insert audit rows and read model manifests. It cannot change identities, memberships or models.

### Cleanup and restore rehearsal (fixtures, local stack)

`tests/integration/test_retention.py` runs against `supabase start`. It stores six flows through the normal
upload path, then moves the receipt times of three of them back 45 days and of one back 5 days. One flow is
stored with an installation clock 10 days slow. One old flow is cited by a report from 10 days ago, and another
by a report from 100 days ago. The fixtures also include a 40-day-old standalone log, a closed 40-day-old session
and a session still open. A backup of one old flow is taken first. Results:

- An analyst gets 403 for both preview and apply, and periods below 30/90 days are refused (400).
- The preview counts the expiring report and at least two expiring flows, the alert held by the recent report,
  the open session and the computer with the slow clock, and deletes nothing.
- With batch size 1, each batch removes at most one report, flow, alert and session. Replaying a batch returns
  the same counts with `replayed: true`, and the same id with another batch size is refused (409). Batches
  continue until `more` is false.
- **Removed:** the plain old flow (traffic, prediction, alert, logs, event anchor), the flow cited only by the
  100-day-old report, that report, the old standalone log and the closed session.
- **Kept:** the old flow cited by the recent report (and the report and its link), the recent flow, the flow
  with the slow clock (its receipt time is recent), the recent log and the open session.
- **Restore:** the backed-up flow is reinserted in dependency order. The event-group check validates it again,
  and its analyst can read it through `alert_detail` as before.

The offline suite covers the page: preview figures, two batches of size 1, replay, the 20-batch limit, and
analysts being refused.

### Local pending events (outbox)

Events that are not uploaded within **7 days** expire, inside the outbox's 100 MiB limit. An expired event
becomes `rejected` with reason `expired`, never `synced`. It loses its payload to free space but stays counted
and exportable for 28 days. The dashboard shows it under Rejected as "N expired: not uploaded within 7 days",
and node health reports it among rejected events. A capture session's reservation is released only when no
pending event still uses it. Events the cloud rejected keep their own reason, and synced records are removed
after 7 days as before.

## Verification

- Offline suite (`pytest`), ruff, and pyright (`npm run typecheck`) pass.
- The Retention page was opened in Microsoft Edge (headless), signed in as the offline suite's synthetic
  administrator against the fake backend with the fixtures above. The confirmation prompt appeared, one click
  removed the two expired flows and the old log while keeping the cited alert, and the batch appeared under
  Recent cleanup batches. The page fits a 400 px screen without sideways scrolling. Screenshots:
  `test-results/iter6_6b/ui_6b3_retention_*.png`. The table sizes there come from the fake backend, not
  PostgreSQL.
- Integration lane (`pytest -m integration` with `RUN_WINDOWS_CAPTURE_TESTS=1`) passes against the local stack:
  80 tests, including row security, the schema check that every privileged function belongs to one of the two
  narrow roles with a pinned `search_path`, the node health tests and the retention rehearsal.

## Open items

- **Hosted pilot:** a maintainer applies the pending migrations with `python cloud_migrate.py --status` and then
  `python cloud_migrate.py --yes`. These are `20261025010000` (6A.3), `20261027010000`, `20261027020000` and
  `20261027030000`. Until then the hosted pilot cannot serve the Computers or Retention pages, and
  installations' health reports are refused without affecting uploads.
- **Scheduling stays off.** Cleanup is run by an administrator from the Retention page. Scheduled maintenance
  can be considered after a cleanup has been run and checked on the hosted pilot.
- **Retention periods** are constants in `cloud_app.py` (`OPERATIONAL_DAYS`, `REPORT_DAYS`), not a setting.
  Changing them is a code change, reviewed like any other.
