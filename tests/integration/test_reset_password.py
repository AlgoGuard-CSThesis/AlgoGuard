"""The maintainer password reset against the local stack's real Auth service."""

import uuid
from types import SimpleNamespace

import pytest
from stack_support import require_status

from reset_password import reset

pytestmark = pytest.mark.integration


def test_a_reset_replaces_the_password_and_ends_running_sign_ins(local_stack, local_http):
    api = local_stack["api_url"]
    maintenance = {"apikey": local_stack["secret_key"],
                   "Authorization": "Bearer " + local_stack["secret_key"]}
    email = f"zz_migration_smoke_reset_{uuid.uuid4().hex[:10]}@algoguard.invalid"
    old = "Integration!" + uuid.uuid4().hex
    created = local_http.post(api + "/auth/v1/admin/users", headers=maintenance,
                              json={"email": email, "password": old, "email_confirm": True})
    require_status(created, (200, 201), "fixture account")

    def sign_in(password):
        return local_http.post(api + "/auth/v1/token",
                               headers={"apikey": local_stack["publishable_key"]},
                               params={"grant_type": "password"},
                               json={"email": email, "password": password})

    try:
        running = sign_in(old).json()
        generated = reset(SimpleNamespace(email=email, api_url=api, password_stdin=False),
                          {"SUPABASE_SECRET_KEY": local_stack["secret_key"]})
        assert generated
        assert sign_in(generated).status_code == 200
        assert sign_in(old).status_code == 400
        renewal = local_http.post(api + "/auth/v1/token",
                                  headers={"apikey": local_stack["publishable_key"]},
                                  params={"grant_type": "refresh_token"},
                                  json={"refresh_token": running["refresh_token"]})
        assert renewal.status_code == 400
    finally:
        local_http.delete(api + "/auth/v1/admin/users/" + created.json()["id"],
                          headers=maintenance)
