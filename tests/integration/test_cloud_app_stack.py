"""Stage 5D.5 end to end: the cloud Flask app against the real local stack.

A real Auth user signs in with email/password; the app downloads a model that
maintenance tooling published to private Storage; every page reads through the
Data API; a manual prediction and a CSV capture upload through the outbox; and
revocation and logout behave as the operating table requires. Maintenance SQL
is used only to create fixtures and to observe results, never for app access.
"""

import time
import uuid
from types import SimpleNamespace

import pytest
from stack_support import connect_local_database, require_status

from cloud_app import build_services, create_cloud_app
from publish_model import publish
from services import live_monitor_service
from tests.cloud_support import (
    attack_rows,
    csrf,
    monitor_until,
    post_json,
    train_stacking_artifact,
    wait_for,
)

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def published(cloud, local_stack, local_http, tmp_path_factory):
    import psycopg2

    folder = tmp_path_factory.mktemp("stack-artifact")
    path = folder / "stacking.joblib"
    path.write_bytes(train_stacking_artifact(folder))
    model = dict(
        model_name="Stacking Ensemble",
        version="stacking-five-v3",
        run_training_status="completed",
        evaluation_status="completed",
        accuracy=95.0,
        f1_score=95.0,
        roc_auc=95.0,
    )
    cloud["sql"]("NOTIFY pgrst, 'reload schema'")
    conn = connect_local_database(psycopg2.connect, local_stack["db_url"])
    try:
        manifest = publish(conn, local_stack["api_url"], local_stack["secret_key"], uuid.uuid4(),
                           model, path, cloud["users"][0]["profile"])
    finally:
        conn.close()
    assert manifest["status"] == "active"
    yield manifest
    response = local_http.delete(
        local_stack["api_url"] + "/storage/v1/object/models/" + manifest["object_path"],
        headers=cloud["maintenance"],
    )
    assert response.status_code in {200, 400, 404}


@pytest.fixture(scope="module")
def analyst(cloud, local_stack, local_http):
    email = f"{cloud['prefix']}_e2e@algoguard.invalid"
    password = "E2e!Aa1" + uuid.uuid4().hex
    created = local_http.post(
        local_stack["api_url"] + "/auth/v1/admin/users",
        headers=cloud["maintenance"],
        json={"email": email, "password": password, "email_confirm": True},
    )
    require_status(created, (200, 201), "end-to-end account")
    profile = cloud["sql"](
        "insert into public.profile(auth_user_id,username,email) values(%s,%s,%s) "
        "returning profile_id",
        (created.json()["id"], f"{cloud['prefix']}_e2e", email),
    )[0][0]
    cloud["sql"]("insert into public.user_role(profile_id,role) values(%s,'analyst')", (profile,))
    return SimpleNamespace(email=email, password=password, profile=profile)


@pytest.fixture
def stack_app(cloud, local_stack, analyst, published, tmp_path, monkeypatch):
    monkeypatch.setitem(live_monitor_service.SPEED_CHOICES, "fast", 0.001)
    cfg = SimpleNamespace(
        state_dir=tmp_path / "state",
        supabase_url=local_stack["api_url"],
        supabase_publishable_key=local_stack["publishable_key"],
        secure_cookies=False,
    )
    services = build_services(cfg)
    node = str(services.node_id)
    admin_profile = cloud["users"][0]["profile"]
    # The node row carries the fixture prefix so module cleanup removes it.
    cloud["sql"](
        "insert into public.node(node_id,display_name,status,approved_at,approved_by) "
        "values(%s,%s,'approved',now(),%s)",
        (node, cloud["prefix"], admin_profile),
    )
    cloud["sql"]("update public.node_membership set is_default=false where profile_id=%s",
                 (analyst.profile,))
    cloud["sql"](
        "insert into public.node_membership(node_id,profile_id,status,is_default,decided_at,"
        "decided_by) values(%s,%s,'approved',true,now(),%s)",
        (node, analyst.profile, admin_profile),
    )
    app = create_cloud_app(cfg, services=services, start_workers=False)
    app.config["TESTING"] = True
    services.sync.start()
    cloud["sql"]("update public.node_membership set status='approved' where profile_id=%s",
                 (analyst.profile,))
    yield SimpleNamespace(app=app, services=services, node=node)
    services.close()


def _sign_in(app, analyst):
    client = app.test_client()
    response = client.post("/login", data={
        "email": analyst.email, "password": analyst.password, "_csrf_token": csrf(client)})
    assert response.status_code == 302, response.get_data(as_text=True)[:1000]
    client.csrf = csrf(client, "/")
    return client


def test_cloud_app_end_to_end_on_the_local_stack(cloud, local_stack, local_http, analyst,
                                                 stack_app):
    sql = cloud["sql"]
    client = _sign_in(stack_app.app, analyst)
    dashboard = client.get("/").get_data(as_text=True)
    assert "Node approved" in dashboard and "Stacking Ensemble" in dashboard

    # Manual prediction: queued first, committed only after acknowledgement.
    attack = None
    for row in attack_rows():
        result = post_json(client, "/predict", row).get_json()
        assert result["status"] == "success" and result["prediction_id"] is None
        if result["prediction"] == "Attack":
            attack = result
            break
    assert attack is not None, "the published model classified no sample attack as Attack"
    synced = wait_for(lambda: (lambda data: data if data.get("synced") else None)(
        client.get(f"/api/events/{attack['event_uuid']}").get_json()), timeout=30)
    assert sql("select count(*) from public.ingest_event where event_uuid=%s and "
               "owner_profile_id=%s", (attack["event_uuid"], analyst.profile))[0][0] == 1
    alert_id = sql("select alert_id from public.ingest_event where event_uuid=%s",
                   (attack["event_uuid"],))[0][0]
    assert synced["alert_id"] == str(alert_id)

    # Alerts, reports, logs, nodes, and accounts pages through the Data API.
    page = client.get("/alerts").get_data(as_text=True)
    assert f'value="{alert_id}"' in page
    request_id = page.split('name="request_id" value="', 1)[1].split('"', 1)[0]
    created = client.post("/reports", data={"_csrf_token": client.csrf, "request_id": request_id,
                                            "alert_id": [str(alert_id)]})
    assert created.status_code == 302, created.get_data(as_text=True)[:1000]
    assert f"#{alert_id}" in client.get(created.headers["Location"]).get_data(as_text=True)
    assert sql("select count(*) from public.report where report_uuid=%s", (request_id,))[0][0] == 1
    wait_for(lambda: sql("select count(*) from public.system_log where profile_id=%s and "
                         "action='login_successful' and event_uuid is not null",
                         (analyst.profile,))[0][0] == 1, timeout=30)
    today = time.strftime("%Y-%m-%d", time.gmtime())
    for path, expected in (
        ("/logs?module=detection", "flow_accepted"),
        ("/logs?search=flow_accepted", "flow_accepted"),
        (f"/logs?date_from=2020-01-01&date_to={today}", "login_successful"),
        ("/logs?module=Authentication", "login_successful"),
    ):
        response = client.get(path)
        assert response.status_code == 200 and expected in response.get_data(as_text=True), path
    assert client.get("/nodes").status_code == 200
    assert client.get("/admins").status_code == 302  # analysts cannot administer
    assert client.get("/reports").status_code == 200
    assert client.get("/monitor").status_code == 200
    assert client.get("/simulation").status_code == 200
    sql("insert into public.user_role(profile_id,role) values(%s,'administrator')",
        (analyst.profile,))
    try:
        assert client.get("/admins").status_code == 200
    finally:
        sql("delete from public.user_role where profile_id=%s and role='administrator'",
            (analyst.profile,))

    # CSV capture: pinned deployment, nonblocking upload, terminal close reconciled.
    started = post_json(client, "/monitor/start", {"source_type": "csv", "speed": "fast",
                                                   "persist": "all"})
    assert started.status_code == 200, started.get_json()
    session_id = started.get_json()["session"]["session_id"]
    monitor_until(client, lambda data: data["session"]["totals"]["flows"] >= 8, timeout=60)
    stopped = post_json(client, "/monitor/stop").get_json()["session"]
    assert stopped["state"] == "stopped"
    queued = stopped["totals"]["queued"]
    capture = wait_for(lambda: sql(
        "select capture_id,status,closed_at,deployment_id,flows_emitted from "
        "public.capture_session where capture_uuid=%s and closed_at is not null",
        (session_id,)), timeout=30)[0]
    assert capture[1] == "stopped" and capture[3] == stack_app.app.extensions[
        "algoguard_cloud"].models.current.manifest["deployment_id"]
    wait_for(lambda: sql("select count(*) from public.ingest_event where capture_id=%s",
                         (capture[0],))[0][0] == queued, timeout=60)

    # Bearer API authentication is independent of the browser session.
    signed = local_http.post(
        local_stack["api_url"] + "/auth/v1/token",
        headers={"apikey": local_stack["publishable_key"]},
        params={"grant_type": "password"},
        json={"email": analyst.email, "password": analyst.password},
    )
    require_status(signed, (200,), "bearer login")
    bearer = {"Authorization": "Bearer " + signed.json()["access_token"]}
    api = stack_app.app.test_client()
    assert api.post("/predict", json=attack_rows(1)[0], headers=bearer).status_code == 200
    assert api.get("/monitor/status", headers=bearer).status_code == 200
    assert api.get("/", headers=bearer).status_code == 400

    # Revocation with an unexpired token: uploads stop, records stay exportable.
    sql("update public.node_membership set status='revoked',is_default=false where "
        "profile_id=%s", (analyst.profile,))
    services = stack_app.services
    services.sync.last_authorization.clear()
    pending = post_json(client, "/predict", attack_rows(2)[1]).get_json()
    assert pending["status"] == "success"
    wait_for(lambda: services.sync.status(
        next(iter(stack_app.app.session_interface.sessions.values())).identity)["state"]
        == "blocked", timeout=30)
    exported = client.get("/outbox/export").get_json()
    assert pending["event_uuid"] in [row["event_uuid"] for row in exported["events"]]
    refused = post_json(client, "/monitor/start", {"source_type": "csv", "speed": "fast"})
    assert refused.status_code == 200
    state = monitor_until(client, lambda data: data["session"]["state"] == "error", timeout=30)
    assert "approved membership" in state["session"]["error_message"]

    # Logout ends this installation's session only and requires online login again.
    response = client.post("/logout", data={"_csrf_token": client.csrf})
    assert response.status_code == 302
    assert client.get("/").status_code == 302
    still_valid = api.get("/monitor/status", headers=bearer)
    assert still_valid.status_code == 200  # scope=local left the separate bearer session


def test_pcap_capture_uses_real_cloud_persistence(cloud, analyst, stack_app):
    from pathlib import Path

    from config import get_config
    from tests.test_traffic_sources import write_sample_pcap

    folder = Path(get_config().capture_folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = write_sample_pcap(folder / "cloud-source-check.pcap", sessions=3)
    client = _sign_in(stack_app.app, analyst)
    response = post_json(client, "/monitor/start", {
        "source_type": "pcap", "capture_file": path.name, "speed": "fast", "persist": "all"})
    assert response.status_code == 200, response.get_json()
    terminal = monitor_until(client, lambda data: data["session"]["state"] in
                             ("completed", "error"), timeout=60)["session"]
    assert terminal["state"] == "completed", terminal.get("error_message")
    assert terminal["totals"]["flows"] == 4
    wait_for(lambda: cloud["sql"]("select count(*) from public.ingest_event "
                                 "where capture_id=%s", (terminal["capture_id"],))[0][0] == 4,
             timeout=30)
    rows = cloud["sql"]("select status,flows_emitted from public.capture_session "
                        "where capture_id=%s", (terminal["capture_id"],))
    wait_for(lambda: cloud["sql"]("select status from public.capture_session where capture_id=%s",
                                 (terminal["capture_id"],))[0][0] == "completed", timeout=30)
    assert rows


def test_windows_live_capture_uses_real_cloud_persistence(cloud, local_stack, analyst, stack_app):
    import os
    import urllib.request

    if os.environ.get("RUN_WINDOWS_CAPTURE_TESTS") != "1":
        pytest.skip("Set RUN_WINDOWS_CAPTURE_TESTS=1 on Windows with Npcap.")
    client = _sign_in(stack_app.app, analyst)
    response = post_json(client, "/monitor/start", {
        "source_type": "live", "interface": r"\Device\NPF_Loopback", "persist": "all"})
    assert response.status_code == 200, response.get_json()
    monitor_until(client, lambda data: data["session"]["state"] in ("running", "error"),
                  timeout=30)
    # Public, read-only traffic from a separate client, not the owned cloud opener.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for _ in range(3):
        with opener.open(local_stack["api_url"] + "/auth/v1/.well-known/jwks.json",
                         timeout=10) as result:
            result.read()
    observed = monitor_until(client, lambda data: data["session"]["totals"]["flows"] >= 1,
                             timeout=40)["session"]
    started = time.monotonic()
    stopped = post_json(client, "/monitor/stop").get_json()["session"]
    assert time.monotonic() - started < 6
    assert stopped["state"] == "stopped"
    wait_for(lambda: cloud["sql"]("select count(*) from public.ingest_event "
                                 "where capture_id=%s", (observed["capture_id"],))[0][0] >= 1,
             timeout=30)
    print("Windows live capture: real packet classified, cloud receipt verified, stop bounded.")
