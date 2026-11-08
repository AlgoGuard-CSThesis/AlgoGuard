# Automated test run — 2026-10-23T14:25:14

- Commit: `67032f5` (with uncommitted changes)
- Machine: SanMiguel · Windows-11-10.0.26300-SP0 · Python 3.14.6
- RUN_WINDOWS_CAPTURE_TESTS=0

| Step | Result | Exit code | Duration | Key line |
|---|---|---|---|---|
| pytest-offline | PASS | 0 | 406.7 s | 575 passed, 67 deselected in 403.99s (0:06:43) |
| ruff-lint | PASS | 0 | 2.3 s | All checks passed! |
| pyright-typecheck | PASS | 0 | 37.7 s | 0 errors, 0 warnings, 0 informations |
| node-monitor-poll | PASS | 0 | 0.2 s | # pass 11 / # fail 0 |
| pytest-integration | PASS | 0 | 98.6 s | 65 passed, 2 skipped, 575 deselected in 89.72s (0:01:29) |
| pip-audit | PASS | 0 | 58.8 s | No known vulnerabilities found |
| bandit | FAIL | 1 | 2.2 s | Low: 0 / Medium: 16 / High: 16 |

Full output for each step is in the matching .log file in this folder.
NOT RUN means the tool is missing on this machine; install it or record the step as not executed.
