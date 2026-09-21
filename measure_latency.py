"""
measure_latency.py — MAINTAINER-ONLY tool.

Measures round-trip latency from this machine to the Postgres endpoint named
by DATABASE_URL, for Stage 5A.3 documentation purposes.

Credentials come from `.env.maintainer`, never from the analyst `.env`.
TLS is explicit: `require` for remote targets, `prefer` only for the local
Docker stack, overridable with ALGOGUARD_DB_SSLMODE.

Usage:
    1. python -m pip install -r requirements-maintainer.txt
    2. Copy .env.maintainer.example to .env.maintainer and set DATABASE_URL
    3. python measure_latency.py
"""

import statistics
import sys
import time

from maintainer_env import describe_target, require_database_url, resolve_sslmode, safe

try:
    import psycopg2
except ImportError:
    print(
        "psycopg2 is not installed. Run: "
        "python -m pip install -r requirements-maintainer.txt",
        file=sys.stderr,
    )
    raise SystemExit(1) from None

SAMPLE_COUNT = 10


def main() -> int:
    connection_string = require_database_url()
    print(f"Measuring latency to {describe_target(connection_string)} ...")

    conn = None
    try:
        conn = psycopg2.connect(
            connection_string,
            sslmode=resolve_sslmode(connection_string),
        )
        with conn.cursor() as cur:
            # Warm-up query, excluded from the measurement so connection and
            # TLS setup are not counted as round-trip latency.
            cur.execute("select 1;")
            cur.fetchone()

            samples = []
            for index in range(SAMPLE_COUNT):
                start = time.perf_counter()
                cur.execute("select 1;")
                cur.fetchone()
                elapsed_ms = (time.perf_counter() - start) * 1000
                samples.append(elapsed_ms)
                print(f"  sample {index + 1}/{SAMPLE_COUNT}: {elapsed_ms:.1f} ms")

        samples.sort()
        print("\n--- Results ---")
        print(f"Samples: {len(samples)}")
        print(f"Min:     {samples[0]:.1f} ms")
        print(f"Median:  {statistics.median(samples):.1f} ms")
        print(f"Avg:     {statistics.fmean(samples):.1f} ms")
        print(f"Max:     {samples[-1]:.1f} ms")
        return 0
    except Exception as error:
        print("Latency measurement failed:", safe(error), file=sys.stderr)
        return 1
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    sys.exit(main())
