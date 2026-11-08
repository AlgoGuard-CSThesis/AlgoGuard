# Stage 6B.1 — cross-node list queries at 130,000 alerts

`perf_6b1.sql` loads, inside one transaction that is rolled back, 130,000 alerts with their traffic and predictions
(three nodes: 60% / 30% / 10%, spread over 30 days), 130,000 system-log rows and 2,000 reports into the local
Supabase stack, then times the queries the Alerts, Logs and Reports pages send, as an administrator and as an
analyst with row security on. Stage 5F measured 2,606 stored events and room for about 130,000 more on the pilot,
so 130,000 is the upper end. Run 2026-10-27:

    docker cp perf_6b1.sql supabase_db_AlgoGuard:/tmp/ && docker exec supabase_db_AlgoGuard psql -U postgres -d postgres -f /tmp/perf_6b1.sql

| Query | Before (policies call `can_read_node` per row) | After `20261027020000_node_read_once.sql` |
|---|---|---|
| Q1 alerts, this computer, first page (admin) | 22.0 ms | 12.1 ms |
| Q2 alerts, all computers, first page (admin) | 14.2 ms | 7.6 ms |
| Q3 alerts, this computer, Open, last 7 days (admin) | 29.3 ms | 43.2 ms |
| Q4 alerts, all computers, page 200 (admin) | 26,730 ms | 38.6 ms |
| Q5 logs, this computer, first page (admin) | 4.5 ms | 0.6 ms |
| Q6 logs, all computers, Failed, date range (admin) | 40.2 ms | 22.0 ms |
| Q7 reports, all computers (admin) | 6.3 ms | 0.5 ms |
| Q8 alerts, all visible (analyst of one node) | 58.1 ms | 7.0 ms |
| Q9 alerts of another node by hand-edited URL (analyst; 0 rows) | 9,597 ms | 23.9 ms |
| Q10 logs, all visible (analyst) | 15.1 ms | 0.9 ms |

The read rule is unchanged (the integration lane, 79 tests including row-security tests, passes); only its
evaluation moved from once per row to once per query.