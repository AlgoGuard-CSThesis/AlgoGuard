# Iteration 6 — Summary

Status: **closed on 2026-11-04 as a limited research pilot release.** Every stage met its
acceptance on the local Supabase stack; the research release gate is unmet, so the release is
limited to the documented pilot scope (plan, "Research and score-display contract").

**Objective** (from the plan): make scores interpretable, provide administrator visibility and
retention, and verify a reproducible analyst installation.

Iteration 6 turned the Iteration 5 cloud pilot into something an analyst can install and read.
Each verdict now carries an attack score whose limits are measured and stated. Administrators
see every computer's records and health and can clean up old data under audit. Sign-in, upload,
crash, outage, model and backup failures were caused on purpose and handled. A packaged analyst
installation was set up from scratch, approved and used without training or privileged keys.

## Stages

| Stage | Delivered | Accepted | Record | Commits |
|---|---|---|---|---|
| 6A — Research validation and attack scores | Feature fidelity against Argus, untouched evaluation, calibration assessment, attack probability and versioned score bands; DEF-17 and DEF-19 fixes | 2026-10-25, findings fixed 2026-10-26 | [06a-evidence.md](06a-evidence.md) | `e209d60`, `2718ce1`; `5b5a36a`, `cffb952`, `5bd4e07`, `6cb88e6`, `5567c4e`, `9cda196` |
| 6B — Administrator visibility and retention | Cross-node alerts, logs and reports; node health and the Computers page; audited retention and the Retention page | 2026-10-27 | [06b-operations.md](06b-operations.md) | `1bf8dd0`, `2a8e75e`, `d73a95b` |
| 6C — Recovery and operational hardening | Sign-in lifecycle; durable writes and crash recovery; DEF-10 parallel uploads; model and backup recovery; sustained load and capture exclusion | 2026-11-02 | [06c-recovery.md](06c-recovery.md) | `81c1618`, `d69cf63`, `8998e35`, `03b66d6` |
| 6D — Packaging, documentation, and final QA | Cloud mode by default, setup and start scripts, the analyst package; analyst and maintainer guides; clean-folder installation; final QA and browser walk | 2026-11-04 | [06d-release.md](06d-release.md) | `5cd2e07`, `2d2164e`, `03ed96f`, `d82022f`, `d0f89d4`, `d03ceb2`, `24b6eab` and the record commit |

## Acceptance

| Stage acceptance (plan, Iteration 6) | Verdict | Evidence |
|---|---|---|
| 6A: Claims match evidence; supported features pass measured tolerances; probability is never labelled impact. | Met, with the research gate unmet | 15 of 16 live features within predeclared tolerances on a fresh capture (`sload` only with the link-layer correction); untouched evaluation and calibration reported as measured; bands Low to Very high, never "Critical", with stated limits on every page and in both guides. |
| 6B: Analysts remain scoped; stale/sync/storage states are visible; cleanup preserves retained evidence. | Met | Scope enforced by row security for hand-edited URLs and direct API calls; Computers marks stale after 3 minutes; retention keeps cited evidence and was rehearsed with restore. |
| 6C: Revocation, lost responses, restart, disk failures, and traffic exclusion meet the contract. | Met on the local stack | Every failure in 06c ended with records stored once or kept locally with an honest status; 15-minute run met every 5F budget; own connections excluded across reconnects and a DNS change. |
| 6D: A new analyst configures, enrolls, logs in, and monitors without training or privileged keys. | Met on the local stack | Clean-folder install from the package: setup, approval, model download, all three sources, restart, outage, hostile edits and requests, and a scan of every installed file (06d §6D.3). Not yet done by a new person or against the hosted pilot. |

## Research release gate

**Unmet** (06a): on the untouched UNSW-NB15 set the model reaches accuracy 86.2%, F1 85.4% and
recall 76.0% against 90% targets; scores are calibrated on the hold-out but not on the untouched
set; detection of attacks on live traffic is untested; the benign live evidence comes from one
computer. AlgoGuard is therefore released as a limited research pilot. The README's Scope and
"Accuracy, Scores and Their Limits" and the analyst guide's section 7 say so to every reader.

## Pilot limits and findings from Iteration 5

| Item (05-iteration-summary) | Outcome |
|---|---|
| L1 sustained ≥ 20 events/s not demonstrated | 26.5 events/s for 15 minutes with an outage (6C.4) |
| L2 upload-delay burst (DEF-10) | Four uploads at once: burst p95 3.6 s against 5.3 s (6C.2); re-test on the pilot in cycle 2 |
| L3 capture stops at token expiry | Sign-in renewed in the background and after sleep (6C.1) |
| L4 interrupted capture stays `running` | Late capture summaries close it after restart (6C.2) |
| Revocation refusal and Retry uploads (finding 5) | Retry uploads shown when blocked (6C.1); since 6D.3 approval and restoration also lift the block by themselves |
| Pages show only the local node (finding 6) | Cross-node views (6B.1) |
| 68% of Wi-Fi flows called Attack (L6, DEF-19) | 3–7% on the trained computer with run 10 (6A); re-test in cycle 2 |
| Dependencies not pinned exactly (finding 10) | Pinned; drift reproduced and closed (6D.1, 6D.3) |
| Alert review submitted twice (finding 16) | Missed in 6B; fixed in 6D.4: a submitted form's buttons are disabled, so a second click cannot repeat the action |

## Deviations from the plan

- There was no second computer for 6D.3 or test cycle 2's preparation; the clean installation ran
  in an empty folder on the maintainer's computer, as decided at the close of test cycle 1.
- All Iteration 6 evidence comes from the local Supabase stack. The hosted pilot received the
  6A and 6B migrations and model 60 on 2026-11-04 and is used again in test cycle 2.
- Scheduled retention stays off; administrators run cleanups by hand (6B).

## Maintainer actions still open

- Rotate the pilot database password (Iteration 5 finding 15).
- Check that the pilot project is active before a session (Supabase Free plan pauses idle projects).
- PC B: `git pull` before its next session.

## Next

Test cycle 2 on the hosted pilot: re-test DEF-10 and DEF-19, follow the analyst guide with a
person who has not used AlgoGuard, and record the results in the cycle 2 test records.
