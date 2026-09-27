"""
check_db_connection.py — MAINTAINER-ONLY tool.

Verifies that a maintainer can reach the Postgres endpoint named by
DATABASE_URL (the Supabase pooler connection string, transaction mode,
port 6543).

Credentials come from `.env.maintainer`, never from the analyst `.env`.
TLS is explicit: `require` for remote targets, `prefer` only for the local
Docker stack, overridable with ALGOGUARD_DB_SSLMODE.

Usage:
    1. python -m pip install -r requirements-maintainer.txt
    2. Copy .env.maintainer.example to .env.maintainer and set DATABASE_URL
    3. python check_db_connection.py
"""

import sys

from maintainer_env import describe_target, require_database_url, resolve_sslmode, safe
from maintenance_connections import connect_database


def main() -> int:
    try:
        import psycopg2  # noqa: F401 - explicit prerequisite check for this CLI
    except ImportError:
        print("Install requirements-maintainer.txt to run this tool.", file=sys.stderr)
        return 1
    connection_string = require_database_url()
    print(f"Connecting to {describe_target(connection_string)} ...")

    conn = None
    try:
        conn = connect_database(
            connection_string,
            sslmode=resolve_sslmode(connection_string),
        )
        with conn.cursor() as cur:
            cur.execute("select now() as server_time, version() as pg_version;")
            server_time, pg_version = cur.fetchone()

        print("Connected successfully.")
        print("Server time:", server_time)
        print("Postgres version:", pg_version.split(",")[0])
        return 0
    except Exception as error:
        # safe() strips the password out of any connection string echoed
        # back inside the driver's error message.
        print("Connection failed:", safe(error), file=sys.stderr)
        return 1
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    sys.exit(main())
