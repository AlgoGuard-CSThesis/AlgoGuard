from unittest.mock import Mock

import pytest

from services import database_service as db


@pytest.mark.parametrize("endpoint", ["/predict", "/monitor/start"])
@pytest.mark.parametrize("body", ['{broken json', '[]', '[1]', 'false', 'null', '0', '"flow"', ''])
def test_invalid_json_bodies_are_rejected_before_actions(
    authenticated_client, app_module, monkeypatch, endpoint, body
):
    prediction = Mock()
    start = Mock()
    monkeypatch.setattr(app_module, "_execute_prediction", prediction)
    monkeypatch.setattr(app_module, "start_session", start)
    before = db.get_detection_stats()["total_flows"]

    response = authenticated_client.post(endpoint, data=body, content_type="application/json")

    assert response.status_code == 400
    assert "valid JSON object" in response.get_json()["message"]
    prediction.assert_not_called()
    start.assert_not_called()
    assert db.get_detection_stats()["total_flows"] == before


@pytest.mark.parametrize("endpoint", ["/predict", "/monitor/start"])
def test_json_actions_require_json_content_type(authenticated_client, endpoint):
    response = authenticated_client.post(endpoint, data="{}", content_type="text/plain")
    assert response.status_code == 400
    assert response.get_json()["status"] == "error"


@pytest.mark.parametrize("token", ["invalid", "\u00e9", "\U0001f512"])
def test_invalid_login_tokens_return_client_error(client, token):
    client.get("/login")

    response = client.post("/login", data={"_csrf_token": token})

    assert response.status_code == 400
    assert "Invalid or expired request token" in response.get_data(as_text=True)
    with client.session_transaction() as login_session:
        assert "admin_id" not in login_session
