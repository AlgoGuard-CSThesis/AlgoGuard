"""Stage 6D.3: install the analyst package in a fresh folder and use it as an analyst would.

Opt-in, because it installs the Python packages from the internet into a new virtual
environment (several minutes):

    set RUN_CLEAN_INSTALL=1
    set CLEAN_INSTALL_MODEL=saved_models\\run_10\\stacking.joblib   (optional; a real model)
    set CLEAN_INSTALL_REPORT=test-results\\iter6_6d\\clean_install.json   (optional)
    pytest -m integration tests\\integration\\test_clean_install.py

The package is built from the last commit, unpacked into an empty folder outside the
repository, and set up with SETUP_ALGOGUARD.cmd. The installed app then runs as its own process
against the local stack, exactly as `START_ALGOGUARD.cmd` starts it, and is driven over HTTP the
way its pages are: sign-in, enrollment and approval by an administrator, model download, manual
prediction, every traffic source, restart with cache reuse, a cloud outage during a capture, and
attempts to reach another installation's data or create privileged accounts. Maintenance SQL
only creates fixtures and observes results.
"""

import json
import os
import platform
import re
import shutil
import socket
import subprocess
import time
import uuid
import zipfile
from pathlib import Path

import pytest
from stack_support import require_status

from scripts.build_analyst_package import build
from tests.integration.app_fixtures import make_account, publish_fixture_model, sign_in, start_app
from tests.test_traffic_sources import write_sample_pcap

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("RUN_CLEAN_INSTALL") != "1",
                       reason="set RUN_CLEAN_INSTALL=1 to install the package from scratch"),
]
ROOT = Path(__file__).resolve().parents[2]
PORT = 5077
BASE = f"http://127.0.0.1:{PORT}"
CSRF = re.compile(r'name="_csrf_token" value="([^"]+)"')
# A real UNSW-NB15 flow the deployed model classifies as Normal (user manual, section 6).
NORMAL_FLOW = {
    "dur": 0.004384, "proto": "tcp", "service": "ftp-data", "state": "FIN", "spkts": 8,
    "dpkts": 12, "sbytes": 424, "dbytes": 8824, "rate": 4333.94175, "sload": 677007.3125,
    "dload": 14760949.0, "sinpkt": 0.577714, "dinpkt": 0.350091, "ct_dst_src_ltm": 1,
    "ct_dst_ltm": 1, "ct_srv_dst": 1,
}
KONG = "supabase_kong_AlgoGuard"


def _clean_env(**extra):
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("ALGOGUARD_", "NEXT_PUBLIC_"))
           and key not in ("VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME")}
    return {**env, **extra}


def _wait(condition, timeout, interval=0.5, message="condition"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = condition()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError(f"timed out waiting for {message}")


class InstalledApp:
    """The packaged app as a separate process, started as START_ALGOGUARD.cmd starts it."""

    def __init__(self, folder: Path, log: Path):
        self.folder, self.log, self.process = folder, log, None

    def start(self):
        python = self.folder / "venv" / "Scripts" / "python.exe"
        check = subprocess.run([str(python), "config.py"], cwd=self.folder, env=_clean_env(),
                               capture_output=True, text=True, timeout=60)
        assert check.returncode == 0, check.stdout + check.stderr
        handle = self.log.open("a", encoding="utf-8")
        self.process = subprocess.Popen([str(python), "app.py"], cwd=self.folder,
                                        env=_clean_env(), stdout=handle,
                                        stderr=subprocess.STDOUT)
        started = time.monotonic()

        def listening():
            assert self.process is not None and self.process.poll() is None, self.log.read_text()
            with socket.socket() as probe:
                return probe.connect_ex(("127.0.0.1", PORT)) == 0

        _wait(listening, 180, message="the installed app to listen")
        return time.monotonic() - started

    def stop(self):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()  # closing the START_ALGOGUARD.cmd window
            self.process.wait(timeout=30)
        self.process = None


def _session():
    import requests

    session = requests.Session()
    session.trust_env = False
    return session


def _csrf(session, path):
    page = session.get(BASE + path, timeout=30)
    match = CSRF.search(page.text)
    assert match, f"no CSRF token on {path}: {page.status_code}"
    return match.group(1), page


def _login(account):
    session = _session()
    token, _ = _csrf(session, "/login")
    response = session.post(BASE + "/login", data={"email": account.email,
                                                   "password": account.password,
                                                   "_csrf_token": token},
                            allow_redirects=False, timeout=60)
    assert response.status_code == 302, response.text[:500]
    session.headers["X-CSRF-Token"] = _csrf(session, "/")[0]
    return session


def _json(session, method, path, body=None):
    response = session.request(method, BASE + path, json=body, timeout=60)
    return response.status_code, (response.json() if response.content else {})


def _refused_start(session):
    """Starting without an approved membership ends the session with the approval message."""
    status, started = _json(session, "POST", "/monitor/start",
                            {"source_type": "csv", "persist": "all"})
    if status != 200:
        return json.dumps(started)

    def ended():
        _, state = _json(session, "GET", "/monitor/status")
        return state["session"] if state["session"]["state"] in ("error", "stopped") else None

    return json.dumps(_wait(ended, 60, message="the unapproved session to end"))


def _run_session(session, body, *, until, timeout=300, during=None):
    status, started = _json(session, "POST", "/monitor/start", body)
    assert status == 200, started
    session_id = started["session"]["session_id"]
    events, began = {}, time.monotonic()
    if during:
        during()

    def poll():
        _, state = _json(session, "GET", f"/monitor/status?session_id={session_id}")
        for event in state["events"]:
            events[event["event_uuid"]] = event
        return state["session"] if until(state["session"], time.monotonic() - began) else None

    final = _wait(poll, timeout, 1.0, f"{body['source_type']} session")
    if final["state"] not in ("completed", "stopped", "error"):
        _json(session, "POST", "/monitor/stop")

    def settled():
        _, state = _json(session, "GET", f"/monitor/status?session_id={session_id}")
        sync = state["session"].get("synchronization", {})
        done = state["session"]["state"] in ("completed", "stopped") and not sync.get(
            "pending") and not sync.get("in_memory")
        return state["session"] if done else None

    final = _wait(settled, 180, 1.0, "the session's records to upload")
    return final, list(events.values()), time.monotonic() - began


def _cloud_events(cloud, capture_id):
    return cloud["sql"]("select count(*), count(distinct event_uuid) from public.ingest_event "
                        "where capture_id=%s", (capture_id,))[0]


def _session_report(final, events, seconds, cloud):
    totals, sync = final["totals"], final.get("synchronization", {})
    stored, unique = _cloud_events(cloud, final["capture_id"])
    bands = sorted({event.get("attack_score_band") or "none" for event in events})
    return {"state": final["state"], "flows": totals["flows"], "persisted": totals["persisted"],
            "synced": sync.get("synced"), "dropped": sync.get("dropped"),
            "rejected": sync.get("rejected"), "cloud_events": stored,
            "cloud_unique": unique, "attack": totals.get("attacks"),
            "score_bands_seen": bands, "seconds": round(seconds, 1)}


def _installed_secrets_scan(folder: Path, secrets: dict[str, str]):
    """Search every installed file except third-party packages for privileged values."""
    found, scanned = [], 0
    site_packages = folder / "venv" / "Lib" / "site-packages"
    for path in folder.rglob("*"):
        if not path.is_file() or site_packages in path.parents:
            continue
        scanned += 1
        content = path.read_bytes()
        for label, value in secrets.items():
            if value and value.encode() in content:
                found.append(f"{path.relative_to(folder)}: {label}")
    return {"files_scanned": scanned, "found": found}


def _environment(folder: Path):
    import psutil

    python = folder / "venv" / "Scripts" / "python.exe"
    npcap = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "Npcap" / "wpcap.dll"
    version = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f"(Get-Item '{npcap}').VersionInfo.ProductVersion"],
        capture_output=True, text=True, timeout=60).stdout.strip() if npcap.exists() else None
    return {
        "windows": platform.platform(), "processor": platform.processor(),
        "logical_cpus": psutil.cpu_count(),
        "ram_gib": round(psutil.virtual_memory().total / 2**30, 1),
        "python": subprocess.run([str(python), "--version"], capture_output=True, text=True,
                                 timeout=60).stdout.strip(),
        "npcap": version,
    }


def test_a_clean_install_reaches_monitoring_without_training_or_privileged_keys(
        cloud, local_stack, local_http, tmp_path, stacking_artifact_bytes):
    report: dict = {"date": time.strftime("%Y-%m-%d %H:%M")}
    work = tmp_path / "clean"
    work.mkdir()
    package = build(work / "AlgoGuard-analyst.zip")
    with zipfile.ZipFile(package) as archive:
        archive.extractall(work / "install")
        report["package"] = {"build": archive.read("AlgoGuard/BUILD").decode().strip(),
                             "files": len(archive.namelist()),
                             "zip_kib": round(package.stat().st_size / 1024)}
    folder = work / "install" / "AlgoGuard"

    # -- 1. Setup, as the analyst guide describes, with the two values filled in --------------
    template = (folder / ".env.example").read_text(encoding="utf-8")
    settings = (template
                .replace("NEXT_PUBLIC_SUPABASE_URL=\n",
                         f"NEXT_PUBLIC_SUPABASE_URL={local_stack['api_url']}\n")
                .replace("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=\n",
                         f"NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY={local_stack['publishable_key']}\n")
                .replace("ALGOGUARD_PORT=5000", f"ALGOGUARD_PORT={PORT}"))
    assert settings.count(local_stack["api_url"]) == 1
    (folder / ".env").write_text(settings, encoding="utf-8")
    began = time.monotonic()
    setup = subprocess.run(["cmd", "/c", str(folder / "SETUP_ALGOGUARD.cmd")], cwd=folder,
                           env=_clean_env(),
                           stdin=subprocess.DEVNULL, capture_output=True, text=True,
                           timeout=1800)
    report["setup"] = {"exit": setup.returncode, "seconds": round(time.monotonic() - began),
                       "settings_ok": "Settings OK" in setup.stdout,
                       "npcap_note": "needs Npcap" in setup.stdout}
    assert setup.returncode == 0, setup.stdout[-3000:] + setup.stderr[-2000:]
    assert report["setup"]["settings_ok"]
    assert not (folder / "database").exists() and not (folder / "saved_models").exists()

    # -- 2. Accounts and the published model, made by the administrator and maintainer ---------
    analyst = make_account(cloud, local_stack, local_http, "clean_analyst")
    admin = make_account(cloud, local_stack, local_http, "clean_admin", role="administrator")
    model_path = os.environ.get("CLEAN_INSTALL_MODEL")
    artifact = Path(model_path).read_bytes() if model_path else stacking_artifact_bytes
    manifest = publish_fixture_model(cloud, local_stack, work, artifact)
    report["model"] = {"manifest_id": manifest["manifest_id"], "bytes": manifest["object_bytes"],
                       "real_model": bool(model_path)}

    app = InstalledApp(folder, work / "app.log")
    other = None
    node = None
    try:
        report["first_start_seconds"] = round(app.start(), 1)

        # -- 3. First sign-in: enrollment, then approval by an administrator -----------------
        session = _login(analyst)
        dashboard = session.get(BASE + "/", timeout=60).text
        node = (folder / ".algoguard" / "node-id").read_text().strip()
        assert "Awaiting approval" in dashboard and node in dashboard
        refused = _refused_start(session)
        assert "administrator-approved" in refused and "installation ID" in refused
        administrator = _login(admin)
        token, nodes_page = _csrf(administrator, "/nodes")
        forms = re.findall(r'<form class="inline-form" method="post">(.*?)</form>',
                           nodes_page.text, re.S)
        form = next(item for item in forms if f'value="{analyst.profile}"' in item
                    and 'value="approved"' in item and node in item)
        fields = dict(re.findall(r'name="([^"]+)" value="([^"]*)"', form))
        fields["_csrf_token"] = token
        approved = administrator.post(BASE + "/nodes", data=fields, allow_redirects=False,
                                      timeout=60)
        assert approved.status_code == 302
        approved_at = time.monotonic()
        dashboard = session.get(BASE + "/", timeout=60).text
        assert "Node approved" in dashboard
        report["enrollment"] = {"awaiting_before_approval": True, "approved_without_restart": True}

        # -- 4. Model download and a manual prediction ---------------------------------------
        began = time.monotonic()
        assert session.get(BASE + "/simulation", timeout=300).status_code == 200
        cached = sorted((folder / ".algoguard" / "models").glob("*.joblib"))
        assert len(cached) == 1
        report["model"]["download_seconds"] = round(time.monotonic() - began, 1)
        status, predicted = _json(session, "POST", "/predict", NORMAL_FLOW)
        assert status == 200, predicted
        event = predicted["event_uuid"]
        # The first sign-in, before approval, blocked uploads; approval lifts the block at the
        # worker's next approval check (at most 30 s), with no Retry uploads or new sign-in.
        synced = _wait(lambda: (lambda reply: reply[1] if reply[1].get("synced") else None)(
            _json(session, "GET", f"/api/events/{event}")), 90, message="the prediction to sync")
        report["manual_prediction"] = {"prediction": predicted["prediction"],
                                       "attack_score_band": predicted.get("attack_score_band"),
                                       "synced": synced["synced"],
                                       "synced_seconds_after_approval":
                                           round(time.monotonic() - approved_at, 1)}

        # -- 5. Every traffic source ---------------------------------------------------------
        final, events, seconds = _run_session(
            session, {"source_type": "csv", "dataset": "algoguard_big.csv", "speed": "max",
                      "order": "sequential", "persist": "all"},
            until=lambda state, _: state["state"] in ("completed", "stopped", "error")
            or state["totals"]["persisted"] >= 300)
        report["csv_replay"] = _session_report(final, events, seconds, cloud)
        write_sample_pcap(folder / "captures" / "clean-install.pcap", sessions=10)
        final, events, seconds = _run_session(
            session, {"source_type": "pcap", "capture_file": "clean-install.pcap",
                      "speed": "max", "order": "sequential", "persist": "all"},
            until=lambda state, _: state["state"] in ("completed", "stopped", "error"))
        report["pcap_replay"] = _session_report(final, events, seconds, cloud)

        def browse():
            for url in ("http://example.com/", "https://www.python.org/", "https://example.org/"):
                try:
                    _session().get(url, timeout=10).close()
                except Exception:
                    pass

        final, events, seconds = _run_session(
            session, {"source_type": "live", "interface": "", "persist": "all"},
            until=lambda state, elapsed: elapsed > 40 or state["state"] == "error",
            during=browse)
        report["live_capture"] = _session_report(final, events, seconds, cloud)
        report["live_capture"]["packets"] = final.get("capture")
        for name in ("csv_replay", "pcap_replay", "live_capture"):
            result = report[name]
            assert result["state"] in ("completed", "stopped"), result
            assert result["synced"] == result["persisted"] == result["cloud_events"]
            assert result["cloud_unique"] == result["cloud_events"]
            assert "none" not in result["score_bands_seen"], result
        assert report["pcap_replay"]["flows"] == 11
        assert report["live_capture"]["flows"] > 0

        # -- 6. Restart: online sign-in again, the cached model reused -----------------------
        before = {path.name: path.stat().st_mtime for path in cached}
        old_cookie = session.cookies.copy()
        app.stop()
        report["restart_seconds"] = round(app.start(), 1)
        stale = _session()
        stale.cookies.update(old_cookie)
        assert stale.get(BASE + "/", allow_redirects=False, timeout=60).status_code == 302
        session = _login(analyst)
        assert session.get(BASE + "/simulation", timeout=120).status_code == 200
        after = {path.name: path.stat().st_mtime
                 for path in (folder / ".algoguard" / "models").glob("*.joblib")}
        report["restart"] = {"login_required": True, "cache_reused": after == before}
        assert after == before

        # -- 7. The cloud becomes unreachable during a capture -------------------------------
        outage: dict = {}

        def cut():
            time.sleep(4)
            subprocess.run(["docker", "stop", KONG], capture_output=True, timeout=120, check=True)
            outage["stopped"] = time.monotonic()
            time.sleep(25)
            _, state = _json(session, "GET", "/monitor/status")
            outage["pending_during"] = state["session"].get("synchronization", {}).get("pending")
            outage["upload_during"] = state["session"].get("upload", {}).get("state")
            subprocess.run(["docker", "start", KONG], capture_output=True, timeout=120,
                           check=True)
            outage["restored"] = time.monotonic()

        final, events, seconds = _run_session(
            session, {"source_type": "csv", "dataset": "algoguard_big.csv", "speed": "fast",
                      "order": "random", "persist": "all"},
            until=lambda state, elapsed: elapsed > 45, during=cut)
        recovered = time.monotonic()
        report["outage"] = {**_session_report(final, events, seconds, cloud),
                            "pending_during_outage": outage.get("pending_during"),
                            "upload_state_during_outage": outage.get("upload_during"),
                            "synced_within_seconds_of_reconnect":
                                round(recovered - outage["restored"], 1)}
        assert outage["pending_during"] and outage["pending_during"] > 0
        result = report["outage"]
        assert result["synced"] == result["persisted"] == result["cloud_events"]
        assert result["cloud_unique"] == result["cloud_events"]

        # -- 8. Hostile edits and direct requests --------------------------------------------
        other_account = make_account(cloud, local_stack, local_http, "clean_other")
        other = start_app(cloud, local_stack, work / "other-state", [other_account])
        other_client = sign_in(other.app, other_account)
        response = other_client.post("/predict", json=NORMAL_FLOW,
                                     headers={"X-CSRF-Token": other_client.csrf})
        other_event = response.get_json()["event_uuid"]
        _wait(lambda: other_client.get(f"/api/events/{other_event}").get_json().get("synced"),
              60, message="the other computer's record")
        signed = local_http.post(local_stack["api_url"] + "/auth/v1/token",
                                 headers={"apikey": local_stack["publishable_key"]},
                                 params={"grant_type": "password"},
                                 json={"email": analyst.email, "password": analyst.password})
        require_status(signed, (200,), "analyst token")
        tokens = signed.json()
        headers = {"apikey": local_stack["publishable_key"],
                   "Authorization": "Bearer " + tokens["access_token"]}
        api = local_stack["api_url"]
        visible = {table: local_http.get(f"{api}/rest/v1/{table}", headers=headers,
                                         params={"select": "node_id"}).json()
                   for table in ("ingest_event", "capture_session", "alert")}
        foreign = {table: [row for row in rows if row["node_id"] != node]
                   for table, rows in visible.items()}
        asked = local_http.get(f"{api}/rest/v1/ingest_event", headers=headers,
                               params={"select": "event_uuid",
                                       "event_uuid": f"eq.{other_event}"}).json()
        create_admin = local_http.post(f"{api}/functions/v1/account_admin", headers=headers,
                                       json={"request_id": str(uuid.uuid4()),
                                             "action": "create_account",
                                             "email": f"{cloud['prefix']}_x@algoguard.invalid",
                                             "password": "Hostile!Aa1" + uuid.uuid4().hex,
                                             "role": "administrator"})
        self_promote = local_http.post(f"{api}/rest/v1/user_role", headers=headers,
                                       json={"profile_id": analyst.profile,
                                             "role": "administrator"})
        self_approve = local_http.post(f"{api}/rest/v1/node_membership", headers=headers,
                                       json={"node_id": other.node,
                                             "profile_id": analyst.profile,
                                             "status": "approved"})
        report["direct_requests"] = {
            "rows_of_other_installations_visible": {t: len(rows) for t, rows in foreign.items()},
            "other_installation_event_by_id": len(asked),
            "create_administrator_status": create_admin.status_code,
            "self_promote_status": self_promote.status_code,
            "self_approve_on_other_installation_status": self_approve.status_code,
        }
        assert not any(foreign.values()) and not asked
        assert create_admin.status_code == 403
        assert self_promote.status_code in (401, 403)
        assert self_approve.status_code in (401, 403)
        roles = cloud["sql"]("select role from public.user_role where profile_id=%s",
                             (analyst.profile,))
        assert roles == [("analyst",)]

        # Pointing this installation at the other computer's ID gains nothing.
        node_file = folder / ".algoguard" / "node-id"
        app.stop()
        node_file.write_text(other.node, encoding="ascii")
        app.start()
        hijack = _login(analyst)
        page = hijack.get(BASE + "/", timeout=60).text
        refused = _refused_start(hijack)
        report["node_id_edit"] = {"awaiting_approval": "Awaiting approval" in page,
                                  "monitor_refused": "administrator-approved" in refused}
        assert "Awaiting approval" in page and "administrator-approved" in refused
        app.stop()
        node_file.write_text(node, encoding="ascii")

        # A secret key in .env is refused before anything starts.
        original = (folder / ".env").read_text(encoding="utf-8")
        (folder / ".env").write_text(original.replace(local_stack["publishable_key"],
                                                      "sb_secret_" + "x" * 24), encoding="utf-8")
        python = folder / "venv" / "Scripts" / "python.exe"
        checked = subprocess.run([str(python), "config.py"], cwd=folder, env=_clean_env(),
                                 capture_output=True, text=True, timeout=60)
        refused_start = subprocess.run([str(python), "app.py"], cwd=folder, env=_clean_env(),
                                       capture_output=True, text=True, timeout=120)
        (folder / ".env").write_text(original, encoding="utf-8")
        report["secret_key_in_env"] = {"config_exit": checked.returncode,
                                       "app_exit": refused_start.returncode,
                                       "traceback": "Traceback" in refused_start.stderr}
        assert checked.returncode == 2 and refused_start.returncode == 1
        assert "publishable key" in refused_start.stderr

        # -- 9. No privileged value anywhere in the installation -----------------------------
        report["secrets_scan"] = _installed_secrets_scan(folder, {
            "maintainer secret key": local_stack["secret_key"],
            "database connection string": local_stack["db_url"],
            "analyst password": analyst.password,
            "administrator password": admin.password,
            "access token": tokens["access_token"],
            "refresh token": tokens["refresh_token"],
        })
        assert not report["secrets_scan"]["found"]
        report["no_training_files"] = not any(
            (folder / name).exists() for name in ("train.py", "database", "saved_models"))
        assert report["no_training_files"]
        report["environment"] = _environment(folder)
    finally:
        app.stop()
        if other is not None:
            other.services.close()
        if node:
            # Named with the fixture prefix, the installation is removed with the fixture data.
            cloud["sql"]("update public.node set display_name=%s where node_id=%s",
                         (cloud["prefix"] + "_clean_install", node))
        local_http.delete(local_stack["api_url"] + "/storage/v1/object/models/"
                          + manifest["object_path"], headers=cloud["maintenance"])
        destination = os.environ.get("CLEAN_INSTALL_REPORT")
        if destination:
            Path(destination).parent.mkdir(parents=True, exist_ok=True)
            Path(destination).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            shutil.copy(work / "app.log", Path(destination).with_suffix(".app.log"))
