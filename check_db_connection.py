"""
test_connection.py
Usage:
    1. pip install psycopg2-binary python-dotenv
    2. Make sure DATABASE_URL is set in your .env file, or exported in your shell
    3. python test_connection.py
"""

import os
import sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv is optional; will just rely on shell env vars if not installed

import psycopg2

connection_string = os.environ.get("DATABASE_URL")

if not connection_string:
    print("DATABASE_URL is not set. Add it to your .env file or export it in your shell.")
    sys.exit(1)

try:
    conn = psycopg2.connect(connection_string, sslmode="prefer")
    cur = conn.cursor()
    cur.execute("select now() as server_time, version() as pg_version;")
    server_time, pg_version = cur.fetchone()

    print("✅ Connected successfully via pooler.")
    print("Server time:", server_time)
    print("Postgres version:", pg_version.split(",")[0])

    cur.close()
    conn.close()
except Exception as e:
    print("❌ Connection failed:", e)
    sys.exit(1)
