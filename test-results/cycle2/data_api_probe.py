"""Test cycle 2, TC-06: can an analyst read another installation's rows through the Data API?

Signs in to the cloud project in this installation's .env as the analyst whose e-mail and
password you type (the password is not shown), asks the Data API for every table's rows of
the other installation, and prints only counts. Row security must answer 0 for every table.
As a control, it also prints how many rows of each table the analyst can see at all, which
shows that the same requests do work. It signs out only this probe's own sign-in at the end,
so AlgoGuard stays signed in.

Run it yourself (it asks for a password), from the AlgoGuard folder, inside the venv:
    venv\\Scripts\\python.exe test-results\\cycle2\\data_api_probe.py --other-node <installation ID>

Prints no token, password or e-mail address. Read only: it changes nothing in the cloud.
"""
import argparse
import getpass
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from uuid import UUID

TABLES = ["node", "node_membership", "node_health", "capture_session", "ingest_event",
          "network_traffic", "prediction", "alert", "alert_detail", "report", "system_log"]


def load_settings():
    try:
        from dotenv import dotenv_values
    except ImportError:
        sys.exit("Run this inside AlgoGuard's venv (python-dotenv is missing).")
    values = {**dotenv_values(Path.cwd() / ".env"), **os.environ}
    url = (values.get("NEXT_PUBLIC_SUPABASE_URL") or "").rstrip("/")
    key = values.get("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY") or ""
    if not url.startswith("https://") or not key.startswith("sb_publishable_"):
        sys.exit("Run this from the AlgoGuard folder whose .env holds the cloud URL and key.")
    return url, key


def call(method, url, headers, body=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()


def count(url, headers, table, other_node=None):
    query = {"select": "*", "limit": "1"}
    if other_node:
        query["node_id"] = f"eq.{other_node}"
    status, response_headers, body = call(
        "GET", f"{url}/rest/v1/{table}?{urllib.parse.urlencode(query)}",
        {**headers, "Prefer": "count=exact", "Range": "0-0"})
    if status not in (200, 206):
        try:
            message = json.loads(body).get("message", "")
        except ValueError:
            message = ""
        return f"HTTP {status} {message[:60]}".strip()
    total = response_headers.get("Content-Range", "*/?").split("/")[-1]
    return total


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--other-node", required=True, help="the other installation's ID")
    other = str(UUID(parser.parse_args().other_node))
    url, key = load_settings()
    email = input("Analyst e-mail: ").strip()
    password = getpass.getpass("Password (not shown): ")
    status, _, body = call("POST", f"{url}/auth/v1/token?grant_type=password",
                           {"apikey": key, "Content-Type": "application/json"},
                           {"email": email, "password": password})
    if status != 200:
        sys.exit(f"Sign-in refused (HTTP {status}).")
    token = json.loads(body)["access_token"]
    headers = {"apikey": key, "Authorization": f"Bearer {token}", "Accept": "application/json"}
    print(f"Other installation: {other}")
    print(f"{'Table':<18} {'Other node rows (must be 0)':>28} {'Rows visible at all':>20}")
    leaked = False
    try:
        for table in TABLES:
            other_rows = count(url, headers, table, other)
            visible = count(url, headers, table)
            leaked = leaked or (other_rows.isdigit() and int(other_rows) > 0)
            print(f"{table:<18} {other_rows:>28} {visible:>20}")
    finally:
        call("POST", f"{url}/auth/v1/logout?scope=local", headers)
    print("RESULT:", "FAIL - another installation's rows are readable" if leaked
          else "PASS - no row of the other installation is readable")


if __name__ == "__main__":
    main()
