# Automated test run — 2026-10-20T06:58:49

- Commit: `0480899` (with uncommitted changes)
- Machine: SanMiguel · Windows-11-10.0.26300-SP0 · Python 3.14.6
- RUN_WINDOWS_CAPTURE_TESTS=0

| Step | Result | Exit code | Duration | Key line |
|---|---|---|---|---|
| pytest-offline | PASS | 0 | 187.9 s | 550 passed, 67 deselected in 185.68s (0:03:05) |
| ruff-lint | FAIL | 1 | 0.2 s | Found 3 errors. |
| pyright-typecheck | PASS | 0 | 12.3 s | 0 errors, 0 warnings, 0 informations |
| node-monitor-poll | PASS | 0 | 0.1 s | # pass 11 / # fail 0 |
| pytest-integration | PASS | 0 | 57.4 s | 65 passed, 2 skipped, 550 deselected in 54.91s |
| pip-audit | PASS | 0 | 43.0 s | No known vulnerabilities found |
| bandit | FAIL | 1 | 1.9 s | Low: 0 / Medium: 16 / High: 16 |

Full output for each step is in the matching .log file in this folder.
NOT RUN means the tool is missing on this machine; install it or record the step as not executed.
