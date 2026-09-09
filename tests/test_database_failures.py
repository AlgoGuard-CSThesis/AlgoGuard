import sqlite3

import pytest

from services import database_service as db


@pytest.mark.parametrize(
    ("path", "operation"),
    [("/", "get_detection_stats"), ("/alerts", "list_alerts"), ("/monitor/status", "get_status")],
)
def test_database_errors_return_a_response_without_redirecting(
    authenticated_client, app_module, monkeypatch, path, operation
):
    def unavailable(*args, **kwargs):
        raise sqlite3.DatabaseError("private database failure details")

    monkeypatch.setattr(app_module, operation, unavailable)
    monkeypatch.setattr(app_module, "log_system_event", unavailable)
    response = authenticated_client.get(path)

    assert response.status_code == 500
    assert "Location" not in response.headers
    assert b"private database failure details" not in response.data
    if path == "/monitor/status":
        assert response.get_json()["status"] == "error"
    else:
        assert b"Database unavailable" in response.data


@pytest.mark.parametrize("packet_size", [2**53 + 1, 2**63 - 1])
def test_integer_traffic_metadata_keeps_its_exact_value(app_module, packet_size):
    traffic_id = db.insert_network_traffic_from_flow({"packet_size": packet_size})
    with db.get_connection() as connection:
        saved = connection.execute(
            "SELECT packet_size FROM network_traffic WHERE traffic_id = ?", (traffic_id,)
        ).fetchone()
    assert saved["packet_size"] == packet_size


@pytest.mark.parametrize(
    "flow", [{"sbytes": 1e308, "dbytes": 1e308}, {"packet_size": "Infinity"}]
)
def test_unrepresentable_traffic_metadata_does_not_abort_storage(app_module, flow):
    traffic_id = db.insert_network_traffic_from_flow(flow)
    with db.get_connection() as connection:
        saved = connection.execute(
            "SELECT packet_size FROM network_traffic WHERE traffic_id = ?", (traffic_id,)
        ).fetchone()
    assert saved["packet_size"] in (None, 0)
