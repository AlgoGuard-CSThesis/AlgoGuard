"""Stage 5D cloud application against the offline fake Supabase service.

These tests exercise the real Flask routes, CloudAuth, CloudRepository, outbox,
upload worker, and cloud monitor over HTTP. The fake service stands in for
Supabase; the integration lane checks the same paths against the real stack.
"""

import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

import pytest

import cloud_outbox
from cloud_app import build_services, create_cloud_app
from services import database_service
from tests.cloud_support import (
    ADMIN,
    ANALYST,
    OTHER,
    attack_rows,
    login,
    make_cloud,
    monitor_until,
    post_json,
    signed_in,
    wait_for,
)

MANUAL_FLOW = {
    "dur": "5.5", "proto": "tcp", "service": "-", "state": "INT", "spkts": "2", "dpkts": "0",
    "sbytes": "900000", "dbytes": "0", "rate": "900", "sttl": "254", "dttl": "0",
    "sload": "1000000", "dload": "0", "sinpkt": "0.01", "dinpkt": "0",
}


def owned_events(cloud, email):
    user = cloud.fake.users[email]
    return [row for row in cloud.fake.rows("ingest_event")
            if row["owner_profile_id"] == user["profile_id"]]


# -- 5D.1 login, sessions, and scoped reads ------------------------------------

def test_login_uses_cloud_identity_and_cookie_holds_only_an_opaque_id(cloud):
    client = cloud.app.test_client()
    page = client.get("/login").get_data(as_text=True)
    assert 'name="email"' in page and 'name="username"' not in page
    response = login(client, *ANALYST[:2])
    assert response.status_code == 302 and response.headers["Location"] == "/"
    cookie = response.headers["Set-Cookie"]
    assert cookie.startswith("algoguard_cloud_session=")
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie
    assert "eyJ" not in cookie and ANALYST[1] not in cookie
    session = next(iter(cloud.app.session_interface.sessions.values()))
    assert session.identity.username == "analyst"
    assert "access_token" not in repr(session.identity)
    dashboard = client.get("/").get_data(as_text=True)
    assert "Node approved" in dashboard and cloud.node in dashboard
    assert "Welcome back to AlgoGuard." in dashboard


def test_rejected_and_offline_logins_never_create_a_session(cloud):
    client = cloud.app.test_client()
    wrong = login(client, ANALYST[0], "Wrong-Password-1!")
    assert wrong.status_code == 200
    assert "not accepted" in wrong.get_data(as_text=True)
    cloud.fake.offline = True
    offline = login(client, *ANALYST[:2])
    assert "cloud is unreachable" in offline.get_data(as_text=True)
    assert "offline access" in offline.get_data(as_text=True)
    assert all(item.identity is None for item in cloud.app.session_interface.sessions.values())
    assert client.get("/").status_code == 302


def test_deactivated_and_historical_profiles_cannot_sign_in(cloud):
    cloud.fake.add_historical_profile("legacy-admin")
    for row in cloud.fake.tables["profile"]:
        if row["profile_id"] == cloud.other["profile_id"]:
            row["is_active"] = False
    client = cloud.app.test_client()
    assert "not accepted" in login(client, *OTHER[:2]).get_data(as_text=True)
    assert "not accepted" in login(client, "legacy-admin", "anything").get_data(as_text=True)


def test_restart_requires_online_login_even_with_a_cached_model(cloud):
    client = signed_in(cloud.app)
    assert client.get("/simulation").status_code == 200  # downloads and caches the model
    assert list((cloud.cfg.state_dir / "models").glob("*.joblib"))
    cloud.services.close()
    restarted = build_services(cloud.cfg)
    app = create_cloud_app(cloud.cfg, services=restarted, start_workers=False)
    try:
        assert restarted.node_id == UUID(cloud.node)  # installation identity persists
        browser = app.test_client()
        browser.set_cookie("algoguard_cloud_session",
                           client.get_cookie("algoguard_cloud_session").value)
        response = browser.get("/")
        assert response.status_code == 302 and "/login" in response.headers["Location"]
        cloud.fake.offline = True
        page = login(app.test_client(), *ANALYST[:2]).get_data(as_text=True)
        assert "Online sign-in is required" in page
    finally:
        restarted.close()


def test_logout_stops_tokens_and_ends_only_this_installations_session(cloud):
    client = signed_in(cloud.app)
    identity = next(iter(cloud.app.session_interface.sessions.values())).identity
    response = client.post("/logout", data={"_csrf_token": client.csrf})
    assert response.status_code == 302 and "logged_out=1" in response.headers["Location"]
    assert not identity.active and identity.access_token == ""
    assert cloud.fake.logouts[-1][1] == "local"
    assert client.get("/").status_code == 302


def test_cookie_writes_require_csrf_but_bearer_api_calls_do_not(cloud):
    client = signed_in(cloud.app)
    assert client.post("/predict", json=MANUAL_FLOW).status_code == 400
    assert client.post("/logout").status_code == 400
    token = cloud.fake.token_for(ANALYST[0])
    api = cloud.app.test_client()
    response = api.post("/predict", json=MANUAL_FLOW,
                        headers={"Authorization": "Bearer " + token})
    assert response.status_code == 200, response.get_json()
    assert "Set-Cookie" not in response.headers
    assert api.get("/", headers={"Authorization": "Bearer " + token}).status_code == 400
    forged = api.post("/predict", json=MANUAL_FLOW, headers={"Authorization": "Bearer forged"})
    assert forged.status_code == 401 and forged.get_json()["status"] == "error"
    anonymous = api.post("/predict", json=MANUAL_FLOW)
    assert anonymous.status_code == 401


def test_concurrent_bearer_requests_never_swap_identities(cloud):
    tokens = {email: cloud.fake.token_for(email) for email in (ANALYST[0], ADMIN[0])}
    api = cloud.app.test_client()

    def predict(email):
        response = api.post("/predict", json=MANUAL_FLOW,
                            headers={"Authorization": "Bearer " + tokens[email]})
        return email, response.get_json()["event_uuid"]

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(predict, [ANALYST[0], ADMIN[0]] * 10))
    outbox = cloud.services.outbox
    with outbox.db_lock:
        owners = dict(outbox.db.execute("select event_uuid,profile_id from event").fetchall())
    for email, event_uuid in results:
        assert owners[event_uuid] == cloud.fake.users[email]["profile_id"]


def test_enrollment_on_login_and_administrator_approval(tmp_path, stacking_artifact_bytes,
                                                        fast_replay):
    env = make_cloud(tmp_path, stacking_artifact_bytes, approve=False)
    try:
        analyst = signed_in(env.app)
        memberships = env.fake.rows("node_membership")
        assert [row["status"] for row in memberships if row["node_id"] == env.node] == [
            "pending"]
        page = analyst.get("/").get_data(as_text=True)
        assert "Awaiting approval" in page and env.node in page
        refused = post_json(analyst, "/monitor/start", {"source_type": "csv", "speed": "fast"})
        assert refused.status_code == 200
        state = monitor_until(analyst, lambda data: data["session"]["state"] == "error")
        assert "administrator-approved membership" in state["session"]["error_message"]
        assert analyst.get("/admins").status_code == 302  # analysts cannot administer
        # An administrator approves from their own (already approved) installation.
        env.fake.approve(env.admin["profile_id"], env.node)
        admin = signed_in(env.app, ADMIN)
        queue = admin.get("/nodes").get_data(as_text=True)
        assert "Pending Requests" in queue and "analyst" in queue
        request_id = re.search(r'name="request_id" value="([^"]+)"', queue).group(1)
        response = admin.post("/nodes", data={
            "_csrf_token": admin.csrf, "request_id": request_id, "action": "membership",
            "status": "approved", "node_id": env.node,
            "profile_id": str(env.analyst["profile_id"]), "is_default": "true",
        })
        assert response.status_code == 302
        assert "Node approved" in analyst.get("/").get_data(as_text=True)
    finally:
        env.services.close()
        env.fake.stop()


def test_every_page_reads_through_the_cloud_and_never_opens_sqlite(cloud, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("cloud mode opened the legacy business database")

    monkeypatch.setattr(database_service, "get_connection", forbidden)
    client = signed_in(cloud.app, ADMIN)
    prediction = client.post("/simulation", data={**MANUAL_FLOW, "_csrf_token": client.csrf})
    assert prediction.status_code == 200
    for path in ("/", "/monitor", "/simulation", "/alerts", "/logs?search=flow&module=detection",
                 "/logs?date_from=2020-01-01&date_to=2999-12-31", "/reports", "/nodes",
                 "/admins", "/outbox/export"):
        response = client.get(path)
        assert response.status_code == 200, (path, response.get_data(as_text=True)[:500])
    rest = [path for method, path in cloud.fake.calls if path.startswith("/rest/v1/")]
    assert any("alert_detail" in path for path in rest) and any("system_log" in path
                                                                 for path in rest)


def test_network_failure_pages_are_controlled_without_recursive_logging(cloud):
    client = signed_in(cloud.app)
    before = cloud.services.outbox.counts(
        next(iter(cloud.app.session_interface.sessions.values())).identity)
    cloud.fake.offline = True
    response = client.get("/alerts")
    assert response.status_code == 503
    assert "cloud is unavailable" in response.get_data(as_text=True)
    status = client.get("/monitor/status")
    assert status.status_code == 200  # local state needs no cloud round trip
    api = post_json(client, "/predict", MANUAL_FLOW)
    assert api.status_code == 503 and api.get_json()["status"] == "error"
    after = cloud.services.outbox.counts(
        next(iter(cloud.app.session_interface.sessions.values())).identity)
    assert after["summaries_pending"] == before["summaries_pending"]


# -- 5D.2/5D.3 persistence contract ------------------------------------------------

def test_manual_prediction_reports_queued_state_until_acknowledged(cloud):
    client = signed_in(cloud.app)
    page = client.post("/simulation", data={**MANUAL_FLOW, "_csrf_token": client.csrf})
    text = page.get_data(as_text=True)
    assert "Assigned after upload" in text and "Saved on this computer" in text
    result = post_json(client, "/predict", MANUAL_FLOW).get_json()
    assert result["status"] == "success" and result["prediction_id"] is None
    assert result["persistence"] in ("in_memory", "durable_pending") and not result["synced"]
    UUID(result["event_uuid"])
    synced = wait_for(lambda: (lambda data: data if data.get("synced") else None)(
        client.get(f"/api/events/{result['event_uuid']}").get_json()))
    assert synced["persistence"] == "synced" and int(synced["prediction_id"]) > 0
    stored = [row for row in cloud.fake.rows("ingest_event")
              if row["event_uuid"] == result["event_uuid"]]
    assert len(stored) == 1 and stored[0]["payload"]["capture_id"] is None
    assert client.get("/api/events/00000000-0000-4000-8000-000000000000").status_code == 404


def test_csv_capture_pins_deployment_and_uploads_through_the_outbox(cloud):
    client = signed_in(cloud.app)
    started = post_json(client, "/monitor/start", {
        "source_type": "csv", "speed": "fast", "persist": "all"})
    assert started.status_code == 200 and started.get_json()["session"]["cloud"]
    running = monitor_until(client, lambda data: data["session"]["totals"]["flows"] >= 12)
    session = running["session"]
    manifest = next(row for row in cloud.fake.rows("model_manifest") if row["status"] == "active")
    assert session["deployment"]["deployment_id"] == manifest["deployment_id"]
    assert {event["persistence"] for event in running["events"]} <= {
        "in_memory", "durable_pending", "synced"}
    stopped = post_json(client, "/monitor/stop").get_json()["session"]
    assert stopped["state"] == "stopped" and stopped["stopped_at"]
    assert stopped["local_drain_complete"] is True
    queued = stopped["totals"]["queued"]
    wait_for(lambda: len(owned_events(cloud, ANALYST[0])) >= queued)
    capture = wait_for(lambda: next((row for row in cloud.fake.rows("capture_session")
                                     if row["closed_at"]), None))
    assert capture["status"] == "stopped" and capture["deployment_id"] == manifest[
        "deployment_id"]
    assert capture["flows_emitted"] >= queued
    assert all(row["payload"]["capture_id"] == capture["capture_id"]
               for row in owned_events(cloud, ANALYST[0]))
    final = monitor_until(client, lambda data: data["session"]["synchronization"]["synced"]
                          >= queued)
    assert final["session"]["state"] == "stopped"
    audit = wait_for(lambda: [row for row in cloud.fake.rows("system_log")
                              if row.get("action") == "monitor_stopped"])
    assert audit[0]["module"] == "Live Monitor"


def test_session_cap_reserves_slots_before_enqueue(cloud, monkeypatch):
    monkeypatch.setattr(cloud_outbox, "SESSION_LIMIT", 5)
    client = signed_in(cloud.app)
    post_json(client, "/monitor/start", {"source_type": "csv", "speed": "fast", "persist": "all"})
    data = monitor_until(client, lambda data: data["session"]["totals"]["flows"] >= 10)
    assert data["session"]["capped"] is True
    assert data["session"]["totals"]["queued"] == 5
    post_json(client, "/monitor/stop")
    assert data["session"]["synchronization"]["dropped"] >= 1


def test_stop_is_bounded_even_when_local_storage_stalls(cloud, monkeypatch):
    cloud.services.monitor.drain_seconds = 1
    client = signed_in(cloud.app)
    original = cloud.services.outbox._persist
    release = threading.Event()

    def stalled(item):
        release.wait(10)
        original(item)

    monkeypatch.setattr(cloud.services.outbox, "_persist", stalled)
    post_json(client, "/monitor/start", {"source_type": "csv", "speed": "fast", "persist": "all"})
    monitor_until(client, lambda data: data["session"]["totals"]["flows"] >= 3)
    started = time.monotonic()
    stopped = post_json(client, "/monitor/stop").get_json()["session"]
    elapsed = time.monotonic() - started
    release.set()
    assert stopped["state"] == "stopped"  # terminal regardless of pending storage/upload
    assert stopped["local_drain_complete"] is False
    assert elapsed < 3


def test_network_loss_keeps_capturing_and_reconnect_uploads_without_duplicates(cloud):
    client = signed_in(cloud.app)
    post_json(client, "/monitor/start", {"source_type": "csv", "speed": "fast", "persist": "all"})
    monitor_until(client, lambda data: data["session"]["state"] == "running")
    cloud.fake.offline = True
    offline = monitor_until(client, lambda data: data["session"]["totals"]["flows"] >= 15
                            and data["session"]["upload"]["state"] == "retrying")
    assert offline["session"]["state"] == "running"
    assert offline["session"]["synchronization"]["pending"] > 0
    stopped = post_json(client, "/monitor/stop").get_json()["session"]
    cloud.fake.offline = False
    cloud.services.sync.retry.clear()
    queued = stopped["totals"]["queued"]
    wait_for(lambda: len(owned_events(cloud, ANALYST[0])) >= queued, timeout=20)
    uuids = [row["event_uuid"] for row in owned_events(cloud, ANALYST[0])]
    assert len(uuids) == len(set(uuids)) == queued


def test_lost_acknowledgement_is_reconciled_by_server_deduplication(cloud):
    client = signed_in(cloud.app)
    cloud.fake.fail("POST", "/rest/v1/rpc/store_flow_batch", after=True, status=504)
    result = post_json(client, "/predict", MANUAL_FLOW).get_json()
    wait_for(lambda: cloud.services.sync.retry)
    cloud.services.sync.retry.clear()
    wait_for(lambda: client.get(f"/api/events/{result['event_uuid']}").get_json().get("synced"))
    assert len([row for row in cloud.fake.rows("ingest_event")
                if row["event_uuid"] == result["event_uuid"]]) == 1
    assert cloud.fake.count_calls("POST", "/rest/v1/rpc/store_flow_batch") >= 2


def test_one_invalid_event_does_not_reject_its_batch(cloud):
    identity_client = signed_in(cloud.app)
    cloud.services.sync.close()
    good = post_json(identity_client, "/predict", MANUAL_FLOW).get_json()["event_uuid"]
    bad = post_json(identity_client, "/predict", MANUAL_FLOW).get_json()["event_uuid"]
    # The cloud already holds different content under the second UUID.
    cloud.fake.insert("ingest_event", {"event_uuid": bad, "node_id": cloud.node,
                                       "owner_profile_id": cloud.analyst["profile_id"],
                                       "payload": {"different": True}, "traffic_id": 1,
                                       "prediction_id": 1, "alert_id": None, "audit_log_id": 1})
    identity = next(iter(cloud.app.session_interface.sessions.values())).identity
    cloud.services.sync.sync_once(identity)
    states = cloud.services.outbox.statuses(identity, [good, bad])
    assert states[good]["persistence"] == "synced"
    assert states[bad] == {"persistence": "rejected", "reason": "conflict"}
    exported = identity_client.get("/outbox/export").get_json()
    assert [row["event_uuid"] for row in exported["events"]] == [bad]


def test_expiry_without_refresh_stops_capture_and_requires_login(cloud):
    client = signed_in(cloud.app)
    identity = next(iter(cloud.app.session_interface.sessions.values())).identity
    post_json(client, "/monitor/start", {"source_type": "csv", "speed": "fast", "persist": "all"})
    monitor_until(client, lambda data: data["session"]["totals"]["flows"] >= 3)
    cloud.fake.offline = True  # refresh unavailable
    identity.expires_at = time.time() - 1
    wait_for(lambda: cloud.services.monitor.session["state"] == "stopped")
    assert "expired" in cloud.services.monitor.session["error_message"]
    cloud.fake.offline = False
    assert client.get("/").status_code == 302  # online login again
    assert not identity.active


def test_concurrent_refresh_spends_the_refresh_token_once(cloud):
    client = signed_in(cloud.app)
    identity = next(iter(cloud.app.session_interface.sessions.values())).identity
    cloud.services.sync.close()
    identity.expires_at = time.time() + 30  # inside the refresh margin
    with ThreadPoolExecutor(max_workers=8) as pool:
        codes = list(pool.map(lambda _: client.get("/monitor/status").status_code, range(16)))
    assert set(codes) == {200}
    assert cloud.fake.refresh_grants == 1
    assert identity.expires_at > time.time() + 600


def test_restart_with_pending_events_replays_only_under_the_original_user(
        tmp_path, stacking_artifact_bytes, fast_replay):
    env = make_cloud(tmp_path, stacking_artifact_bytes)
    try:
        env.fake.approve(env.other["profile_id"], env.services.node_id)
        client = signed_in(env.app)
        env.fake.offline = True
        queued = [post_json(client, "/predict", MANUAL_FLOW) for _ in range(3)]
        assert {response.status_code for response in queued} == {503}  # model check is online
        env.fake.offline = False
        identity = next(iter(env.app.session_interface.sessions.values())).identity
        env.services.sync.close()
        uuids = [post_json(client, "/predict", MANUAL_FLOW).get_json()["event_uuid"]
                 for _ in range(3)]
        assert env.services.outbox.counts(identity)["pending"] == 3
        env.services.close()  # process exit with durable pending events
        restarted = build_services(env.cfg)
        app = create_cloud_app(env.cfg, services=restarted, start_workers=False)
        restarted.sync.start()
        try:
            signed_in(app, OTHER)
            time.sleep(1.5)
            assert owned_events(env, OTHER[0]) == [] and owned_events(env, ANALYST[0]) == []
            signed_in(app, ANALYST)
            wait_for(lambda: len(owned_events(env, ANALYST[0])) == 3)
            assert sorted(row["event_uuid"] for row in owned_events(env, ANALYST[0])) == sorted(
                uuids)
        finally:
            restarted.close()
    finally:
        env.fake.stop()


def test_full_local_storage_stops_saving_but_classification_continues(
        tmp_path, stacking_artifact_bytes, fast_replay):
    env = make_cloud(tmp_path, stacking_artifact_bytes, start=False)
    try:
        env.services.outbox.close()
        tiny = cloud_outbox.Outbox(tmp_path / "tiny", total_bytes=3 * 1024 * 1024)
        env.services.outbox = env.services.sync.outbox = env.services.monitor.outbox = tiny
        tiny.start()
        env.services.monitor.outbox = tiny
        client = signed_in(env.app)
        post_json(client, "/monitor/start", {"source_type": "csv", "speed": "fast",
                                             "persist": "all"})
        full = monitor_until(client, lambda data: data["session"]["storage_stopped"],
                             timeout=40)
        flows_at_capacity = full["session"]["totals"]["flows"]
        # Verify progress after capacity is reached, without imposing a model
        # throughput requirement that varies across Windows hardware.
        data = monitor_until(client, lambda data: data["session"]["storage_stopped"]
                             and data["session"]["totals"]["flows"] >= flows_at_capacity + 5,
                             timeout=20)
        post_json(client, "/monitor/stop")
        assert data["session"]["state"] in ("running", "stopped")
        assert data["session"]["synchronization"]["dropped"] > 0
        assert sum(path.stat().st_size for path in (tmp_path / "tiny").iterdir()) <= (
            3 * 1024 * 1024)
    finally:
        env.services.close()
        env.fake.stop()


def test_revoked_membership_stops_capture_and_keeps_records_exportable(cloud):
    client = signed_in(cloud.app)
    cloud.services.sync.close()
    post_json(client, "/monitor/start", {"source_type": "csv", "speed": "fast", "persist": "all"})
    monitor_until(client, lambda data: data["session"]["totals"]["queued"] >= 5)
    cloud.fake.revoke(cloud.analyst["profile_id"], cloud.node)
    identity = next(iter(cloud.app.session_interface.sessions.values())).identity
    cloud.services.sync.last_authorization.clear()
    cloud.services.sync.sync_once(identity)
    assert cloud.services.sync.blocked[str(identity.user_id)] == "permission"
    wait_for(lambda: cloud.services.monitor.session["state"] == "stopped")
    assert "revoked" in cloud.services.monitor.session["error_message"]
    assert owned_events(cloud, ANALYST[0]) == []
    exported = client.get("/outbox/export")
    assert exported.status_code == 200
    body = json.loads(exported.get_data())
    assert body["events"] and all(row["status"] == "pending" for row in body["events"])
    assert "attachment" in exported.headers["Content-Disposition"]
    page = client.get("/").get_data(as_text=True)
    assert "Retry uploads" in page and "blocked" in page.lower()


def test_logout_stops_the_capture_and_account_switch_keeps_owner_records(cloud):
    client = signed_in(cloud.app)
    post_json(client, "/monitor/start", {"source_type": "csv", "speed": "fast", "persist": "all"})
    monitor_until(client, lambda data: data["session"]["totals"]["flows"] >= 3)
    switched = client.post("/login", data={  # same browser, different account
        "email": OTHER[0], "password": OTHER[1], "_csrf_token": client.csrf})
    assert switched.status_code == 302
    assert cloud.services.monitor.session["state"] == "stopped"
    assert "signed out" in cloud.services.monitor.session["error_message"]
    other_status = client.get("/monitor/status").get_json()["session"]
    assert other_status["state"] == "idle" and other_status["totals"]["flows"] == 0


# -- reports, filters, and administration -----------------------------------------

def test_alert_selection_creates_one_report_with_its_evidence(cloud):
    client = signed_in(cloud.app)
    uuids = []
    for row in attack_rows():
        result = post_json(client, "/predict", row).get_json()
        if result["prediction"] == "Attack":
            assert result["alert_expected"] is True
            uuids.append(result["event_uuid"])
        if len(uuids) == 2:
            break
    for event_uuid in uuids:
        wait_for(lambda event_uuid=event_uuid: client.get(
            f"/api/events/{event_uuid}").get_json().get("synced"))
    page = client.get("/alerts").get_data(as_text=True)
    alert_ids = re.findall(r'name="alert_id" value="(\d+)"', page)
    request_id = re.search(r'name="request_id" value="([^"]+)"', page).group(1)
    assert len(alert_ids) == 2
    form = {"_csrf_token": client.csrf, "request_id": request_id, "alert_id": alert_ids}
    created = client.post("/reports", data=form)
    assert created.status_code == 302
    detail = client.get(created.headers["Location"]).get_data(as_text=True)
    assert all(f"#{alert_id}" in detail for alert_id in alert_ids)
    replay = client.post("/reports", data=form, follow_redirects=True)
    assert "already saved" in replay.get_data(as_text=True)
    assert len(cloud.fake.rows("report")) == 1
    assert len(cloud.fake.rows("report_alert")) == 2
    data = client.get(created.headers["Location"].replace("/reports/", "/api/reports/"))
    assert data.get_json()["status"] == "success" and len(data.get_json()["alerts"]) == 2


def test_log_filters_and_audit_entries_are_scoped_and_idempotent(cloud):
    client = signed_in(cloud.app)
    wait_for(lambda: [row for row in cloud.fake.rows("system_log")
                      if row.get("action") == "login_successful"])
    page = client.get("/logs?module=Authentication").get_data(as_text=True)
    assert "login_successful" in page and "analyst signed in" in page
    empty = client.get("/logs?search=no-such-entry").get_data(as_text=True)
    assert "No matching logs" in empty
    bad = client.get("/logs?date_from=01/02/2026&run_id=abc").get_data(as_text=True)
    assert "YYYY-MM-DD" in bad and "positive whole number" in bad
    logins = [row for row in cloud.fake.rows("system_log")
              if row.get("action") == "login_successful"]
    assert len(logins) == 1 and logins[0]["event_uuid"]


def test_administrator_account_management_uses_the_checked_function(cloud):
    admin = signed_in(cloud.app, ADMIN)
    page = admin.get("/admins").get_data(as_text=True)
    request_id = re.search(r'name="request_id" value="([^"]+)"', page).group(1)
    weak = admin.post("/admins", data={
        "_csrf_token": admin.csrf, "request_id": request_id, "action": "create_account",
        "email": "new@example.test", "username": "new", "role": "analyst",
        "password": "short", "confirm_password": "short"})
    assert "12 to 128 characters" in weak.get_data(as_text=True)
    created = admin.post("/admins", data={
        "_csrf_token": admin.csrf, "request_id": request_id, "action": "create_account",
        "email": "new@example.test", "username": "new", "role": "analyst",
        "password": "New-Password-123!", "confirm_password": "New-Password-123!"})
    assert created.status_code == 302
    assert "new@example.test" in cloud.fake.users
    # A demoted administrator loses access immediately, despite an unexpired token.
    cloud.fake.tables["user_role"] = [row for row in cloud.fake.tables["user_role"]
                                      if row["profile_id"] != cloud.admin["profile_id"]]
    cloud.fake.insert("user_role", {"profile_id": cloud.admin["profile_id"], "role": "analyst"})
    assert admin.get("/admins").status_code == 302
    assert "Accounts</a>" not in admin.get("/").get_data(as_text=True)


# -- configuration ---------------------------------------------------------------

def test_cloud_mode_configuration_is_validated_and_isolated(tmp_path):
    import config

    good = {
        "ALGOGUARD_DB_MODE": "supabase",
        "NEXT_PUBLIC_SUPABASE_URL": "https://project.supabase.co/",
        "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY": "sb_publishable_abc",
        "ALGOGUARD_STATE_DIR": str(tmp_path / "state"),
    }
    loaded = config.load_config(good)
    assert loaded.db_mode == "supabase" and loaded.supabase_url == "https://project.supabase.co"
    assert loaded.state_dir == (tmp_path / "state").resolve()
    for override in (
        {"NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY": "sb_secret_abc"},
        {"NEXT_PUBLIC_SUPABASE_URL": "http://project.supabase.co"},
        {"NEXT_PUBLIC_SUPABASE_URL": "https://user:pw@project.supabase.co"},
        {"ALGOGUARD_HOST": "0.0.0.0"},
    ):
        with pytest.raises(config.ConfigError, match="supabase"):
            config.load_config({**good, **override})
    assert config.load_config({**good, "NEXT_PUBLIC_SUPABASE_URL": "http://127.0.0.1:54321"})


def test_app_entry_point_serves_the_cloud_app_without_touching_sqlite(tmp_path):
    import os
    import subprocess
    import sys

    database = tmp_path / "legacy.sqlite3"
    env = {
        **{key: value for key, value in os.environ.items() if not key.startswith("ALGOGUARD_")},
        "ALGOGUARD_DB_MODE": "supabase",
        "NEXT_PUBLIC_SUPABASE_URL": "http://127.0.0.1:9",
        "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY": "sb_publishable_test",
        "ALGOGUARD_DATABASE_PATH": str(database),
        "ALGOGUARD_STATE_DIR": str(tmp_path / "state"),
    }
    script = ("import app; print(app.app.config['CLOUD_MODE']); "
              "print(app.legacy_app.config['CLOUD_MODE'])")
    result = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True,
                            text=True, timeout=120, cwd=os.path.dirname(os.path.dirname(
                                os.path.abspath(__file__))))
    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.split() == ["True", "False"]
    assert not database.exists() and not database.parent.joinpath("database").exists()
    assert (tmp_path / "state" / "node-id").exists()
