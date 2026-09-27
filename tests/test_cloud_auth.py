import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import pytest
from flask import Flask
from flask import session as flask_session

from cloud_auth import CloudAuth, CloudIdentity, MemorySession, MemorySessionInterface
from cloud_repository import RepositoryError

session = cast(MemorySession, flask_session)


def identity(**kwargs):
    return CloudIdentity(
        uuid4(),
        1,
        uuid4(),
        "user",
        ("analyst",),
        time.time() + 3600,
        "secret-access",
        "secret-refresh",
        **kwargs,
    )


def test_opaque_session_has_no_tokens_and_restart_requires_login():
    app = Flask(__name__)
    app.session_interface = MemorySessionInterface()

    @app.route("/")
    def index():
        session["_csrf_token"] = "csrf"
        session.identity = identity()
        return "okay"

    client = app.test_client()
    response = client.get("/")
    cookie = response.headers["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie
    assert "secret-" not in cookie and "csrf" not in cookie
    sid = next(iter(app.session_interface.sessions))
    assert cookie.startswith("session=" + sid + ";")
    assert "secret-" not in repr(app.session_interface.sessions[sid].identity)
    app.session_interface = MemorySessionInterface()
    with app.test_request_context(headers={"Cookie": "session=" + sid}):
        assert session.identity is None


def test_concurrent_refresh_happens_once_and_keeps_owner(monkeypatch):
    auth = CloudAuth("http://localhost:54321", "sb_publishable_test", uuid4())
    original = identity()
    original.expires_at = time.time() - 1
    calls = []

    def request(*args, **kwargs):
        calls.append(kwargs["body"])
        return {"access_token": "replacement", "refresh_token": "replacement-refresh"}

    monkeypatch.setattr(auth, "_transport", lambda *a: SimpleNamespace(_request=request))
    replacement = CloudIdentity(
        original.user_id,
        1,
        original.node_id,
        "user",
        ("analyst",),
        time.time() + 3600,
        "replacement",
        "replacement-refresh",
    )
    monkeypatch.setattr(auth, "authenticate_access", lambda *a: replacement)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: auth.repository(original), range(8)))
    assert len(calls) == 1
    assert original.access_token == "replacement"
    original.expires_at = time.time() - 1
    replacement.user_id = uuid4()
    with pytest.raises(RepositoryError, match="authentication"):
        auth.repository(original)
    assert original.access_token == "" and not original.active


def test_expired_session_stops_if_refresh_unavailable(monkeypatch):
    auth = CloudAuth("http://localhost:54321", "sb_publishable_test", uuid4())
    original = identity()
    original.expires_at = time.time() - 1

    def request(*a, **k):
        raise RepositoryError("transient")

    monkeypatch.setattr(auth, "_transport", lambda *a: SimpleNamespace(_request=request))
    with pytest.raises(RepositoryError, match="authentication"):
        auth.repository(original)
    assert original.refresh_token is None


@pytest.mark.parametrize("response", [None, [], "invalid"])
def test_malformed_auth_responses_are_controlled(monkeypatch, response):
    auth = CloudAuth("http://localhost:54321", "sb_publishable_test", uuid4())
    monkeypatch.setattr(
        auth, "_transport", lambda *args: SimpleNamespace(_request=lambda *a, **k: response)
    )
    with pytest.raises(RepositoryError) as login_error:
        auth.login("user@example.com", "password")
    assert login_error.value.category == "protocol"

    original = identity()
    original.expires_at = time.time() - 1
    with pytest.raises(RepositoryError) as refresh_error:
        auth.repository(original)
    assert refresh_error.value.category == "authentication"
    assert not original.active
    assert original.access_token == "" and original.refresh_token is None


def test_refresh_failure_backs_off_while_the_token_is_still_valid(monkeypatch):
    auth = CloudAuth("http://localhost:54321", "sb_publishable_test", uuid4())
    original = identity()
    original.expires_at = time.time() + 30  # inside the refresh margin
    calls = []

    def request(*args, **kwargs):
        calls.append(kwargs.get("body"))
        raise RepositoryError("transient")

    monkeypatch.setattr(auth, "_transport", lambda *a: SimpleNamespace(_request=request))
    for _ in range(5):
        auth.repository(original)
    assert len(calls) == 1 and original.active  # one attempt, then back off
    original.refresh_after = 0.0
    auth.repository(original)
    assert len(calls) == 2
