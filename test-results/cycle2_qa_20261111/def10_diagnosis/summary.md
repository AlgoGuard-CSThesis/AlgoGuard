# DEF-10 diagnosis and fix (2026-11-16, PC A)

DEF-10 in test cycle 2: the 300-record Save-every-flow burst on PC A had an upload delay p95 of
about 5.5 s after clock correction, twice (captures 58 and 60); PC B passed at 3.19 s.

## Where the time went

**Pilot timeline** (`pilot_timeline_57_58_60.txt`, read-only; `def10_timeline.py`). Events grouped by
`received_at`, one value per upload request. On PC A the second upload pass left about 2 s into the
burst but carried only the first 122 (capture 60) or 141 (capture 58) events, although 200 or
more had been scored by then. The uploader reads only events already written to the local spool,
so the spool was behind. On PC B (capture 57) the second pass carried a full 200, so its spool kept
up.

**Server ruled out** (`pilot_server_time.txt`; `def10_server_time.py`). `store_flow_batch` runs on
average 50 ms per call on the pilot (3,552 calls). Concurrent batches take only per-event advisory
locks and insert rows, so the four parallel uploads do not wait for each other.

**Spool write rate on PC A** (`def10_group_bench.py`, the spool's pragmas, 300 rows of 1.6 KB):

| Rows per transaction | Quiet PC | Busy Python thread in the process |
|---|---|---|
| 1 (as shipped) | 45.7 rows/s | 11.9 rows/s |
| 10 | 305.6 rows/s | 75.0 rows/s |
| 50 | 1,373.3 rows/s | 120.3 rows/s |

Each committed transaction waits for a disk flush (journal_mode=delete, synchronous=full), about
22 ms on PC A, and longer while the capture loop keeps the process busy. The real spool, measured
the same way at different moments, wrote 25 to 64 events/s. A burst produces 100 to 160 events/s.

## Cause

The spool wrote one flushed transaction per event. On a PC whose disk flush is slow, a
Save-every-flow burst outran it, and records waited in memory before the uploader could see them.

## Fix

`cloud_outbox.Outbox`: the writer takes every event waiting in the handoff (up to `GROUP_LIMIT`,
100) and writes them in one transaction. An event whose UUID is already stored with other content
is refused alone. If a group cannot be written (full or failing disk), its events are written one
at a time as before, so the ones that fit are kept. Tests: `tests/test_cloud_outbox.py`
(`test_a_burst_waiting_in_the_handoff_is_saved_in_a_few_writes`: 150 commits before the fix, at
most 2 after; `test_a_conflict_in_a_saved_group_refuses_only_its_own_event`;
`test_a_disk_filling_up_during_a_group_keeps_the_events_that_fit`).

## Burst replica (`def10_harness.py`)

Real Outbox and SyncWorker, a fake cloud taking 0.35 s before its transaction starts and 0.35 s
more to answer, 300 events at 100/s. Delay = transaction start minus event creation. Six runs per
variant in the same session, alternating:

| Load in the process | Before the fix (p95) | After the fix (p95) |
|---|---|---|
| none | 5.75 s, 3.76 s, 2.09 s | 2.07 s, 2.19 s, 2.09 s |
| 30 ms busy / 30 ms idle | 2.34 s, 2.12 s, 2.11 s | 2.05 s, 2.12 s, 2.17 s |

Before the fix the result depends on how fast the disk flushes at that moment; after it, it does
not. The re-test that counts is TC-23 on the pilot (PC A alone, and both PCs at once).
