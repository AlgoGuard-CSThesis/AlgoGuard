"""
measure_latency.py
Measures round-trip latency from this machine to the Supabase pilot project's
Postgres endpoint, for Stage 5A.3 documentation purposes.

Usage:
    pip install psycopg2-binary python-dotenv
    Set DATABASE_URL (the pooler connection string) via env var or .env,
    then: python measure_latency.py
"""

import os
import sys
import time

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import psycopg2

connection_string = os.environ.get("DATABASE_URL")

if not connection_string:
    print("DATABASE_URL is not set.")
    sys.exit(1)

SAMPLE_COUNT = 10

try:
    conn = psycopg2.connect(connection_string, sslmode="prefer")
    cur = conn.cursor()

    # Warm-up query (excluded from measurement, avoids counting connection setup)
    cur.execute("select 1;")
    cur.fetchone()

    samples = []
    for i in range(SAMPLE_COUNT):
        start = time.perf_counter()
        cur.execute("select 1;")
        cur.fetchone()
        elapsed_ms = (time.perf_counter() - start) * 1000
        samples.append(elapsed_ms)
        print(f"  sample {i + 1}/{SAMPLE_COUNT}: {elapsed_ms:.1f} ms")

    cur.close()
    conn.close()

    samples.sort()
    avg = sum(samples) / len(samples)
    median = samples[len(samples) // 2]

    print("\n--- Results ---")
    print(f"Min:    {samples[0]:.1f} ms")
    print(f"Median: {median:.1f} ms")
    print(f"Avg:    {avg:.1f} ms")
    print(f"Max:    {samples[-1]:.1f} ms")

except Exception as e:
    print("❌ Latency measurement failed:", e)
    sys.exit(1)
