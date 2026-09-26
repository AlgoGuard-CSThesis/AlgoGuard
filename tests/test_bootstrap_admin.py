"""Offline tests for the first-Administrator bootstrap.

No network and no database: the Auth calls take a session object and the
database call takes a connection, so both halves are exercised with
stand-ins. What matters here is the recovery behaviour — a run that died
between creating the Auth user and creating the profile must be fixable
by running it again, without producing a second account.
"""

import pytest

import bootstrap_admin
from bootstrap_admin import (
    BootstrapError,
    api_url_from_jwks_url,
    create_auth_user,
    default_username,
    find_auth_user,
    generate_password,
    normalise_email,
    resolve_api_url,
    upsert_profile_and_role,
)

API = "https://project.supabase.co"


class FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


class FakeSession:
    """Serves users a page at a time, and records what was asked for."""

    def __init__(self, pages=None, post_response=None):
        self.pages = pages or [[]]
        self.post_response = post_response
        self.requests = []

    def get(self, url, params=None, timeout=None):
        self.requests.append(("GET", url, params))
        page = (params or {}).get("page", 1)
        users = self.pages[page - 1] if page - 1 < len(self.pages) else []
        return FakeResponse(200, {"users": users})

    def post(self, url, json=None, timeout=None):
        self.requests.append(("POST", url, json))
        return self.post_response


class FakeCursor:
    def __init__(self, results):
        self.results = list(results)
        self.executed = []
        self.rowcount = 0
        self._current = None

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), params))
        self._current = self.results.pop(0) if self.results else None
        self.rowcount = 1 if self._current is not None else 0

    def fetchone(self):
        return self._current

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConnection:
    def __init__(self, results):
        self.cursor_object = FakeCursor(results)
        self.committed = False

    def cursor(self):
        return self.cursor_object

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.committed = exc[0] is None
        return False


# ---------------------------------------------------------------------
# Passwords and identifiers
# ---------------------------------------------------------------------


def test_generated_passwords_are_long_and_never_repeat():
    passwords = {generate_password() for _ in range(50)}
    assert len(passwords) == 50
    assert all(len(p) >= 24 for p in passwords)
    assert all(
        any(c.islower() for c in p)
        and any(c.isupper() for c in p)
        and any(c.isdigit() for c in p)
        and any(not c.isalnum() for c in p)
        for p in passwords
    )


def test_a_short_password_is_refused_rather_than_padded():
    with pytest.raises(BootstrapError):
        generate_password(8)


def test_emails_are_normalised_and_nonsense_is_refused():
    assert normalise_email("  Admin@Example.COM ") == "admin@example.com"
    assert default_username("Admin@Example.com") == "admin"
    for bad in ("", "not-an-email", "@example.com", "admin@"):
        with pytest.raises(BootstrapError):
            normalise_email(bad)


# ---------------------------------------------------------------------
# Where to send the admin calls
# ---------------------------------------------------------------------


def test_the_api_url_falls_back_to_the_jwks_url_already_configured():
    jwks = "https://project.supabase.co/auth/v1/.well-known/jwks.json"
    assert api_url_from_jwks_url(jwks) == API
    assert resolve_api_url(None, {"SUPABASE_JWKS_URL": jwks}) == API


def test_an_explicit_api_url_wins_and_is_validated():
    assert resolve_api_url("https://other.supabase.co/", {}) == "https://other.supabase.co"
    assert resolve_api_url(None, {"SUPABASE_API_URL": API}) == API
    with pytest.raises(BootstrapError):
        resolve_api_url("project.supabase.co", {})
    with pytest.raises(BootstrapError):
        resolve_api_url(None, {})


# ---------------------------------------------------------------------
# Finding an existing account
# ---------------------------------------------------------------------


def test_an_existing_user_is_found_on_a_later_page_and_case_insensitively():
    session = FakeSession(
        pages=[
            [{"id": "a", "email": "someone@else.test"}],
            [{"id": "b", "email": "Admin@Example.com"}],
        ]
    )
    found = find_auth_user(session, API, "admin@example.com")
    assert found["id"] == "b"


def test_an_absent_user_returns_none_without_looping_forever():
    session = FakeSession(pages=[[{"id": "a", "email": "someone@else.test"}], []])
    assert find_auth_user(session, API, "admin@example.com") is None
    assert len(session.requests) == 2


def test_a_failed_listing_is_an_error_not_an_empty_result():
    class Broken(FakeSession):
        def get(self, url, params=None, timeout=None):
            return FakeResponse(401)

    with pytest.raises(BootstrapError, match="Listing users failed"):
        find_auth_user(Broken(), API, "admin@example.com")


# ---------------------------------------------------------------------
# Creating it, and recovering from a half-finished run
# ---------------------------------------------------------------------


def test_creating_the_auth_user_returns_the_new_record():
    session = FakeSession(post_response=FakeResponse(200, {"id": "new-user"}))
    user = create_auth_user(session, API, "admin@example.com", "x" * 24)
    assert user["id"] == "new-user"


def test_a_duplicate_email_resolves_to_the_existing_user_not_an_error():
    """This is the recovery path: the Auth half succeeded on an earlier
    run and the database half did not."""
    session = FakeSession(
        pages=[[{"id": "existing-user", "email": "admin@example.com"}]],
        post_response=FakeResponse(422, {"msg": "email exists"}),
    )
    user = create_auth_user(session, API, "admin@example.com", "x" * 24)
    assert user["id"] == "existing-user"


def test_an_unexpected_status_is_reported_with_a_hint_not_swallowed():
    session = FakeSession(post_response=FakeResponse(403, {}))
    with pytest.raises(BootstrapError, match="SUPABASE_SECRET_KEY"):
        create_auth_user(session, API, "admin@example.com", "x" * 24)


def test_no_password_is_ever_sent_to_an_account_that_already_exists():
    session = FakeSession(
        pages=[[{"id": "existing-user", "email": "admin@example.com"}]],
        post_response=FakeResponse(422, {}),
    )
    create_auth_user(session, API, "admin@example.com", "hunter2hunter2hunter2")
    posts = [r for r in session.requests if r[0] == "POST"]
    assert len(posts) == 1, "one attempt, then a lookup — never a second create"


# ---------------------------------------------------------------------
# The database half
# ---------------------------------------------------------------------


def test_an_existing_profile_is_reused_and_the_role_is_still_ensured():
    connection = FakeConnection(results=[(7, "admin"), None])
    profile_id, profile_created, role_granted = upsert_profile_and_role(
        connection, "auth-uuid", "admin@example.com", "admin"
    )
    statements = [sql for sql, _ in connection.cursor_object.executed]

    assert (profile_id, profile_created) == (7, False)
    assert role_granted is False
    assert not any(statement.startswith("insert into public.profile") for statement in statements)
    assert any("on conflict (profile_id, role) do nothing" in s for s in statements)


def test_a_missing_profile_is_created_and_the_role_granted():
    connection = FakeConnection(results=[None, (9,), (None,)])
    profile_id, profile_created, role_granted = upsert_profile_and_role(
        connection, "auth-uuid", "admin@example.com", "admin"
    )
    assert (profile_id, profile_created, role_granted) == (9, True, True)


def test_the_role_insert_leaves_granted_by_null_for_the_maintenance_path():
    """The 5B.2 trigger treats granted_by IS NULL as trusted maintenance;
    anything else must name a current Administrator."""
    connection = FakeConnection(results=[None, (9,), (None,)])
    upsert_profile_and_role(connection, "auth-uuid", "admin@example.com", "admin")
    role_insert = [s for s, _ in connection.cursor_object.executed if "user_role" in s][0]
    assert "granted_by" not in role_insert


# ---------------------------------------------------------------------
# Dependency hygiene
# ---------------------------------------------------------------------


def test_maintainer_only_libraries_are_not_imported_at_module_level():
    """`pytest -q` on an analyst clone imports this module during
    collection, where neither library is installed."""
    assert not hasattr(bootstrap_admin, "requests")
    assert not hasattr(bootstrap_admin, "psycopg2")
