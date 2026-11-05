"""Offline tests for the maintainer password reset (no network: the Auth calls take a session)."""

import io
from types import SimpleNamespace

import pytest

import reset_password
from bootstrap_admin import BootstrapError
from reset_password import reset, set_password

API = "https://project.supabase.co"
USER_ID = "8f1c2a4e-5b6d-4c3e-9a1f-2b3c4d5e6f70"


class FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, users, put_status=200):
        self.users = users
        self.put_status = put_status
        self.requests = []
        self.closed = False

    def get(self, url, params=None, timeout=None):
        self.requests.append(("GET", url, params))
        page = (params or {}).get("page", 1)
        return FakeResponse(200, {"users": self.users if page == 1 else []})

    def put(self, url, json=None, timeout=None):
        self.requests.append(("PUT", url, json))
        return FakeResponse(self.put_status)

    def close(self):
        self.closed = True


def arguments(**values):
    return SimpleNamespace(**{"email": "Analyst@Example.com", "api_url": API,
                              "password_stdin": False, **values})


def test_the_new_password_goes_to_the_account_with_that_email():
    session = FakeSession([{"id": "a9a9a9a9-0000-4000-8000-000000000000",
                            "email": "someone@else.test"},
                           {"id": USER_ID, "email": "analyst@example.com"}])
    assert set_password(session, API, "analyst@example.com", "New-Passw0rd!x") == USER_ID
    assert session.requests[-1] == ("PUT", f"{API}/auth/v1/admin/users/{USER_ID}",
                                    {"password": "New-Passw0rd!x"})


def test_an_unknown_email_changes_nothing():
    session = FakeSession([{"id": USER_ID, "email": "someone@else.test"}])
    with pytest.raises(BootstrapError, match="No account uses"):
        set_password(session, API, "analyst@example.com", "New-Passw0rd!x")
    assert not [request for request in session.requests if request[0] == "PUT"]


def test_a_refused_change_is_reported_with_a_hint():
    session = FakeSession([{"id": USER_ID, "email": "analyst@example.com"}], put_status=403)
    with pytest.raises(BootstrapError, match="SUPABASE_SECRET_KEY"):
        set_password(session, API, "analyst@example.com", "New-Passw0rd!x")


def test_a_user_id_that_is_not_a_uuid_never_reaches_the_url():
    session = FakeSession([{"id": "../../settings", "email": "analyst@example.com"}])
    with pytest.raises(BootstrapError, match="invalid user id"):
        set_password(session, API, "analyst@example.com", "New-Passw0rd!x")
    assert not [request for request in session.requests if request[0] == "PUT"]


def test_a_generated_password_is_returned_once_and_meets_the_account_rule(monkeypatch):
    session = FakeSession([{"id": USER_ID, "email": "analyst@example.com"}])
    monkeypatch.setattr(reset_password, "_session", lambda key: session)
    generated = reset(arguments(), {"SUPABASE_SECRET_KEY": "sb_secret_test"})
    assert generated and reset_password.password_is_strong(generated)
    assert session.requests[-1][2] == {"password": generated}
    assert session.closed


def test_a_supplied_password_must_meet_the_account_rule(monkeypatch):
    session = FakeSession([{"id": USER_ID, "email": "analyst@example.com"}])
    monkeypatch.setattr(reset_password, "_session", lambda key: session)
    environment = {"SUPABASE_SECRET_KEY": "sb_secret_test"}
    with pytest.raises(BootstrapError, match="12 to 128 characters"):
        reset(arguments(password_stdin=True), environment, io.StringIO("short\n"))
    assert reset(arguments(password_stdin=True), environment,
                 io.StringIO("Chosen-Passw0rd!\r\n")) is None
    assert session.requests[-1][2] == {"password": "Chosen-Passw0rd!"}


def test_the_secret_key_is_required():
    with pytest.raises(BootstrapError, match="SUPABASE_SECRET_KEY is not set"):
        reset(arguments(), {})


def test_maintainer_only_libraries_are_not_imported_at_module_level():
    assert not hasattr(reset_password, "requests")
    assert not hasattr(reset_password, "psycopg2")
