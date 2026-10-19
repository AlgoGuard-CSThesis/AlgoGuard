# Automated test run — 2026-10-19T13:24:12

- Commit: `dafe5e5` (with uncommitted changes)
- Machine: SanMiguel · Windows-11-10.0.26300-SP0 · Python 3.14.6
- RUN_WINDOWS_CAPTURE_TESTS=0

| Step | Result | Exit code | Duration | Key line |
|---|---|---|---|---|
| pytest-offline | PASS | 0 | 132.2 s | 537 passed, 67 deselected in 130.13s (0:02:10) |
| ruff-lint | PASS | 0 | 2.1 s | All checks passed! |
| pyright-typecheck | PASS | 0 | 12.1 s | 0 errors, 0 warnings, 0 informations |
| node-monitor-poll | PASS | 0 | 0.2 s | # pass 11 / # fail 0 |
| pytest-integration | FAIL | 1 | 56.6 s | 5 failed, 60 passed, 2 skipped, 537 deselected in 54.27s |
| pip-audit | PASS | 0 | 41.8 s | No known vulnerabilities found |
| bandit | FAIL | 1 | 1.4 s | Low: 0 / Medium: 16 / High: 16 |

Full output for each step is in the matching .log file in this folder.
NOT RUN means the tool is missing on this machine; install it or record the step as not executed.
