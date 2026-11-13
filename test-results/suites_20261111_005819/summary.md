# Automated test run — 2026-11-11T00:58:20

- Commit: `cbe0cca` (with uncommitted changes)
- Machine: SanMiguel · Windows-11-10.0.26300-SP0 · Python 3.14.6
- RUN_WINDOWS_CAPTURE_TESTS=0

| Step | Result | Exit code | Duration | Key line |
|---|---|---|---|---|
| pytest-offline | PASS | 0 | 459.5 s | 682 passed, 103 deselected, 261 warnings in 452.58s (0:07:32) |
| ruff-lint | PASS | 0 | 0.4 s | All checks passed! |
| pyright-typecheck | PASS | 0 | 14.4 s | 0 errors, 0 warnings, 0 informations |
| node-monitor-poll | PASS | 0 | 0.1 s | # pass 11 / # fail 0 |
| pytest-integration | FAIL | 1 | 154.4 s | 1 failed, 97 passed, 5 skipped, 682 deselected, 8 warnings in 151.94s (0:02:31) |
| pip-audit | FAIL | 1 | 49.7 s | Found 1 known vulnerability in 1 package |
| bandit | FAIL | 1 | 1.7 s | Low: 0 / Medium: 16 / High: 30 |

Full output for each step is in the matching .log file in this folder.
NOT RUN means the tool is missing on this machine; install it or record the step as not executed.
