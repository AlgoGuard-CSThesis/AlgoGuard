# Automated test run — 2026-10-23T16:01:21

- Commit: `651e99e` (with uncommitted changes)
- Machine: SanMiguel · Windows-11-10.0.26300-SP0 · Python 3.14.6
- RUN_WINDOWS_CAPTURE_TESTS=0

| Step | Result | Exit code | Duration | Key line |
|---|---|---|---|---|
| pytest-offline | PASS | 0 | 144.2 s | 576 passed, 67 deselected in 142.56s (0:02:22) |
| ruff-lint | PASS | 0 | 0.2 s | All checks passed! |
| pyright-typecheck | PASS | 0 | 9.4 s | 0 errors, 0 warnings, 0 informations |
| node-monitor-poll | PASS | 0 | 0.1 s | # pass 11 / # fail 0 |
| pytest-integration | PASS | 0 | 46.1 s | 65 passed, 2 skipped, 576 deselected in 43.99s |
| pip-audit | PASS | 0 | 36.5 s | No known vulnerabilities found |
| bandit | FAIL | 1 | 1.2 s | Low: 0 / Medium: 16 / High: 16 |

Full output for each step is in the matching .log file in this folder.
NOT RUN means the tool is missing on this machine; install it or record the step as not executed.
