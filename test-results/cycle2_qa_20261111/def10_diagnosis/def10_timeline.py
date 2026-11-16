"""DEF-10 diagnosis, read-only: how the 300 stored events of each burst capture reached the pilot.

Groups events by received_at (one value per upload request if received_at is the transaction
time) and prints, per group: size, first/last event time relative to the capture's first event,
and arrival time relative to the same origin.
"""
import sys
from collections import OrderedDict

sys.path.insert(0, ".")
from maintainer_env import load_maintainer_env, require_database_url  # noqa: E402
from maintenance_connections import connect_database  # noqa: E402

CAPTURES = [int(v) for v in sys.argv[1:]] or [57, 58, 60, 62]

load_maintainer_env()
c = connect_database(require_database_url())
c.set_session(readonly=True, autocommit=True)
with c.cursor() as cur:
    cur.execute("select column_default from information_schema.columns where table_schema='public' "
                "and table_name='ingest_event' and column_name='received_at'")
    print("received_at default:", cur.fetchone())
    for capture in CAPTURES:
        cur.execute("select event_time, received_at from public.ingest_event where capture_id = %s "
                    "order by event_time", (capture,))
        rows = cur.fetchall()
        if not rows:
            print(capture, "no rows")
            continue
        origin = rows[0][0]
        groups = OrderedDict()
        for event_time, received in rows:
            groups.setdefault(received, []).append(event_time)
        print(f"\n== capture {capture}: {len(rows)} events, {len(groups)} distinct received_at; "
              f"events span {(rows[-1][0] - origin).total_seconds():.2f} s")
        for received, times in sorted(groups.items()):
            delays = [(received - t).total_seconds() for t in times]
            print(f"  arrive +{(received - origin).total_seconds():6.2f} s  n={len(times):3d}  "
                  f"events +{(min(times) - origin).total_seconds():5.2f}..+"
                  f"{(max(times) - origin).total_seconds():5.2f}  "
                  f"delay {min(delays):.2f}-{max(delays):.2f}")
c.close()
