"""DEF-10 / DEF-11 re-test: upload delay and acknowledged events per second, read-only.

For each capture ID given, reads the hosted pilot's ingest_event rows and reports the upload
delay (cloud received_at minus the client's event_time) p50/p95/max, and the acknowledged
events per second while the capture's events arrived. Also estimates this PC's clock offset
from the database clock, since the delay mixes both clocks.

Run from the repository folder, inside the venv (uses .env.maintainer, read-only session):
    python test-results/def_fixes_20261023/upload_delay.py <capture_id> [<capture_id> ...]
Prints JSON; no e-mail addresses, keys or tokens.
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from maintainer_env import load_maintainer_env, require_database_url  # noqa: E402
from maintenance_connections import connect_database  # noqa: E402

QUERY = """
select count(*),
       percentile_cont(0.5) within group (order by extract(epoch from received_at - event_time)),
       percentile_cont(0.95) within group (order by extract(epoch from received_at - event_time)),
       max(extract(epoch from received_at - event_time)),
       min(event_time), max(event_time), min(received_at), max(received_at),
       count(distinct event_uuid)
from public.ingest_event where capture_id = %s
"""
DETAIL_QUERY = """
select event_time, received_at, extract(epoch from received_at - event_time)
from public.ingest_event where capture_id = %s order by event_time
"""
DETAIL = "--detail" in sys.argv


def main(capture_ids):
    load_maintainer_env()
    connection = connect_database(require_database_url())
    connection.set_session(readonly=True, autocommit=True)
    results = {}
    try:
        with connection.cursor() as cursor:
            before = time.time()
            cursor.execute("select extract(epoch from clock_timestamp())")
            server = float(cursor.fetchone()[0])
            after = time.time()
            offset = server - (before + after) / 2  # positive: this PC is behind the database
            for capture_id in capture_ids:
                if DETAIL:
                    cursor.execute(DETAIL_QUERY, (capture_id,))
                    results[f"{capture_id}_timeline"] = [
                        {"event_time_utc": t.isoformat(), "received_utc": r.isoformat(),
                         "delay_s": round(float(d), 3)} for t, r, d in cursor.fetchall()]
                cursor.execute(QUERY, (capture_id,))
                (count, p50, p95, worst, first_event, last_event, first_received,
                 last_received, unique) = cursor.fetchone()
                span = (last_received - first_received).total_seconds() if count else 0.0
                results[str(capture_id)] = {
                    "events": count,
                    "unique_events": unique,
                    "upload_delay_s": {"p50": p50, "p95": p95, "max": worst},
                    "first_event_time_utc": first_event.isoformat() if first_event else None,
                    "last_event_time_utc": last_event.isoformat() if last_event else None,
                    "received_span_s": round(span, 3),
                    "acknowledged_events_per_s": round(count / span, 2) if span > 0 else None,
                }
    finally:
        connection.close()
    report = {"clock_offset_s_database_minus_pc": round(offset, 3),
              "round_trip_s": round(after - before, 3), "captures": results}
    print(json.dumps(report, indent=2, default=float))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    main([int(value) for value in sys.argv[1:] if value != "--detail"])
