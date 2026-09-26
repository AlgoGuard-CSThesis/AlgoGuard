"""
tests/integration/test_auth_configuration.py

Stage 5B.2 evidence, against the local Supabase stack:

  * the public sign-up endpoints refuse everyone;
  * tokens this stack issues verify with the real verifier, against the
    project's published public keys;
  * authorization follows the protected role record, not the token — a
    token claiming `administrator` is worth nothing without the row.

Requires:
    - Docker + `supabase start`, with `supabase db reset` already applied
    - `supabase status -o env > .env.supabase.local`
    - python -m pip install -r requirements-maintainer.txt
    - PyJWT[crypto] from requirements.txt

Run with:
    python -m pytest -m integration -v
"""

import uuid
from types import SimpleNamespace

import pytest
from stack_support import connect_local_database, require_status

psycopg2 = pytest.importorskip(
    "psycopg2",
    reason="integration lane needs: python -m pip install -r requirements-maintainer.txt",
)
pytest.importorskip("jwt", reason="PyJWT[crypto] is required: pip install -r requirements.txt")

from token_verification import (  # noqa: E402
    JwksCache,
    TokenRejected,
    issuer_for,
    jwks_url_for,
    verify_token,
)

pytestmark = pytest.mark.integration

DISPOSABLE_DOMAIN = "algoguard.invalid"


def disposable_email():
    return f"algoguard.test+{uuid.uuid4().hex[:8]}@{DISPOSABLE_DOMAIN}"


@pytest.fixture()
def admin_headers(local_stack):
    return {
        "apikey": local_stack["secret_key"],
        "Authorization": f"Bearer {local_stack['secret_key']}",
        "Content-Type": "application/json",
    }


@pytest.fixture()
def disposable_user(local_stack, local_http, admin_headers):
    """An Auth user that exists only for one test, with a signed-in token."""
    email = disposable_email()
    password = "Integration!" + uuid.uuid4().hex[:12]

    created = local_http.post(
        f"{local_stack['api_url']}/auth/v1/admin/users",
        json={"email": email, "password": password, "email_confirm": True},
        headers=admin_headers,
        timeout=10,
    )
    require_status(created, (200, 201), "Auth admin user creation")
    user_id = created.json()["id"]

    try:
        signed_in = local_http.post(
            f"{local_stack['api_url']}/auth/v1/token",
            params={"grant_type": "password"},
            json={"email": email, "password": password},
            headers={"apikey": local_stack["publishable_key"], "Content-Type": "application/json"},
            timeout=10,
        )
        require_status(signed_in, (200,), "Auth password sign-in")
        yield {"id": user_id, "email": email, "access_token": signed_in.json()["access_token"]}
    finally:
        local_http.delete(
            f"{local_stack['api_url']}/auth/v1/admin/users/{user_id}",
            headers={k: v for k, v in admin_headers.items() if k.lower() != "content-type"},
            timeout=10,
        )


def published_keys(local_stack, local_http):
    response = local_http.get(
        f"{local_stack['api_url']}/auth/v1/.well-known/jwks.json",
        headers={"apikey": local_stack["publishable_key"]},
        timeout=10,
    )
    if response.status_code != 200:
        return []
    return response.json().get("keys", [])


# ---------------------------------------------------------------------
# Nobody may sign themselves up
# ---------------------------------------------------------------------


def test_public_signup_is_refused(local_stack, local_http):
    response = local_http.post(
        f"{local_stack['api_url']}/auth/v1/signup",
        json={"email": disposable_email(), "password": "Whatever!" + uuid.uuid4().hex[:12]},
        headers={"apikey": local_stack["publishable_key"], "Content-Type": "application/json"},
        timeout=10,
    )
    assert response.status_code not in (200, 201), (
        "Public sign-up succeeded. Check [auth].enable_signup and "
        "[auth.email].enable_signup in supabase/config.toml, then restart the stack."
    )
    assert response.status_code in (400, 401, 403, 422), (
        f"Unexpected sign-up status {response.status_code}."
    )


def test_bootstrap_generated_password_and_idempotence(local_stack, local_http, monkeypatch):
    from bootstrap_admin import bootstrap

    email = disposable_email()
    args = SimpleNamespace(
        email=email,
        username="zz_migration_smoke_" + uuid.uuid4().hex[:10],
        api_url=local_stack["api_url"],
        password_stdin=False,
    )
    monkeypatch.setenv("DATABASE_URL", local_stack["db_url"])
    outcome = None
    try:
        outcome = bootstrap(args, {"SUPABASE_SECRET_KEY": local_stack["secret_key"]})
        assert outcome.password and outcome.role_granted
        signed = local_http.post(
            local_stack["api_url"] + "/auth/v1/token",
            params={"grant_type": "password"},
            headers={"apikey": local_stack["publishable_key"]},
            json={"email": email, "password": outcome.password},
        )
        require_status(signed, (200,), "generated-password sign-in")
        retried = bootstrap(args, {"SUPABASE_SECRET_KEY": local_stack["secret_key"]})
        assert retried.profile_id == outcome.profile_id and retried.password is None
        assert (
            not retried.auth_user_created
            and not retried.profile_created
            and not retried.role_granted
        )
    finally:
        if outcome:
            connection = connect_local_database(psycopg2.connect, local_stack["db_url"])
            try:
                with connection:
                    with connection.cursor() as cursor:
                        cursor.execute(
                            "delete from public.user_role where profile_id=%s",
                            (outcome.profile_id,),
                        )
                        cursor.execute(
                            "delete from public.profile where profile_id=%s", (outcome.profile_id,)
                        )
            finally:
                connection.close()
            local_http.delete(
                local_stack["api_url"] + "/auth/v1/admin/users/" + outcome.auth_user_id,
                headers={
                    "apikey": local_stack["secret_key"],
                    "Authorization": "Bearer " + local_stack["secret_key"],
                },
            )


def test_anonymous_sign_in_is_refused(local_stack, local_http):
    response = local_http.post(
        f"{local_stack['api_url']}/auth/v1/signup",
        json={},
        headers={"apikey": local_stack["publishable_key"], "Content-Type": "application/json"},
        timeout=10,
    )
    assert response.status_code not in (200, 201)


def test_an_administrator_can_still_be_created_through_the_admin_api(disposable_user):
    """The path that replaces self-service signup has to work, or the
    previous two tests have only proved the product is unusable."""
    assert disposable_user["id"]
    assert disposable_user["access_token"]


# ---------------------------------------------------------------------
# Tokens: verified locally, against public keys
# ---------------------------------------------------------------------


def test_the_stack_publishes_asymmetric_signing_keys(local_stack, local_http):
    keys = published_keys(local_stack, local_http)
    if not keys:
        pytest.skip(
            "This stack still signs with the legacy shared secret. To switch it: "
            "supabase gen signing-key --algorithm ES256 > supabase/signing_keys.json "
            "(in cmd.exe), "
            "uncomment signing_keys_path in supabase/config.toml, restart the stack."
        )
    assert all(key.get("kty") != "oct" for key in keys), (
        "A symmetric key is published in the JWKS: every holder could mint tokens."
    )


def test_a_real_token_from_this_stack_verifies(local_stack, local_http, disposable_user):
    if not published_keys(local_stack, local_http):
        pytest.skip("Stack is on the legacy shared secret; see the previous test's message.")

    cache = JwksCache(
        jwks_url_for(local_stack["api_url"]),
        fetcher=lambda url, timeout: local_http.get(
            url, headers={"apikey": local_stack["publishable_key"]}, timeout=timeout
        ).json(),
    )

    verified = verify_token(
        disposable_user["access_token"], keys=cache, issuer=issuer_for(local_stack["api_url"])
    )

    assert verified.subject == disposable_user["id"]
    assert verified.email == disposable_user["email"]
    assert verified.seconds_remaining > 0


def test_a_real_token_is_refused_for_a_different_project(local_stack, local_http, disposable_user):
    if not published_keys(local_stack, local_http):
        pytest.skip("Stack is on the legacy shared secret; see the earlier test's message.")

    cache = JwksCache(
        jwks_url_for(local_stack["api_url"]),
        fetcher=lambda url, timeout: local_http.get(
            url, headers={"apikey": local_stack["publishable_key"]}, timeout=timeout
        ).json(),
    )

    with pytest.raises(TokenRejected):
        verify_token(
            disposable_user["access_token"],
            keys=cache,
            issuer="https://someone-elses-project.supabase.co/auth/v1",
        )


# ---------------------------------------------------------------------
# Authorization reads the database, not the token
# ---------------------------------------------------------------------


def test_a_token_claiming_administrator_is_not_an_administrator(local_stack, disposable_user):
    """The demotion case, which is why roles are not read from the JWT:
    the claim says administrator, the record says nothing, and the
    database is what answers.

    The Auth user is a real one created through the admin API — writing
    into auth.users by hand would be testing a table shape GoTrue owns.
    Everything this test writes is rolled back.
    """
    connection = connect_local_database(psycopg2.connect, local_stack["db_url"])
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "insert into public.profile (auth_user_id, username) values (%s, %s) "
                "returning profile_id",
                (disposable_user["id"], f"zz_migration_smoke_{uuid.uuid4().hex[:8]}"),
            )
            profile_id = cursor.fetchone()[0]

            forged_claims = (
                '{"sub": "%s", "role": "administrator", '
                '"app_metadata": {"role": "administrator"}}' % disposable_user["id"]
            )
            cursor.execute("select set_config('request.jwt.claims', %s, true)", (forged_claims,))

            cursor.execute("select private.current_profile_id(), private.is_administrator()")
            resolved_profile, is_admin = cursor.fetchone()
            assert resolved_profile == profile_id
            assert is_admin is False, "A token claim granted an administrator privilege."

            cursor.execute(
                "insert into public.user_role (profile_id, role) values (%s, 'administrator')",
                (profile_id,),
            )
            cursor.execute("select private.is_administrator()")
            assert cursor.fetchone()[0] is True

            cursor.execute("delete from public.user_role where profile_id = %s", (profile_id,))
            cursor.execute("select private.is_administrator()")
            assert cursor.fetchone()[0] is False, (
                "Removing the record did not remove the privilege."
            )
    finally:
        connection.rollback()
        connection.close()


def test_a_profile_cannot_grant_itself_a_role(local_stack, disposable_user):
    connection = connect_local_database(psycopg2.connect, local_stack["db_url"])
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "insert into public.profile (auth_user_id, username) values (%s, %s) "
                "returning profile_id",
                (disposable_user["id"], f"zz_migration_smoke_{uuid.uuid4().hex[:8]}"),
            )
            profile_id = cursor.fetchone()[0]

            with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                cursor.execute(
                    "insert into public.user_role (profile_id, role, granted_by) "
                    "values (%s, 'administrator', %s)",
                    (profile_id, profile_id),
                )
    finally:
        connection.rollback()
        connection.close()


def test_the_authorization_helpers_are_not_reachable_over_the_data_api(local_stack, local_http):
    """They live in `private`, which is not an exposed schema. If this
    ever starts returning 200, an analyst can ask the database about
    anyone's roles."""
    for function in ("is_administrator", "current_profile_id"):
        response = local_http.post(
            f"{local_stack['rest_url']}/rpc/{function}",
            json={},
            headers={
                "apikey": local_stack["publishable_key"],
                "Authorization": f"Bearer {local_stack['publishable_key']}",
                "Content-Type": "application/json",
            },
            timeout=10,
        )
        assert response.status_code in (401, 403, 404), (
            f"rpc/{function} returned HTTP {response.status_code}"
        )
