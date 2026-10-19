"""TC-10 (SEC-01): five wrong passwords for one existing local account, then a sixth attempt.

Runs against a local-mode (SQLite) app started on its own port, e.g.
    set ALGOGUARD_DB_MODE=sqlite & set ALGOGUARD_PORT=5001 & python app.py
Every password sent is a made-up wrong value; no real password is used or recorded. The lockout
lives in that app process's memory, so stopping the app clears it.
"""
import html
import json
import re
import sys
from datetime import datetime

import requests

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5001"
USER = sys.argv[2] if len(sys.argv) > 2 else "admin"
s = requests.Session()
s.trust_env = False  # never send local traffic through a proxy


def token(page):
    m = re.search(r'name="_csrf_token" value="([^"]+)"', page)
    return m.group(1) if m else ""


def flashes(page):
    found = re.findall(r'class="[^"]*alert[^"]*"[^>]*>(.*?)</div>', page, flags=re.S)
    text = [" ".join(html.unescape(re.sub(r"<[^>]+>", " ", f)).split()) for f in found]
    return [t for t in text if t]


page = s.get(f"{BASE}/login", timeout=10).text
results = []
for attempt in range(1, 7):
    resp = s.post(f"{BASE}/login", data={"username": USER, "password": f"wrong-password-{attempt}",
                                         "_csrf_token": token(page), "next": ""},
                  timeout=10, allow_redirects=False)
    page = resp.text
    results.append({"attempt": attempt, "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "http_status": resp.status_code, "messages": flashes(page)})
    print(results[-1])
out = {"base": BASE, "account": USER, "passwords": "made-up wrong values only (not recorded)",
       "attempts": results}
print(json.dumps(out, indent=2))
