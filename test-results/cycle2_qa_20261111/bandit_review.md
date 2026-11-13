# Bandit review — test cycle 2 (C2-SEC-01)

Run: `test-results/suites_20261111_005819/bandit.log`, commit `cbe0cca`, 13,256 lines of application code.
By severity: **High 0, Medium 15, Low 31** (cycle 1, 2026-10-24: High 0, Medium 14, Low 18). The run's
summary line printed the confidence counts (`High: 30`) as if they were severities; fixed in `f768dd0`.

Rule kept from cycle 1: every Medium or High finding is read in the source and justified, or raised as a
defect.

| # | Finding | Location | Decision |
|---|---|---|---|
| 1–14 | B104 (outgoing sockets bound to an ephemeral port; a host list) and B608 (SQL built from fixed table names or `?` placeholders) | `cloud_connections.py`, `maintenance_connections.py`, `maintainer_env.py`, `cloud_outbox.py`, `legacy_backup.py`, `legacy_import.py`, `services/database_service.py` | The same 14 findings justified in cycle 1 (`test-results/sec01_20261019_172340/bandit_review.md`); the code is unchanged apart from line numbers. |
| 15 | B301 `pickle.loads` | `services/process_pipe.py:30` (new in Iteration 6, DEF-17) | Justified. The pickles travel only over the anonymous stdin/stdout pipes of a child process AlgoGuard starts itself (live inference and capture). No other process can write to them, and the parent sends the model it has already verified against the protected manifest. |

The 13 additional Low findings: 4 more `subprocess` imports (B404) and 4 more subprocess calls with
fixed argument lists and no shell (B603), 2 more programs started by name (B607: `git` for the build number in `app_version.py` and
`scripts/build_analyst_package.py`), 2 more
`try/except/pass` around best-effort cleanup (B110) and the `pickle` import (B403) that goes with
finding 15. Lows are not required to be justified.

pip-audit on the same run: one advisory, Werkzeug 3.1.8 PYSEC-2026-4312 (CVE-2026-102598), raised as
DEF-23 and fixed in `23fc068` (Werkzeug 3.1.9); pip-audit after the fix: "No known vulnerabilities found".
