"""Stage 6C.1: authentication and authorization lifecycle failures (offline fake service).

The real-stack counterparts are in ``tests/integration/test_auth_lifecycle.py``. These cover what
the local stack cannot do on demand: rotating and revoking the project's signing key, a computer
sleeping past its token's expiry, and a cloud that refuses every token.
"""

import logging
import time

from cloud_repository import CloudRepository
from tests.cloud_support import ADMIN, ANALYST, csrf, monitor_until, post_json, signed_in, wait_for
from tests.test_cloud_app import MANUAL_FLOW


def identity_of(cloud, client=None):
    sessions = list(cloud.app.session_interface.sessions.values())
    return next(item.identity for item in sessions if item.identity is not None)


def jwks_fetched_long_ago(cloud):
    """The key set is fetched again at most every 30 s; pretend that time has passed."""
    keys = cloud.services.auth.keys
    if keys._last_refresh is not None:
        keys._last_refresh -= 60


def test_a_rotated_signing_key_keeps_sessions_and_new_logins_verify(cloud):
    client = signed_in(cloud.app)
    identity = identity_of(cloud)
    old_token = identity.access_token
    cloud.fake.rotate_signing_key()
    # The old key still verifies until it is revoked: nothing changes for the open session.
    assert client.get("/").status_code == 200
    assert identity.access_token == old_token
    # A new sign-in carries the new key; the client fetches the key set once and verifies it.
    jwks_fetched_long_ago(cloud)
    other = signed_in(cloud.app, ADMIN)
    assert other.get("/").status_code == 200


def test_a_revoked_signing_key_renews_the_sign_in_and_uploads_resume(cloud):
    client = signed_in(cloud.app)
    identity = identity_of(cloud)
    old_token = identity.access_token
    cloud.services.sync.close()  # the page, not the upload worker, meets the refusal first
    cloud.fake.rotate_signing_key()
    cloud.fake.revoke_previous_keys()
    jwks_fetched_long_ago(cloud)
    # The cloud now refuses the token. The refresh token still works, so the page repeats
    # with a renewed sign-in instead of sending the user to the login page.
    response = client.get("/alerts")
    assert response.status_code == 302 and response.headers["Location"] == "/alerts"
    assert client.get("/alerts").status_code == 200
    assert identity.active and identity.access_token != old_token
    assert cloud.fake.refresh_grants == 1
    # Uploads continue under the same user with the renewed token.
    cloud.services.sync.start()
    event = post_json(client, "/predict", MANUAL_FLOW).get_json()["event_uuid"]
    wait_for(lambda: client.get(f"/api/events/{event}").get_json().get("synced"), timeout=20)


def test_the_upload_worker_renews_a_refused_token_without_ending_the_session(cloud):
    client = signed_in(cloud.app)
    identity = identity_of(cloud)
    cloud.services.sync.close()
    event = post_json(client, "/predict", MANUAL_FLOW).get_json()["event_uuid"]
    cloud.fake.rotate_signing_key()
    cloud.fake.revoke_previous_keys()
    jwks_fetched_long_ago(cloud)
    worker = cloud.services.sync
    worker.last_authorization.clear()
    worker.sync_once(identity)  # 401, then one renewal
    assert identity.active and str(identity.user_id) not in worker.blocked
    worker.sync_once(identity)
    assert cloud.services.outbox.statuses(identity, [event])[event]["persistence"] == "synced"
    assert client.get("/").status_code == 200  # the browser session is the same sign-in


def test_uploads_resume_by_themselves_once_the_installation_is_approved(cloud):
    """6D.3: found by the clean install. A new installation is refused until an administrator
    approves it; its uploads used to stay blocked after approval until Retry uploads."""
    import cloud_sync

    client = signed_in(cloud.app)
    identity = identity_of(cloud)
    owner = str(identity.user_id)
    worker = cloud.services.sync
    worker.close()
    event = post_json(client, "/predict", MANUAL_FLOW).get_json()["event_uuid"]
    cloud.fake.revoke(cloud.analyst["profile_id"], cloud.node)  # not approved (any more)
    worker.last_authorization.clear()
    worker.sync_once(identity)
    assert worker.blocked[owner] == "permission"
    worker.permission_checked[owner] -= cloud_sync.PERMISSION_RECHECK_SECONDS
    worker.sync_once(identity)  # rechecked: still not approved, so still blocked
    assert worker.blocked[owner] == "permission"
    cloud.fake.approve(cloud.analyst["profile_id"], cloud.node)
    worker.sync_once(identity)  # checked moments ago: waits for the next recheck
    assert owner in worker.blocked
    worker.permission_checked[owner] -= cloud_sync.PERMISSION_RECHECK_SECONDS
    worker.sync_once(identity)
    assert owner not in worker.blocked
    assert cloud.services.outbox.statuses(identity, [event])[event]["persistence"] == "synced"


def test_a_cloud_that_refuses_the_renewed_token_too_ends_the_sign_in(cloud):
    client = signed_in(cloud.app)
    identity = identity_of(cloud)
    cloud.services.sync.close()
    event = post_json(client, "/predict", MANUAL_FLOW).get_json()["event_uuid"]
    cloud.fake.rotate_signing_key()
    cloud.fake.revoke_previous_keys()
    jwks_fetched_long_ago(cloud)
    first = client.get("/alerts")
    assert first.status_code == 302 and first.headers["Location"] == "/alerts"
    # The renewed token is refused as well, within seconds of the renewal.
    cloud.fake.rotate_signing_key()
    cloud.fake.revoke_previous_keys()
    second = client.get("/alerts")
    assert second.status_code == 302 and second.headers["Location"].startswith("/login")
    assert not identity.active
    assert cloud.fake.refresh_grants == 1  # one renewal, no loop
    # The user's event stays on this computer for their next online login.
    assert cloud.services.outbox.counts(identity)["pending"] == 1
    assert event in [row["event_uuid"] for row in cloud.services.outbox.export_pending(identity)]


def test_renewal_waits_out_a_cloud_outage_without_signing_out(cloud):
    client = signed_in(cloud.app)
    identity = identity_of(cloud)
    cloud.fake.fail("GET", "/rest/v1/", status=401, times=1)
    cloud.fake.fail("POST", "/auth/v1/token", status=503, times=1)
    response = client.get("/alerts")
    assert response.status_code == 503
    assert identity.active  # the cloud could not answer; the sign-in stays
    assert client.get("/alerts").status_code == 200


def test_a_computer_that_slept_past_expiry_renews_and_keeps_its_capture(cloud):
    client = signed_in(cloud.app)
    identity = identity_of(cloud)
    started = post_json(client, "/monitor/start",
                        {"source_type": "csv", "speed": "fast", "persist": "none"})
    assert started.status_code == 200
    monitor_until(client, lambda data: data["session"]["state"] == "running")
    post_json(client, "/monitor/pause")
    # Asleep past the token's expiry; the refresh token still works when it wakes.
    identity.expires_at = time.time() - 5
    wait_for(lambda: cloud.fake.refresh_grants == 1, timeout=10)
    time.sleep(0.5)
    state = client.get("/monitor/status").get_json()["session"]
    assert state["state"] == "paused" and not state["error_message"]
    assert identity.active and identity.expires_at > time.time()
    post_json(client, "/monitor/stop")


def test_demotion_takes_effect_on_the_next_administrator_page(cloud):
    client = signed_in(cloud.app, ADMIN)
    assert client.get("/retention").status_code == 200
    with cloud.fake.lock:
        cloud.fake.tables["user_role"] = [
            row for row in cloud.fake.tables["user_role"]
            if not (row["profile_id"] == cloud.admin["profile_id"]
                    and row["role"] == "administrator")]
    # The same token: roles come from the protected records, never from the token.
    response = client.get("/retention")
    assert response.status_code == 302 and response.headers["Location"] == "/"
    assert client.get("/computers").status_code == 302
    assert identity_of(cloud).display_role == "Analyst"


def test_secrets_never_reach_cookies_logs_urls_or_error_screens(cloud, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    urls = []
    original = CloudRepository._request

    def recording(self, method, path, *, params=None, body=None):
        urls.append(path + ("?" + "&".join(f"{k}={v}" for k, v in params.items())
                            if params else ""))
        return original(self, method, path, params=params, body=body)

    monkeypatch.setattr(CloudRepository, "_request", recording)
    responses = []
    client = cloud.app.test_client()
    token = csrf(client)
    responses.append(client.post("/login", data={"email": ANALYST[0], "password": "wrong-one",
                                                 "_csrf_token": token}))
    responses.append(client.post("/login", data={"email": ANALYST[0], "password": ANALYST[1],
                                                 "_csrf_token": csrf(client)}))
    client.csrf = csrf(client, "/")
    identity = identity_of(cloud)
    tokens = {identity.access_token, identity.refresh_token}
    for path in ("/", "/alerts", "/logs", "/reports", "/nodes", "/monitor", "/simulation",
                 "/monitor/status", "/outbox/export", "/no-such-page"):
        responses.append(client.get(path))
    responses.append(post_json(client, "/predict", MANUAL_FLOW))
    # Error screens: the cloud unavailable, then a refused token renewed once.
    cloud.fake.offline = True
    responses.append(client.get("/alerts"))
    responses.append(client.get("/monitor/status"))
    cloud.fake.offline = False
    cloud.fake.fail("GET", "/rest/v1/", status=401, times=1)
    responses.append(client.get("/alerts"))
    tokens |= {identity.access_token, identity.refresh_token}
    responses.append(client.post("/logout", data={"_csrf_token": client.csrf}))
    responses.append(client.get("/"))
    secrets = {value for value in tokens | {ANALYST[1]} if value}
    assert len(secrets) >= 5  # the password and two generations of access/refresh tokens

    exposed = []
    for response in responses:
        exposed.append(response.get_data(as_text=True))
        exposed.extend(f"{name}: {value}" for name, value in response.headers.items())
    exposed.extend(urls)
    exposed.extend(record.getMessage() for record in caplog.records)
    cloud.services.outbox.drain()
    exposed.append(cloud.services.outbox.path.read_bytes().decode("latin-1"))
    exposed.extend(str(row) for row in cloud.fake.rows("system_log"))
    for text in exposed:
        for secret in secrets:
            assert secret not in text
    cookies = [value for response in responses
               for name, value in response.headers.items() if name == "Set-Cookie"]
    assert cookies
    assert [value for value in cookies
            if "HttpOnly" not in value or "SameSite=Lax" not in value] == []
