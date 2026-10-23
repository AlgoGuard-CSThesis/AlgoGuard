# Automated test run — 2026-10-24T00:52:03

- Commit: `c775d13` (with uncommitted changes)
- Machine: SanMiguel · Windows-11-10.0.26300-SP0 · Python 3.14.6
- RUN_WINDOWS_CAPTURE_TESTS=0

| Step | Result | Exit code | Duration | Key line |
|---|---|---|---|---|
| pytest-offline | PASS | 0 | 343.1 s | 577 passed, 67 deselected in 340.30s (0:05:40) |
| ruff-lint | PASS | 0 | 0.2 s | All checks passed! |
| pyright-typecheck | PASS | 0 | 25.8 s | 0 errors, 0 warnings, 0 informations |
| node-monitor-poll | PASS | 0 | 0.1 s | # pass 11 / # fail 0 |
| pytest-integration | PASS | 0 | 78.6 s | 65 passed, 2 skipped, 577 deselected in 76.25s (0:01:16) |
| pip-audit | PASS | 0 | 55.5 s | No known vulnerabilities found |
| bandit | FAIL | 1 | 1.2 s | Low: 0 / Medium: 16 / High: 16 |

Full output for each step is in the matching .log file in this folder.
NOT RUN means the tool is missing on this machine; install it or record the step as not executed.
