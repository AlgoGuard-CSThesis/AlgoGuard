# Iteration 5 — Summary

Status: **closed on 2026-10-16 with documented limits.**

**Objective** (from the plan): deliver protected shared data, verified model
delivery, recoverable migration, and bounded interruption behavior.

Iteration 5 moved AlgoGuard from one local SQLite installation to a cloud pilot.
Installations now sign in through Supabase Auth and write through Row Level
Security. Each installation is an approved node with its own identity. A bounded
local outbox keeps records safe while the cloud is unreachable. Models arrive as
verified, immutable publications. Six stages delivered this in order, and each
has its own record.

## Stages

| Stage | Delivered | Accepted | Record | Stage commit |
|---|---|---|---|---|
| 5A — Configuration and test infrastructure | Typed configuration; separate analyst and maintainer credentials; local Supabase test stack; baseline tests | 2026-09-24 | [05a-foundations.md](05a-foundations.md) | `656c268`, `d1552d2` |
| 5B — Identity, schema, and authorization | Versioned migrations; Auth and protected roles; approved enrollment; RLS on every table; transactional API functions | 2026-09-26 | [05b-access.md](05b-access.md) | `57b37f5` |
| 5C — Model publication and delivery | Immutable artifacts; serialized activation; manifests with runtime lock; verified downloads and atomic cache promotion | 2026-09-27 | [05c-models.md](05c-models.md) | `a8048bf` |
| 5D — Application integration and persistence | Cloud login; API-backed pages; bounded outbox and upload worker; capture lifecycle; narrow cloud-traffic exclusion | 2026-09-27 | [05d-integration.md](05d-integration.md) | `52a3b5b` |
| 5E — Existing data and recovery | Legacy backup and repeatable import; cutover and recovery rehearsal | 2026-09-30 | [05e-cutover.md](05e-cutover.md) | `5f87793` |
| 5F — Two-node pilot verification | Local two-client rehearsal (2026-09-30); physical two-computer pilot on the hosted project (2026-10-08 to 2026-10-16) | 2026-10-16, with limits | [05f-pilot.md](05f-pilot.md) | `f060755` (local rehearsal) and the commit that adds this summary |

The two-computer procedure is in
[05f-two-computer-runbook.md](05f-two-computer-runbook.md).

## Exit criteria

| Criterion (plan, Iteration 5) | Verdict | Evidence |
|---|---|---|
| Two enrolled installations classify and submit under their own users. | Met | 5F §3: both PCs ran all four sources on the hosted pilot, and no row was written under the other client's node or owner. |
| Direct hostile-client tests cannot cross assignments. | Met | 5B permission matrix and probes; 5F local probes (`test_relational_access.py`); hosted RLS check under each pilot account (5F §3). |
| Existing data is accounted for. | Met | 5E: snapshot counts, the import ledger, and recovery rehearsal with colliding IDs. |
| Models load from verified manifests. | Met | 5C; in the pilot, both PCs downloaded the 8,558,646-byte object into empty caches and matched its hash. |
| Interruption has bounded consequences. | Met, with limits L3–L4 | 5F Test E: 210 offline records survived a stop and restart and synced once, 9.5 s after sign-in; shutdown with the cloud unreachable took under 10 s. A live capture stops at token expiry, and an interrupted capture can remain `running` in the cloud. |
| Analyst installations have no privileged cloud credentials. | Met by design | 5A.2: analyst settings hold only the project URL and publishable key, and privileged tools load `.env.maintainer`. The pilot's analyst `.env` files were not audited line by line. |
| Integration tests exercise the real Auth/API/RLS path. | Met | 67 integration tests passed against the local Supabase stack with Windows Npcap checks enabled (5F §8.2). |

## Deviations from the plan

- The pilot region is Tokyo, not Singapore. This was accepted in 5A.
- The hosted pilot was provisioned only on 2026-10-08. All 5B–5E evidence came
  from the local stack. The runbook now has a provisioning section.
- The physical pilot spanned two code versions: `4c9666c` for Tests A–D and
  `940e082` afterwards. The only change between them was sign-in clock-skew
  tolerance.
- Two performance budgets were not met as agreed. They are recorded as limits
  and were not re-targeted after the results (5F §4).

## Limits and findings carried into Iteration 6

| Item | 5F reference | Iteration 6 stage |
|---|---|---|
| Sustained ≥ 20 events/s per client not demonstrated; needs a load harness | L1, finding 4 | 6C.4 |
| Upload-delay burst (p95 24 s) on one live run; cause not found | L2, finding 3 | 6C.4 |
| Live capture stops at access-token expiry, about 1 hour after sign-in | L3, finding 1 | 6C.1 |
| Interrupted capture stays `running` in the cloud | L4, finding 2 | 6C.2 |
| Revocation refusal does not point to Retry uploads; Starts while blocked open empty captures | finding 5 | 6C.1 |
| Ordinary pages show only the local node, even for the Administrator | finding 6 | 6B.1 |
| 68% of real Wi-Fi flows classified Attack; 2 of 224 verdicts differ across PCs | L6, findings 11–12 | 6A.1–6A.2 |
| Non-model dependencies not pinned exactly | finding 10 | 6D.1 / 6D.3 |
| Alert review can be submitted twice | finding 16 | 6B |

The earlier carry-forwards in [05a §7](05a-foundations.md) and
[05d §9](05d-integration.md) still stand.

## Maintainer actions still open

- Rotate the pilot database password. It appeared in a session transcript on
  2026-10-08 (finding 15).
- The pilot is on the Supabase Free plan, which pauses projects after a week
  without activity. Check that it is active before a demonstration or test
  session.
- Leave the pilot records in place. Capture 7 stays `running` as evidence of
  limit L4 until 6C.2 adds reconciliation.

## Next

Iteration 6 starts with 6A, research validation and attack scores. That stage
also owns limit L6: no live-accuracy claim follows from Iteration 5.
