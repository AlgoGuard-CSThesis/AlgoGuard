# SEC-01 — Bandit findings review (scan of 2026-10-19 13:28, build dafe5e5, product code 940e082)

Source: `test-results/suites_20261019_132412/bandit.log` (`python -m bandit -r <top-level *.py> services scripts`).
By severity: **High 0, Medium 14, Low 18.** (The runner's summary line printed the by-confidence counts, Medium 16 / High 16, by mistake.)
Rule (Master Prompt): justify each Medium/High or raise a defect. Each Medium below was read in the source.

| # | Test | Location | What the code does | Verdict |
|---|---|---|---|---|
| 1 | B104 bind all interfaces | cloud_connections.py:177 | Binds an **outgoing** client socket to `0.0.0.0:0` (ephemeral port) before `connect()`, to learn its local port for own-traffic exclusion. Nothing listens. | Justified — false positive |
| 2 | B104 | maintenance_connections.py:141 | Same outgoing-socket pattern for the maintenance connection lease. | Justified — false positive |
| 3 | B104 | maintainer_env.py:44 | `"0.0.0.0"` is only a member of the set of local hosts where the local stack's Postgres has no TLS. No bind. | Justified — false positive |
| 4 | B608 SQL string | cloud_outbox.py:430 | Builds only `?` placeholders (`",".join("?" ...)`); every value is a parameter. | Justified |
| 5 | B608 | legacy_backup.py:76 | Table name from the fixed `TABLE_KEYS` constant; no user input. | Justified |
| 6 | B608 | legacy_backup.py:110 | Table/key from a fixed tuple and `TABLE_KEYS`. | Justified |
| 7 | B608 | legacy_backup.py:162 | Same as 6. | Justified |
| 8 | B608 | legacy_backup.py:202 | Table from a manifest, but checked against the allow-list ("detection_model", "model_deployment") on line 199 first; values are `?` parameters. | Justified |
| 9 | B608 | legacy_import.py:67 | Table from the fixed `ORDER` list and key from `TABLE_KEYS`. | Justified |
| 10 | B608 | legacy_import.py:131 | Table from a hard-coded tuple; `node_id` passed as `%s`. | Justified |
| 11 | B608 | legacy_import.py:200 | Same as 10. | Justified |
| 12 | B608 | services/database_service.py:585 | Column names filtered through the fixed `allowed` set (line 575); values are `?` parameters. | Justified |
| 13 | B608 | services/database_service.py:1201 | WHERE built from fixed condition strings; all filter values (dates, module, status, model, run_id, search text) are `?` parameters. | Justified |
| 14 | B608 | services/database_service.py:1205 | Same query as 13 plus `LIMIT ? OFFSET ?`. | Justified |

**Low findings (18, no action required by the rule):** B110 try/except/pass ×7 (error-tolerant cleanup paths); B105 "hardcoded password" ×2 (an empty-string default in cloud_auth.py:71 and the password-policy help text in services/password_policy.py:7 — no secrets); B311 `random.Random()` in traffic_source_service.py:115 (shuffles the "Randomised" replay order, not security); B404/B603/B607 subprocess ×8 (test tooling in scripts/, and cloud_outbox.py:55).

**Hardening suggestion (not a defect):** cloud_outbox.py:55 runs `icacls` by name (B607). Windows searches the application and current directories before System32, so calling `%SystemRoot%\System32\icacls.exe` by full path would remove that dependency on PATH. Arguments are fixed and no shell is used.

**Result:** no Medium or High finding needs a fix; no defect raised.
