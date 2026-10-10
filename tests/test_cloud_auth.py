import json
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from flask import Flask
from flask import session as flask_session
from jwt.algorithms import ECAlgorithm

from cloud_auth import CloudAuth, CloudIdentity, MemorySession, MemorySessionInterface
from cloud_repository import RepositoryError
from token_verification import JwksCache, issuer_for

session = cast(MemorySession, flask_session)

PROJECT_URL = "http://localhost:54321"


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


def signed_auth(monkeypatch):
    """A CloudAuth that verifies real signatures and finds one active analyst."""
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_jwk = json.loads(ECAlgorithm.to_jwk(private_key.public_key()))
    public_jwk.update({"kid": "ec-key-1", "alg": "ES256", "use": "sig"})
    keys = JwksCache(PROJECT_URL, fetcher=lambda *a: {"keys": [public_jwk]})
    auth = CloudAuth(PROJECT_URL, "sb_publishable_test", uuid4(), jwks=keys)
    profile = {"profile_id": 1, "username": "user", "is_active": True}
    repo = SimpleNamespace(
        _request=lambda *a, **k: [profile],
        list_records=lambda *a, **k: SimpleNamespace(rows=[{"role": "analyst"}]),
    )
    monkeypatch.setattr(auth, "_transport", lambda *a: repo)

    def mint(*, issued_at, expires_at):
        claims = {
            "sub": str(uuid4()),
            "aud": "authenticated",
            "iss": issuer_for(PROJECT_URL),
            "iat": int(issued_at),
            "exp": int(expires_at),
        }
        return jwt.encode(claims, private_key, algorithm="ES256", headers={"kid": "ec-key-1"})

    return auth, mint


def test_sign_in_survives_a_computer_clock_a_few_seconds_slow(monkeypatch):
    # Stage 5F pilot: PC A ran 2-7 s behind Supabase, so every fresh token's
    # issue time looked like the future and correct passwords were refused.
    auth, mint = signed_auth(monkeypatch)
    now = time.time()
    token = mint(issued_at=now + 7, expires_at=now + 3607)
    signed_in = auth.authenticate_access(token, "refresh")
    assert signed_in.username == "user" and signed_in.active


def test_clock_tolerance_is_bounded(monkeypatch):
    auth, mint = signed_auth(monkeypatch)
    now = time.time()
    with pytest.raises(RepositoryError, match="authentication"):
        auth.authenticate_access(mint(issued_at=now + 60, expires_at=now + 3660))


def test_clock_tolerance_never_extends_an_expired_token(monkeypatch):
    # Verification tolerates skew, but the identity keeps the real expiry,
    # and every request is checked against it without tolerance.
    auth, mint = signed_auth(monkeypatch)
    now = time.time()
    bearer = auth.authenticate_access(mint(issued_at=now - 3605, expires_at=now - 5))
    with pytest.raises(RepositoryError, match="authentication"):
        auth.repository(bearer)
    assert not bearer.active
