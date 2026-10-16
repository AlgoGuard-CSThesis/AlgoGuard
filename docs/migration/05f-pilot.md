# Stage 5F — Two-node pilot verification

Status: **accepted with documented limits** (2026-10-16). The physical
two-computer pilot ran against the hosted pilot from 2026-10-08 to 2026-10-16.
Every isolation, delivery, recovery and revocation check passed. Seven of the
nine performance budgets were met (live lag was recorded on PC B only). One
budget was missed on one run, and one could not be demonstrated with the paced
sources. Both are recorded in §4 and
§7 as limits rather than re-targeted. The maintainer closed Stage 5F on that
basis on 2026-10-16. Iteration 5 closes with this record and the
[Iteration 5 summary](05-iteration-summary.md).

The procedure is in [the two-computer pilot runbook](05f-two-computer-runbook.md),
which has been corrected with what this pilot taught (§5).

## 1. Setup

**Target.** Hosted pilot `gswyqmznjwbonassteco` (Tokyo, ap-northeast-1, Supabase
Free plan), confirmed by the maintainer. The hosted project had no schema before
the pilot; all earlier 5B–5E evidence used the local stack. On 2026-10-08 the
maintainer applied all 15 migrations with `cloud_migrate.py --yes` (`--status`:
15 applied, 0 pending), deployed the `account_admin` Edge Function
(`--no-verify-jwt`; the function verifies the bearer itself), disabled public
sign-up, and bootstrapped the Administrator. Signing keys are ES256 (JWKS).

**Model.** Local model 18 (Stacking Ensemble, trained 2026-10-08 on
`datasets/algoguard_big.csv`, F1 95.07%) was published as publication
`62447bdc-976f-42d7-b02b-65963f2b028b`: manifest 1, deployment 1, object
8,558,646 bytes, SHA-256 prefix `9c9f0fb18524eeda`, Python 3.14.6, workflow
`stacking-five-v3`. Exactly one deployment was active throughout. Models 6 and 12
were refused because they predate Stage 5C and carry no `training_runtime` stamp.

**Accounts and nodes** (no passwords recorded):

| Role | Account | Node | Membership |
|---|---|---|---|
| Administrator | `cel.hendrix` | PC A (`SanMiguel`) | approved; used for the 2026-10-15 runs and for Test F |
| PC A analyst | `celpca` | PC A `d535e2fd-8392-4876-a945-c8d9c5184049` | approved 2026-10-08 07:11:10Z, default |
| PC B analyst | `bryntpcb` | PC B `265f767e-f540-4f71-bf36-c7364f99ba59` (`LAPTOP-PNL5IIAJ`) | approved 2026-10-08 07:06:04Z, default |

The two node UUIDs differ and each persisted across every restart from
2026-10-08 to 2026-10-16, including the deliberate restart in Test E.

**Code.** Tests A–D ran on commit `4c9666c` on both PCs (PC B cloned from
GitHub). The clock-skew fix `940e082` (2026-10-10) changed only sign-in token
verification. The 2026-10-15 PC A runs and all 2026-10-16 work ran on `940e082`.
PC B pulled `9e18f30`, which adds only the test-cycle scripts, results and
documents on top of `940e082`. Model packages are locked and identical on both
PCs. Non-model packages are not pinned exactly (finding 10).

**Hardware and network.**

| | PC A (coordinator) | PC B |
|---|---|---|
| Windows | 11 Home Single Language, build 26200 | 11 Home Single Language, build 26200 |
| CPU | Intel Core i5-13420H, 8 cores / 12 threads | AMD Ryzen 5 7520U, 4 cores / 8 threads |
| RAM | 15.7 GiB | 7.2 GiB |
| Python / Npcap | 3.14.6 / 1.88 | 3.14.6 (the model's runtime check passed) / not recorded |
| Network | Wi-Fi, Intel Wi-Fi 6 AX203 | Wi-Fi |
| State folder | `C:\AlgoGuardPilot\state-PC-A` | `C:\AlgoGuardPilot\state-PC-A` on PC B's own disk (finding 13) |

HTTPS timing to the public JWKS endpoint, ten samples each (median / p95, ms):

| | Connect | First byte | Total |
|---|---|---|---|
| PC A start (2026-10-08) | 51.9 / 67.7 | 233.2 / 265.3 | 233.4 / 265.5 |
| PC A end (2026-10-16) | 52.6 / 69.3 | 236.2 / 252.1 | 236.3 / 252.3 |
| PC B start (2026-10-08) | 52.1 / 73.4 | 206.4 / 351.1 | 206.5 / 351.2 |
| PC B end (2026-10-16) | 61.6 / 91.9 | 259.3 / 337.7 | 259.4 / 337.8 |

## 2. Acceptance targets

Agreed by the maintainer on 2026-10-08, before Test A. Live lag is measured from
a flow's last packet, so it includes the 15 s idle timeout
(`IDLE_TIMEOUT_SECONDS`).

| Budget | Target |
|---|---|
| p95 upload acknowledgement (pending → synced, online) | ≤ 5 s |
| Sustained acknowledged events per client | ≥ 20 events/s |
| Average live event-to-verdict lag | ≤ 20 s |
| Live capture packet drop | ≤ 1% |
| AlgoGuard process CPU, peak | ≤ 50% of the machine |
| AlgoGuard process RAM (working set), peak | ≤ 1 GB |
| Local state-folder growth per PC, whole pilot | ≤ 50 MB |
| Outage recovery | all pending synced ≤ 2 min after reconnect; 0 lost; 0 duplicates |
| Revocation / restore | refused on the next action; works again after restore |

## 3. Results

Cloud figures are read-only queries of the hosted pilot by capture ID. Upload
delay is cloud `received_at` minus client `event_time`, so it includes any client
clock offset. PC B's clock was at least 1.3 s behind on 2026-10-16 (capture 12's
first event is stamped 1.3 s before the cloud opened that capture), which
overstates PC B's 2026-10-16 delays by about that much.

| Test | PC | Source / condition | Flows | Stored → synced | Pending peak → final | Dropped | Duration | Avg inference / live lag | CPU / RAM peak | Notes |
|---|---|---|---:|---:|---:|---:|---:|---|---|---|
| A Manual | A | Online | 1 | 1 | 1 → 0 | 0 | — | 1,911.8 ms (first inference) | — | event `575a0d55…`; Attack 80.82%; upload 1.22 s; started 52 ms apart from PC B |
| A Manual | B | Online | 1 | 1 | 1 → 0 | 0 | — | 58.8 ms | — | event `6657bfbb…`; Attack 80.82%; upload 1.81 s |
| B PCAP | A | Fast, every flow (capture 3) | 11 | 11 | 11 → 0 | 0 of 82 packets | 8 s | 278.3 ms | 230 MB after run | 10 attacks + 1 normal; upload p95 1.75 s |
| B PCAP | B | Fast, every flow (capture 2) | 11 | 11 | 11 → 0 | 0 of 82 packets | 5 s | 58.8 ms | not recorded | same 11 flows; upload p95 3.51 s; ran 18 s before PC A, not overlapping |
| C CSV cap | A | Concurrent, Fast, every flow (capture 5) | 5,000 | 300 (cap) | → 0 | 4,700 not stored, by design | 45 min 36 s | 158.6 ms | 24.5% / 294 MB | 211 alerts; upload p95 1.63 s; ran together with PC B for 35 min |
| C CSV cap | B | Concurrent, Fast, every flow (capture 4) | 5,000 | 300 (cap) | → 0 | 4,700 not stored, by design | 35 min 1 s | 63.7 ms | not recorded | 210 alerts; upload p95 2.08 s |
| D Live | A | Wi-Fi, Npcap (capture 10, 2026-10-15, Administrator) | 27 | 18 | → 0 | 0 of 848 packets | 45 s | lag not recorded | — | 329 own cloud packets excluded; upload p95 1.33 s |
| D Exclusion | A | `check_capture_exclusion.py`, 2026-10-16 | — | — | — | 0 of 2,810 packets | 41 s | — | — | `passed: true`; owned flows classified 0; unrelated flows classified 10; 181 cloud packets excluded |
| D Live | B | Default interface, save attacks (capture 6, 2026-10-10) | 2,866 | 300 (cap) | → 0 | 0 of 119,100 packets | 30 min 33 s | lag not recorded | — | 7,848 own cloud packets excluded; 1,950 flows (68%) classified Attack; upload p95 **24.06 s**; ended at token expiry (finding 1) |
| D Live | B | Wi-Fi, every flow (capture 19, 2026-10-16) | — | 300 (cap) | → 0 | — | — | **13.1 s avg lag** | — | upload p95 3.44 s |
| D Exclusion | B | `check_capture_exclusion.py`, 2026-10-16 | — | — | — | 0 of 740 packets | — | — | — | `passed: true`; owned flows classified 0; unrelated flows classified 10; 187 cloud packets excluded |
| E Offline / restart | B | CSV Fast, every flow, disconnected (capture 12) | 210 | 210 | 210 → 0 | 0 | 1 min 38 s classifying, all but the first ~2 s offline | — | — | see below |
| F Revocation | B | Valid login, membership revoked then restored | — | — | — | — | — | — | 6.3% / 108 MB (capture 18) | see below |

**Test E (2026-10-16).** A first attempt (capture 11) did not disconnect: Airplane
mode left PC B online, and all 300 records synced normally. It is not counted
(finding 14). In the repeat, PC B started capture 12 online at 09:29:04Z and went
offline about 2 s later, before any upload. It classified 210 flows offline, all
held as pending. The capture was stopped offline. `Ctrl+C` exited in under 10 s
with the cloud unreachable. Restarting offline refused sign-in, as designed.
After reconnecting, `bryntpcb` signed in at 09:32:39Z. All 210 records were
acknowledged between 09:32:44.6Z and 09:32:48.7Z, the last 9.5 s after sign-in.
The cloud holds 210 events with 210 distinct event UUIDs, 210 traffic rows,
210 predictions and 139 alerts, all under PC B's node and owner. No PC A record
changed. The capture's stop summary and its start audit entry also arrived after
reconnect, and capture 12 closed as `stopped` with 210 flows.

**Test F (2026-10-16).** The Administrator revoked `bryntpcb` on PC B's node at
09:40:58Z and restored it at 09:45:21Z, then revoked and restored once more at
09:50:38Z and 09:50:42Z. While revoked, PC B was refused, and the cloud accepted
no capture and no event from it. The exact wording on PC B was not recorded.
After Restore, PC B still refused to monitor until **Retry uploads** was pressed
on its Dashboard. This is by design, but the runbook did not say so (finding 5).
The five Starts attempted in that window each opened an empty capture in the
cloud (captures 13–17, 0 flows). After Retry uploads, capture 18 ran normally:
332 flows, 234 stored (all attacks). PC A kept working throughout.

**Administrator reads across nodes.** On PC A, the Administrator's Alert History
showed only PC A's alerts, because ordinary pages default to the installation's
own node until 6B.1 (finding 6). Access was therefore checked on the hosted
database instead. The check ran as each account under the `authenticated` role
with that account's JWT subject, read-only, on 2026-10-16:

| Acting as | Alerts visible |
|---|---|
| `cel.hendrix` (Administrator) | both nodes: 467 on PC A and 1,527 on PC B |
| `celpca` (PC A analyst) | PC A only (467); PC B's node row is not visible |
| `bryntpcb` (PC B analyst) | PC B only |

**Incidental cap evidence.** On 2026-10-08, PC B ran an unplanned CSV replay
(capture 1) while checking the model page. It classified 571 flows and stored
exactly 300, all attacks, all with alerts.

**Cross-PC verdicts.** The 300 stored rows from Test C are the same 224 distinct
inputs on both PCs. Identical inputs always got identical results on the same
PC. Across the two PCs, 2 of 224 differed: one label flipped at the boundary
(PC B Normal 54.63%, PC A Attack 50.65%), and one kept its label with a different
confidence. Both PCs used the same hash-checked model file and locked model
packages, so the cause is most likely numeric differences between CPUs or maths
libraries (finding 11).

## 4. Budgets: actual against target

| Budget | Target | PC A | PC B | Verdict |
|---|---|---|---|---|
| p95 upload acknowledgement | ≤ 5 s | 1.33–2.09 s across 5 runs; manual 1.22 s | 2.08–4.46 s across 6 runs; **24.06 s** on live capture 6 (2026-10-10) | **Not met on one run.** Met on the other 11, including the 2026-10-16 live retest (3.44 s). Limit L2 |
| Sustained acknowledged events/s per client | ≥ 20 | ≈ 1.9/s (capture 5: 300 events in 157 s) | backlog drain of 210 events in 4.1 s ≈ 51/s | **Not demonstrated.** Sources are paced by design: CSV Fast emits one flow per 0.35 s, ≤ 2.9/s. Each capture stores at most 300 events. Limit L1 |
| Average live lag | ≤ 20 s | not recorded | 13.1 s | Met on PC B; not recorded on PC A |
| Live packet drop | ≤ 1% | 0% (0 of 848; 0 of 2,810) | 0% (0 of 119,100; 0 of 740) | Met |
| Process CPU peak | ≤ 50% | 24.5% (2-s samples, Test C) | 6.3% (2-s samples, capture 18) | Met |
| Process RAM peak | ≤ 1 GB | 294 MB | 108 MB | Met |
| Local state growth | ≤ 50 MB | 0.051 → 8.846 MiB (8.162 MiB is the model) | 9.12 MiB in total | Met |
| Outage recovery | ≤ 2 min, 0 lost, 0 duplicates | — | 9.5 s after sign-in; 0 lost; 0 duplicates | Met. Timed from sign-in, because uploads after a restart wait for an online sign-in by design |
| Revocation / restore | refused next action; works after restore | — | refused immediately; works after Retry uploads | Met, with the Retry uploads step now in the runbook |

PC A's resource samples cover 07:58–08:43Z on 2026-10-08, the first 32 minutes of
its 46-minute CSV run. PC B's cover 60 s of capture 18. Both are the sum of the
`python` processes' working sets and CPU time. Informal Task Manager readings
taken during Test E were not recorded clearly and are not used.

## 5. Findings

| # | Finding | Severity | Goes to |
|---|---|---|---|
| 1 | **Live capture ends at access-token expiry, about 1 hour after sign-in.** The capture loop checks expiry but never refreshes (`cloud_monitor._check_authorization`). Refresh happens only when the upload worker or a page request calls `CloudAuth.repository()` in the last 60 s (`REFRESH_MARGIN_SECONDS`). Once a capture reaches its 300-event cap there is nothing to upload, so nothing refreshes. Capture 6 ended exactly 1 hour after sign-in, and its stop summary waited for the next sign-in. Pending records are kept, so this is bounded, but a long live capture cannot run. | Medium | 6C.1 |
| 2 | **A capture interrupted by an app or PC shutdown stays `running` in the cloud.** No close summary is ever queued, and a later sign-in does not reconcile it (capture 7, opened 2026-10-10, still `running`). | Low | 6C.2 |
| 3 | **Upload-delay burst on live capture 6:** p95 24.06 s, max 36.65 s, with 25 events taking 20–30 s+ around 01:12:25–01:12:44Z. Not reproduced on 2026-10-16 (capture 19 p95 3.44 s, newer code `940e082`). Cause not identified. | Medium | 6C.4 |
| 4 | **Sustained ≥ 20 events/s cannot be shown with the shipped sources.** CSV replay is paced (`SPEED_CHOICES`), live capture runs at wire pace, and storage is capped at 300 per capture. A backlog drained at ≈ 51 events/s for 4 s. A sustained-load harness is needed. | — | 6C.4 |
| 5 | **A permission block persists on the client after Restore** until the user presses Retry uploads or signs in again (`CloudSync.register`, `/sync/retry`). This is intended, but the runbook said only "refresh". The Live Monitor refusal does not mention Retry uploads, and each Start during the block opens an empty capture in the cloud (captures 13–17). | Low | runbook corrected; message and empty captures to 6C.1 |
| 6 | **Ordinary pages show only the installation's own node, even for the Administrator.** `list_records` applies a node filter unless `all_visible_nodes` is set, and Alert History does not set it. RLS lets the Administrator read every node (§3), so this is a view gap, not an access gap. | — | 6B.1 |
| 7 | **Clock skew blocked sign-in.** PC A's Windows Time service was unsynchronised ("Local CMOS Clock") and about 7 s behind, then 2 s behind on 2026-10-10. Supabase accepted the password, but token verification used zero leeway, and the generic message hid the cause. Fixed in `940e082` (10 s leeway for `iat`/`nbf`; expiry stays exact). PC A's clock was unsynchronised again on 2026-10-16 (0.53 s off). | Fixed | runbook: sync clocks |
| 8 | **The hosted pilot had never been provisioned.** The runbook assumed migrations, an Administrator, the Edge Function and an active model already existed. | — | runbook §0 added |
| 9 | Models trained before Stage 5C cannot be published (no runtime stamp), so the pilot model had to be retrained. | — | documented |
| 10 | Non-model dependencies use `>=`, so a fresh install drifts from an existing one. PC A was upgraded to match PC B. | Low | 6D.1 / 6D.3 |
| 11 | 2 of 224 identical inputs got different verdicts across the two PCs (§3). | Low | 6A.2 |
| 12 | On real Wi-Fi traffic, 68% of PC B's 2,866 flows were classified Attack, mostly TCP/UDP 443 and DNS. This is not a 5F gate, but it limits any live-accuracy claim. | High for research | 6A.1 / 6A.2 |
| 13 | PC B's `.env` kept the runbook's example path `state-PC-A`. It is harmless because the folder is on PC B's own disk and the node IDs differ, but it is confusing. | — | runbook clarified |
| 14 | Windows Airplane mode did not disconnect PC B, so Test E's first attempt was void. | — | runbook: verify offline first |
| 15 | The pilot database password appeared in a session transcript on 2026-10-08. | — | **maintainer: rotate the password** |
| 16 | Alert 926 was marked Resolved twice, one second apart (double submit). | Cosmetic | 6B |

## 6. Shared storage and capacity

Measured on 2026-10-16 at 10:07Z against the Supabase Free plan's published
limits (500 MB database per project, 1 GB file storage; pricing page checked
2026-10-16):

- The `public` schema grew from 1.06 MB before Test A to 10.47 MB with 2,606
  stored events. That is about 3.6 KB per stored event, counting its traffic,
  prediction, alert and audit rows and their indexes.
- The whole database is 23.1 MB, 4.6% of 500 MB. At the measured rate, the
  remaining space holds about 130,000 more stored events, roughly 440 captures
  that reach the 300-event cap.
- File storage holds one 8.56 MB model object, under 1% of 1 GB. Each new model
  publication adds about the same.
- Local state stayed under 10 MiB per PC, most of it the cached model. Synced
  outbox payloads are cleared on acknowledgement (Stage 5D).
- Hosted backup growth was not measured. Free projects pause after one week without activity, so check that
  the pilot is active before a demonstration or test session.

## 7. What Stage 5F accepts, and its limits

**Accepted on the physical pilot:**
- Two Windows installations keep distinct node UUIDs across restarts. Each needs
  an approved membership, and each defaults to its own node's ordinary views.
- Each installation downloaded and verified the active model into an empty cache.
  Each ran manual prediction, PCAP, CSV and live capture without local training.
- No row was written under the other client's node or owner. Analysts can read
  only their own node, and the Administrator can read both.
- The 300-event cap held on real clients.
- Pending records survived an offline stop and restart, and synced once, with
  none lost or duplicated.
- Revocation blocked the next action immediately, and access returned after
  restore.
- AlgoGuard's own cloud traffic was excluded on both PCs, while unrelated HTTPS
  to the same provider stayed visible.
- Packet drop, CPU, RAM and local-storage budgets were met.

**Documented limits (not accepted):**
- **L1** — Sustained acknowledged throughput of ≥ 20 events/s per client was not
  demonstrated; see finding 4.
- **L2** — p95 upload acknowledgement exceeded 5 s on one live run; see finding 3.
- **L3** — Live capture stops about 1 hour after sign-in once the cap is reached;
  see finding 1.
- **L4** — An interrupted capture can remain `running` in the cloud; see
  finding 2.
- **L5** — Not exercised: shutdown against a *slow* (as opposed to unreachable)
  endpoint, live detection lag on PC A, local enqueue time, and a live capture
  on PC A under an analyst account (the Administrator account was used).
- **L6** — Detection quality on real traffic is unvalidated (finding 12). Stage
  6A owns this; no live-accuracy claim follows from Stage 5F.

## 8. Local rehearsal and regression evidence (2026-09-30)

These checks ran on one machine against the local Supabase stack before the
physical pilot. They are kept unchanged. The open items they listed are answered
in §3–§7 above.

### 8.1 Local two-client rehearsal

`tests/integration/test_two_node_acceptance.py` used the real local Supabase
Auth, Data API, RLS, and Storage stack. Two distinct users were assigned to two
approved node UUIDs. Each `ModelCache` started empty, fetched the published
model with that user's credentials, checked it against the signed/protected
manifest and bytes, and kept the file in a separate node directory. The test
then submitted three batches of ten events per node simultaneously through the
normal authenticated repository. It checked every receipt, node/owner mapping,
administrator reads across both nodes, and access revocation while the second
test token remained valid. Revoked access blocked both the current-node check
and another write; cleanup restored the membership.

Local node identity tests confirm that separate state directories create
distinct UUIDs and repeated startup lookup preserves each UUID. Existing
`tests/integration/test_relational_access.py` covers direct cross-node reads,
writes, forged relationship links, membership/role manipulation, and global
administrator reads. Stage 5D's Windows evidence covers CSV, PCAP, live capture,
and manual prediction on one installation; the two-client smoke uses real model
downloads and API flow writes but does not repeat all capture sources on each
of two physical clients.

The test machine was Windows 11 Home Single Language, build 26200, with a
13th-generation Intel Core i5-13420H (8 cores, 12 logical processors) and
15.7 GiB RAM. Both clients and the Supabase stack ran on that same machine. In
one short run, 60 accepted events across two clients completed in 0.254 s
(236.68 acknowledged events/s). The six batch request round trips had a
median of 75.30 ms and a nearest-rank p95 (the worst of these six samples) of
107.45 ms. This measures a small local API
smoke, not inference latency, packet throughput, Internet latency, or a pilot
service limit. No network RTT, hardware comparison, or acceptance budgets have
been established. The test deliberately sets no performance threshold.

Reproduce it with an unpaused local stack:

```powershell
python -m pytest -q -s -m integration tests/integration/test_two_node_acceptance.py --tb=short
python -m pytest -q tests/test_node_enrollment.py --tb=short
```

Evidence: `check-output/5f-two-node-local.txt` and the node identity unit test
(3 passed). The local performance number is sensitive to machine load and local
stack state, so a later run should record its own output alongside context.

### 8.2 Additional local regression evidence (2026-09-30)

After restarting the previously stopped local Edge Runtime, the full offline
Python lane passed (508 passed, 67 integration tests deselected), and the full
local integration lane passed (67 passed) with `RUN_WINDOWS_CAPTURE_TESTS=1` on
this Npcap-equipped machine. The recovery/Edge subset also passed independently
(12 passed). Ruff and Pyright reported no findings, and the JavaScript suite
passed 21 tests. Logs are `check-output/5f-focused-integration.txt`,
`check-output/5f-integration-full.txt`, and
`check-output/5f-integration-windows.txt`.

Local automated coverage additionally confirms the 300-event reservation cap
while writes are pending, durable pending-event replay only for the original
user after restart, lifecycle-summary preservation when flow storage fills,
terminal stop behavior when local persistence stalls, and capture close/count
reconciliation through the authenticated local cloud stack. Synthetic packet
checks keep unrelated HTTPS to the same provider address visible for
classification. Real Npcap loopback capture reached cloud persistence; the
maintenance-process test excluded AlgoGuard's owned database connections while
keeping a separate connection to the same local database endpoint visible.
Two-computer behavior, network loss/latency, end-to-end performance budgets,
and pilot hardware resource growth remain unmeasured; these local checks do not
substitute for your two-computer pilot.

## 9. Evidence

- Hosted pilot rows, by capture ID: 1–7 (2026-10-08/10), 8–10 (2026-10-15),
  11–19 (2026-10-16). Captures 7 and 13–17 are explained in findings 2 and 5.
- Working notes and raw samples on PC A, git-ignored and not committed:
  `check-output/5f-two-pc-pilot.md`, `check-output/5f-pc-a-resources-part1.csv`
  and `check-output/5f-pc-a-resources-part2.csv`.
- Local rehearsal logs: `check-output/5f-two-node-local.txt`,
  `check-output/5f-focused-integration.txt`, `check-output/5f-integration-full.txt`
  and `check-output/5f-integration-windows.txt`.
