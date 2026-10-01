# Stage 5F — Two-node pilot verification

Status: **local two-client smoke and local regression gates passed; physical
pilot and performance budgets remain open** (2026-09-30). This record does not
accept Stage 5F or close Iteration 5.

The step-by-step procedure for the remaining physical test is in
[the two-computer pilot runbook](05f-two-computer-runbook.md).

## Local two-client rehearsal

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

## Work still required before Stage 5F exit

- Run two separate Windows installations with their own retained node IDs, users,
  memberships, model caches, restarts, and capture sources. Test CSV, PCAP, live
  capture, and manual/API classification on each installation concurrently.
- Measure the actual path to the intended pilot service: RTT/loss, event-to-
  verdict lag, local enqueue time, acknowledgement delay, pending queue age,
  end-to-end throughput, packet loss, CPU/RAM, and shared model/storage/backup
  growth. Choose hardware-specific acceptance budgets before tuning.
- Rehearse 300 flows per capture with writes still pending across restarts and
  both clients, bounded shutdown under slow/unavailable endpoints, lifecycle
  summaries, same-provider unrelated HTTPS visibility, and recovery.
- The Windows Npcap live-capture and maintenance-capture tests require
  `RUN_WINDOWS_CAPTURE_TESTS=1` and Npcap; they were explicitly enabled and
  passed on this machine. The full local integration rerun had no server
  failures.

The observed local rate is not a production target, and no defaults were tuned
from it. The local suites are now clean, but Stage 5F stays open until the
physical pilot, declared budgets, and required measurements are recorded.

## Additional local regression evidence (2026-09-30)

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
