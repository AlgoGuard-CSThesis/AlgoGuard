# Automated test run — 2026-10-20T04:57:24

- Commit: `503cb9b` (with uncommitted changes)
- Machine: SanMiguel · Windows-11-10.0.26300-SP0 · Python 3.14.6
- RUN_WINDOWS_CAPTURE_TESTS=0

| Step | Result | Exit code | Duration | Key line |
|---|---|---|---|---|
| pytest-offline | PASS | 0 | 142.0 s | 537 passed, 67 deselected in 139.19s (0:02:19) |
| ruff-lint | PASS | 0 | 2.0 s | All checks passed! |
| pyright-typecheck | PASS | 0 | 12.2 s | 0 errors, 0 warnings, 0 informations |
| node-monitor-poll | PASS | 0 | 0.2 s | # pass 11 / # fail 0 |
| pytest-integration | PASS | 0 | 52.0 s | 65 passed, 2 skipped, 537 deselected in 49.47s |
| pip-audit | PASS | 0 | 50.7 s | No known vulnerabilities found |
| bandit | FAIL | 1 | 1.3 s | Low: 0 / Medium: 16 / High: 16 |

Full output for each step is in the matching .log file in this folder.
NOT RUN means the tool is missing on this machine; install it or record the step as not executed.
