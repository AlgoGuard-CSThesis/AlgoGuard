"""SEC-01 roles check (local mode): an Analyst cannot reach administrator pages.

Creates a throwaway Analyst account `cycle1_roles_test` in the local SQLite database (if missing)
with a random password that exists only in this process's memory, signs in to the running
local-mode app, and requests /admins, /nodes and /dashboard. Nothing secret is printed or saved.
Run with ALGOGUARD_DB_MODE=sqlite while the local app listens on the given base URL.
"""
import html
import json
import re
import secrets
import sys
from datetime import datetime
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from services.database_service import create_admin, get_admin_by_username  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5001"
USER = "cycle1_roles_test"
password = "Aa1!" + secrets.token_urlsafe(18)  # meets the 12+ char upper/lower/digit/symbol rule
existing = get_admin_by_username(USER)
if existing:
    raise SystemExit(f"{USER} already exists; its password is unknown - rerun with a new name.")
create_admin(USER, password, email="", role="Analyst")
created_role = get_admin_by_username(USER)["role"]

s = requests.Session()
s.trust_env = False


def token(page):
    m = re.search(r'name="_csrf_token" value="([^"]+)"', page)
    return m.group(1) if m else ""


def flashes(page):
    found = re.findall(r'class="[^"]*alert[^"]*"[^>]*>(.*?)</div>', page, flags=re.S)
    return [t for t in (" ".join(html.unescape(re.sub(r"<[^>]+>", " ", f)).split())
                        for f in found) if t]


def title(page):
    m = re.search(r"<title>(.*?)</title>", page, flags=re.S)
    return " ".join(m.group(1).split()) if m else ""


login_page = s.get(f"{BASE}/login", timeout=10).text
r = s.post(f"{BASE}/login", data={"username": USER, "password": password,
                                  "_csrf_token": token(login_page), "next": ""},
           timeout=10, allow_redirects=True)
del password
results = {"time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "account": USER,
           "role": created_role, "sign_in": {"final_url": r.url.replace(BASE, ""),
                                             "title": title(r.text), "messages": flashes(r.text)}}
for path in ("/admins", "/nodes", "/dashboard"):
    first = s.get(f"{BASE}{path}", timeout=10, allow_redirects=False)
    final = s.get(f"{BASE}{path}", timeout=10, allow_redirects=True)
    results[path] = {"first_status": first.status_code,
                     "redirect_to": first.headers.get("Location", "").replace(BASE, ""),
                     "final_url": final.url.replace(BASE, ""), "final_status": final.status_code,
                     "final_title": title(final.text), "messages": flashes(final.text)}
print(json.dumps(results, indent=2))
