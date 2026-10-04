"""Failed local sign-ins are limited per account and per client address."""

import pytest

from services import database_service as db


@pytest.fixture
def clock(app_module, monkeypatch):
    now = {"value": 1_000.0}
    monkeypatch.setattr(app_module, "_monotonic", lambda: now["value"])
    app_module._login_failures.clear()
    yield now
    app_module._login_failures.clear()


def attempt(client, username, password):
    client.get("/login")
    with client.session_transaction() as test_session:
        token = test_session["_csrf_token"]
    return client.post(
        "/login", data={"username": username, "password": password, "_csrf_token": token}
    )


def test_repeated_failures_pause_the_account_then_release_it(client, app_module, clock):
    for _ in range(app_module.LOGIN_ATTEMPTS_PER_ACCOUNT):
        assert attempt(client, "admin", "wrong-password").status_code == 200

    blocked = attempt(client, "admin", "admin123")
    assert blocked.status_code == 429
    page = blocked.get_data(as_text=True)
    assert "Too many failed sign-in attempts. Try again in 15 minutes." in page
    assert "Invalid username or password" not in page
    assert db.list_system_logs({"search": "Sign-in for admin blocked for 15 minutes"})["total"] == 1

    clock["value"] += app_module.LOGIN_ATTEMPT_WINDOW_SECONDS
    assert attempt(client, "admin", "admin123").status_code == 302


def test_success_clears_the_account_counter(client, app_module, clock):
    for _ in range(app_module.LOGIN_ATTEMPTS_PER_ACCOUNT - 1):
        attempt(client, "admin", "wrong-password")
    assert attempt(client, "admin", "admin123").status_code == 302

    fresh = app_module.app.test_client()
    for _ in range(app_module.LOGIN_ATTEMPTS_PER_ACCOUNT - 1):
        assert attempt(fresh, "admin", "wrong-password").status_code == 200
    assert attempt(fresh, "admin", "admin123").status_code == 302


def test_one_address_cannot_spray_many_usernames(client, app_module, clock):
    for index in range(app_module.LOGIN_ATTEMPTS_PER_ADDRESS):
        attempt(client, f"guess{index}", "wrong-password")
    assert attempt(client, "admin", "admin123").status_code == 429


def test_failures_older_than_the_window_are_forgotten(client, app_module, clock):
    for _ in range(app_module.LOGIN_ATTEMPTS_PER_ACCOUNT - 1):
        attempt(client, "admin", "wrong-password")
    clock["value"] += app_module.LOGIN_ATTEMPT_WINDOW_SECONDS
    attempt(client, "admin", "wrong-password")
    assert attempt(client, "admin", "admin123").status_code == 302
