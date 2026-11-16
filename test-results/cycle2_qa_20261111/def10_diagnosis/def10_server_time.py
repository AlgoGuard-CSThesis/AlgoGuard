"""DEF-10 diagnosis 3, read-only: how long does the pilot's database spend in store_flow_batch?"""
import sys

sys.path.insert(0, ".")
from maintainer_env import load_maintainer_env, require_database_url  # noqa: E402
from maintenance_connections import connect_database  # noqa: E402

load_maintainer_env()
c = connect_database(require_database_url())
c.set_session(readonly=True, autocommit=True)
with c.cursor() as cur:
    cur.execute("select extname from pg_extension where extname = 'pg_stat_statements'")
    print("pg_stat_statements:", cur.fetchone())
    cur.execute("select calls, round(mean_exec_time::numeric, 1), round(max_exec_time::numeric, 1), "
                "round(stddev_exec_time::numeric, 1), rows, left(regexp_replace(query, '\\s+', ' ', 'g'), 110) "
                "from extensions.pg_stat_statements where query ilike '%%store_flow_batch%%' "
                "order by calls desc limit 8")
    for row in cur.fetchall():
        print("  calls", row[0], "mean ms", row[1], "max ms", row[2], "sd", row[3], "rows", row[4], "|", row[5])
    cur.execute("select p.proname, p.prosecdef, l.lanname, length(p.prosrc) from pg_proc p "
                "join pg_language l on l.oid = p.prolang where p.proname like 'store_flow%%'")
    print("functions:", cur.fetchall())
c.close()
