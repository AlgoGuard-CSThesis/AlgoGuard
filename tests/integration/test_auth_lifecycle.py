"""Stage 6C.1 on the local stack: real Auth refresh, account switch, demotion, restart, secrets.

Each test signs in real users through the cloud app's login page. Maintenance SQL only creates
fixtures, changes roles and memberships the way an administrator would, and observes results.
The offline counterparts (signing-key rotation and revocation, sleep past expiry, a cloud that
refuses every token) are in ``tests/test_auth_lifecycle.py``.
"""

import json
import logging
import threading
import time
import uuid
from types import SimpleNamespace

import pytest

from cloud_app import _owner, build_services, create_cloud_app
from cloud_repository import CloudRepository
from tests.cloud_support import attack_rows, csrf, post_json, wait_for

from .app_fixtures import identity_for, make_account, sign_in, start_app

pytestmark = pytest.mark.integration


def refresh_tokens(cloud, account):
    """(issued, revoked) refresh tokens of this user in Supabase Auth."""
    return tuple(cloud["sql"](
        "select count(*),count(*) filter (where revoked) from auth.refresh_tokens "
        "where user_id=%s", (account.user_id,))[0])


def stored(cloud, event_ids):
    return cloud["sql"](
        "select event_uuid::text,owner_profile_id from public.ingest_event "
        "where event_uuid=any(%s::uuid[])", (list(event_ids),))


def test_expiry_during_upload_and_polling_refreshes_once(cloud, local_stack, local_http,
                                                         published, tmp_path):
    analyst = make_account(cloud, local_stack, local_http, "refresh")
    env = start_app(cloud, local_stack, tmp_path / "pc", [analyst], start_sync=False)
    try:
        client = sign_in(env.app, analyst)
        identity = identity_for(env.app, analyst)
        assert refresh_tokens(cloud, analyst) == (1, 0)
        events = [post_json(client, "/predict", row).get_json()["event_uuid"]
                  for row in attack_rows(3)]
        # Expiry during upload: inside the refresh margin, the worker renews first, then uploads.
        identity.expires_at = time.time() + 20
        first = identity.access_token
        env.services.sync.sync_once(identity)
        assert identity.access_token != first
        assert refresh_tokens(cloud, analyst) == (2, 1)  # the old refresh token was spent once
        assert {row[0] for row in stored(cloud, events)} == set(events)
        # Eight callers find the sign-in expired at once: one refresh, one token for all.
        identity.expires_at = time.time() - 1
        tokens, barrier = [], threading.Barrier(8)

        def caller():
            barrier.wait()
            tokens.append(env.services.auth.repository(identity).context.access_token)

        threads = [threading.Thread(target=caller) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert len(set(tokens)) == 1 and refresh_tokens(cloud, analyst) == (3, 2)
        # Expiry during polling: the monitor poll refreshes and the page stays attached.
        identity.expires_at = time.time() - 1
        status = client.get("/monitor/status")
        assert status.status_code == 200 and status.get_json()["status"] == "success"
        assert refresh_tokens(cloud, analyst) == (4, 3) and identity.active
    finally:
        env.services.close()


def test_logout_with_pending_events_and_account_switch_keep_each_users_records(
        cloud, local_stack, local_http, published, tmp_path):
    first, second = (make_account(cloud, local_stack, local_http, label)
                     for label in ("switch_a", "switch_b"))
    env = start_app(cloud, local_stack, tmp_path / "pc", [first, second], start_sync=False)
    try:
        client = sign_in(env.app, first)
        owner = _owner(identity_for(env.app, first))
        pending = [post_json(client, "/predict", row).get_json()["event_uuid"]
                   for row in attack_rows(2)]
        assert env.services.outbox.counts(owner)["pending"] == 2
        assert client.post("/logout", data={"_csrf_token": client.csrf}).status_code == 302
        # Another account signs in on the same computer and uploads its own records only.
        env.services.sync.start()
        other = sign_in(env.app, second)
        own = post_json(other, "/predict", attack_rows(3)[2]).get_json()["event_uuid"]
        wait_for(lambda: other.get(f"/api/events/{own}").get_json().get("synced"), timeout=30)
        time.sleep(3)  # several more upload passes
        assert stored(cloud, pending) == []
        exported = {row["event_uuid"] for row in other.get("/outbox/export").get_json()["events"]}
        assert not exported & set(pending)
        assert env.services.outbox.counts(owner)["pending"] == 2
        assert other.post("/logout", data={"_csrf_token": other.csrf}).status_code == 302
        # The original user signs in again: their records upload under their own profile.
        sign_in(env.app, first)
        wait_for(lambda: len(stored(cloud, pending)) == 2, timeout=30)
        assert {row[1] for row in stored(cloud, pending)} == {first.profile}
        assert stored(cloud, [own])[0][1] == second.profile
    finally:
        env.services.close()


def test_demotion_and_deactivation_take_effect_with_the_same_token(
        cloud, local_stack, local_http, published, tmp_path):
    admin = make_account(cloud, local_stack, local_http, "demoted", role="administrator")
    env = start_app(cloud, local_stack, tmp_path / "pc", [admin], start_sync=False)
    try:
        client = sign_in(env.app, admin)
        identity = identity_for(env.app, admin)
        assert client.get("/retention").status_code == 200
        assert client.get("/computers").status_code == 200
        cloud["sql"]("update public.user_role set role='analyst' where profile_id=%s",
                     (admin.profile,))
        # The token still says nothing about roles; the protected records decide.
        assert client.get("/retention").status_code == 302
        assert client.get("/computers").status_code == 302
        refused = cloud["rpc"]("retention_preview", {"p_operational_days": 30,
                                                     "p_report_days": 90},
                               {"token": identity.access_token})
        assert refused.status_code == 403
        event = post_json(client, "/predict", attack_rows(1)[0]).get_json()["event_uuid"]
        # Deactivation: the next renewal is refused and the sign-in ends; the record stays here.
        cloud["sql"]("update public.profile set is_active=false where profile_id=%s",
                     (admin.profile,))
        identity.expires_at = time.time() - 1
        response = client.get("/")
        assert response.status_code == 302 and response.headers["Location"].startswith("/login")
        assert not identity.active
        assert event in [row["event_uuid"]
                         for row in env.services.outbox.export_pending(_owner(identity))]
    finally:
        env.services.close()


def test_offline_cold_restart_requires_an_online_login_before_capture(
        cloud, local_stack, local_http, published, tmp_path):
    analyst = make_account(cloud, local_stack, local_http, "restart")
    state = tmp_path / "pc"
    env = start_app(cloud, local_stack, state, [analyst], start_sync=False)
    client = sign_in(env.app, analyst)
    env.services.models.resolve(env.services.auth.repository(identity_for(env.app, analyst)))
    event = post_json(client, "/predict", attack_rows(1)[0]).get_json()["event_uuid"]
    env.services.close()
    assert list((state / "models").glob("*.joblib")), "the verified model is cached"

    # Restart with the cloud unreachable: the cached model and pending event never allow an
    # offline login, and capture cannot start.
    cfg = SimpleNamespace(state_dir=state, supabase_url="http://127.0.0.1:9",
                          supabase_publishable_key=local_stack["publishable_key"],
                          secure_cookies=False)
    offline_services = build_services(cfg)
    offline = create_cloud_app(cfg, services=offline_services, start_workers=False)
    offline.config["TESTING"] = True
    try:
        browser = offline.test_client()
        refused = browser.post("/login", data={"email": analyst.email,
                                               "password": analyst.password,
                                               "_csrf_token": csrf(browser)})
        assert refused.status_code != 302
        sessions = getattr(offline.session_interface, "sessions")
        assert all(item.identity is None for item in sessions.values())
        start = browser.post("/monitor/start", json={"source_type": "csv"})
        assert start.status_code == 401
        assert browser.get("/").status_code == 302
    finally:
        offline_services.close()

    # Back online: login is required again, then the same user's pending record uploads.
    restarted = start_app(cloud, local_stack, state, [analyst])
    try:
        fresh = restarted.app.test_client()
        assert fresh.post("/monitor/start", json={"source_type": "csv"}).status_code == 401
        assert stored(cloud, [event]) == []
        sign_in(restarted.app, analyst)
        wait_for(lambda: stored(cloud, [event]), timeout=30)
    finally:
        restarted.services.close()


def test_secrets_stay_out_of_cookies_logs_urls_records_and_error_screens(
        cloud, local_stack, local_http, published, tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    analyst = make_account(cloud, local_stack, local_http, "secrets")
    env = start_app(cloud, local_stack, tmp_path / "pc", [analyst])
    urls = []
    original = CloudRepository._request

    def recording(self, method, path, *, params=None, body=None):
        urls.append(path + ("?" + json.dumps(params) if params else ""))
        return original(self, method, path, params=params, body=body)

    monkeypatch.setattr(CloudRepository, "_request", recording)
    responses = []
    try:
        client = env.app.test_client()
        responses.append(client.post("/login", data={
            "email": analyst.email, "password": analyst.password + "x",
            "_csrf_token": csrf(client)}))
        responses.append(client.post("/login", data={
            "email": analyst.email, "password": analyst.password,
            "_csrf_token": csrf(client)}))
        client.csrf = csrf(client, "/")
        identity = identity_for(env.app, analyst)
        tokens = {identity.access_token, identity.refresh_token}
        events = [post_json(client, "/predict", row) for row in attack_rows(2)]
        responses.extend(events)
        for path in ("/", "/alerts", "/alerts?node=all", f"/alerts?node={uuid.uuid4()}",
                     "/logs", "/reports", "/reports/999999999", "/nodes", "/monitor",
                     "/monitor/status", "/simulation", "/outbox/export",
                     f"/api/events/{uuid.uuid4()}", "/admins", "/retention"):
            responses.append(client.get(path))
        # A renewal while signed in, so a second generation of tokens is checked too.
        identity.expires_at = time.time() - 1
        responses.append(client.get("/"))
        tokens |= {identity.access_token, identity.refresh_token}
        ids = [response.get_json()["event_uuid"] for response in events]
        wait_for(lambda: len(stored(cloud, ids)) == 2, timeout=30)
        responses.append(client.post("/logout", data={"_csrf_token": client.csrf}))
        responses.append(client.get("/"))
    finally:
        env.services.close()
    secrets = {value for value in tokens | {analyst.password, local_stack["secret_key"]}
               if value}
    assert len(secrets) == 6

    exposed = []
    for response in responses:
        exposed.append(response.get_data(as_text=True))
        exposed.extend(f"{name}: {value}" for name, value in response.headers.items())
    exposed.extend(urls)
    exposed.extend(record.getMessage() for record in caplog.records)
    exposed.append(env.services.outbox.path.read_bytes().decode("latin-1"))
    for table, column, owner in (("system_log", "message", "profile_id"),
                                 ("network_traffic", "feature_payload", "owner_profile_id"),
                                 ("ingest_event", "payload::text", "owner_profile_id"),
                                 ("alert", "description", "owner_profile_id")):
        exposed.extend(str(row[0]) for row in cloud["sql"](
            f"select {column} from public.{table} where {owner}=%s", (analyst.profile,)))
    for text in exposed:
        for secret in secrets:
            assert secret not in text
    cookies = [value for response in responses
               for name, value in response.headers.items() if name == "Set-Cookie"]
    assert cookies
    assert [value for value in cookies
            if "HttpOnly" not in value or "SameSite=Lax" not in value] == []
