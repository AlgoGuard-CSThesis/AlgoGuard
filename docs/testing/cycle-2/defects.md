# AlgoGuard test cycle 2 — Defect reports (C2-LOG-01)

Each defect is a DEF card on the [Testing Kanban](https://github.com/users/bry22191919-hash/projects/4) and a row in the Defects sheet of the Test Log. New cycle 2 defects are numbered from **DEF-20**. Their full text goes below, in the same layout as `docs/testing/cycle-1/defects.md`: found by, card owner, date, severity, priority, build, environment, status, steps to reproduce, expected and actual results, and evidence. No e-mail address, password, token or IP address of real traffic is written here.

Severity follows the Master Test Plan (cycle 2), Incident Report section: **Critical** (data crosses between nodes, an unverified model loads, credentials are exposed, or saved results are lost), **High** (a feature is unusable, known rows get the wrong verdict, or a detection target is missed), **Medium** (a workaround is needed, or a performance budget is missed), **Low** (cosmetic, wording or layout).

## Carried over from cycle 1 for re-test

Their full reports are in `docs/testing/cycle-1/defects.md` and on their cards. Cycle 2 re-tests them on the hosted pilot (C2-REG-01).

| ID | Severity | Fixed in | Re-test in cycle 2 | Closed when |
|---|---|---|---|---|
| DEF-10 Upload delay (pilot limit L2) | Medium | Stage 6C, `d69cf63`: up to four upload requests at once | TC-23 (C2-PRF-01) | Upload delay p95 ≤ 5 s for every capture, on both PCs and on both at once |
| DEF-17 Live lag p95 above 500 ms (closed in cycle 1; slower again under load in Stage 6A) | Medium | Stage 6A addendum, `5b5a36a`, `5567c4e`: capture and scoring in separate processes | TC-11 (C2-PRF-01) | p95 ≤ 500 ms for FIN/RST flows on both PCs |
| DEF-19 Benign live traffic labelled Attack | High | Stage 6A, `6cb88e6` and model 60 (training run 10), published to the pilot on 2026-11-04 as deployment #3 | TC-24 (C2-SYS-01) | ≤ 8% of benign live flows labelled Attack on both PCs |

## Cycle 2 defects

None raised yet.
