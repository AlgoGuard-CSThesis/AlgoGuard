"""Alert severity, endpoints, and the Open -> Acknowledged -> Resolved review workflow."""

import math

import pytest

from services import database_service as db
from services.alert_policy import severity_for_confidence
from tests.cloud_support import ADMIN, attack_rows, post_json, signed_in, wait_for


@pytest.mark.parametrize(
    ("confidence", "expected"),
    [
        (99.4, "High"),
        (90.0, "High"),
        (89.99, "Medium"),
        (75.0, "Medium"),
        (62.5, "Low"),
        (50.0, "Low"),
        (None, "High"),
        ("not a number", "High"),
        (math.nan, "High"),
    ],
)
def test_severity_follows_confidence_and_never_understates(confidence, expected):
    assert severity_for_confidence(confidence) == expected


# -- SQLite application -------------------------------------------------------------


@pytest.fixture
def model_id(trained_bundle):
    with db.get_connection() as connection:
        row = connection.execute(
            "SELECT model_id FROM detection_model WHERE run_id = ? AND model_name = ?",
            (trained_bundle["run_id"], "Stacking Ensemble"),
        ).fetchone()
    return row["model_id"]


def store_attack(model_id, confidence, alert_severity=None, **flow):
    _, _, alert_id = db.store_classified_flow(
        {"dur": 1.0, "proto": "tcp", **flow},
        "live_monitor",
        model_id,
        "Attack",
        confidence,
        model_name="Stacking Ensemble",
        create_alert=True,
        alert_severity=alert_severity,
        alert_description="Fixture alert.",
    )
    return alert_id


def alert_row(alert_id):
    return next(row for row in db.list_alerts(limit=1_000_000) if row["alert_id"] == alert_id)


def test_stored_alert_severity_follows_confidence_unless_given(model_id):
    assert alert_row(store_attack(model_id, 97.0))["severity_level"] == "High"
    assert alert_row(store_attack(model_id, 80.0))["severity_level"] == "Medium"
    assert alert_row(store_attack(model_id, 55.0))["severity_level"] == "Low"
    assert alert_row(store_attack(model_id, 97.0, alert_severity="Low"))["severity_level"] == "Low"


def test_alerts_show_flow_endpoints_and_move_through_review(authenticated_client, model_id):
    alert_id = store_attack(
        model_id, 97.0,
        source_ip="10.0.0.5", destination_ip="192.168.1.9", source_port=4444, destination_port=80,
    )
    replay_id = store_attack(model_id, 97.0, source_ip="10.1.2.3", destination_ip="192.168.4.5")
    manual_id = store_attack(model_id, 97.0)  # stored with the "manual" endpoint placeholder

    page = authenticated_client.get("/alerts").get_data(as_text=True)
    assert "10.0.0.5:4444 &rarr; 192.168.1.9:80" in page
    assert "10.1.2.3 &rarr; 192.168.4.5" in page  # port 0 means "not recorded"
    assert "Manual entry" in page and "manual:0" not in page
    assert f'action="/alerts/{alert_id}/status"' in page
    assert f'aria-label="Acknowledge alert {alert_id}"' in page
    assert {replay_id, manual_id} <= {row["alert_id"] for row in db.list_alerts(limit=10)}

    open_before = db.get_detection_stats()["alerts_open"]
    acknowledged = authenticated_client.post(
        f"/alerts/{alert_id}/status", data={"alert_status": "Acknowledged"}, follow_redirects=True
    ).get_data(as_text=True)
    assert f"Alert #{alert_id} marked Acknowledged." in acknowledged
    assert alert_row(alert_id)["alert_status"] == "Acknowledged"
    assert db.get_detection_stats()["alerts_open"] == open_before - 1
    assert f'aria-label="Acknowledge alert {alert_id}"' not in acknowledged

    resolved = authenticated_client.post(
        f"/alerts/{alert_id}/status", data={"alert_status": "Resolved"}, follow_redirects=True
    ).get_data(as_text=True)
    assert alert_row(alert_id)["alert_status"] == "Resolved"
    assert f'aria-label="Reopen alert {alert_id}"' in resolved

    authenticated_client.post(f"/alerts/{alert_id}/status", data={"alert_status": "Open"})
    assert alert_row(alert_id)["alert_status"] == "Open"
    assert db.list_system_logs({"search": f"Alert {alert_id} marked Resolved."})["total"] == 1


def test_alert_review_rejects_unknown_states_and_alerts(authenticated_client, model_id):
    alert_id = store_attack(model_id, 80.0)
    rejected = authenticated_client.post(
        f"/alerts/{alert_id}/status", data={"alert_status": "Deleted"}, follow_redirects=True
    ).get_data(as_text=True)
    assert "Choose Open, Acknowledged, or Resolved." in rejected
    assert alert_row(alert_id)["alert_status"] == "Open"

    missing = authenticated_client.post(
        "/alerts/999999999/status", data={"alert_status": "Resolved"}, follow_redirects=True
    ).get_data(as_text=True)
    assert "Alert #999999999 no longer exists." in missing


def test_alert_review_requires_login_and_a_request_token(client, model_id):
    alert_id = store_attack(model_id, 80.0)
    anonymous = client.post(f"/alerts/{alert_id}/status", data={"alert_status": "Resolved"})
    assert anonymous.status_code == 302 and "/login" in anonymous.headers["Location"]

    client.get("/login")
    with client.session_transaction() as test_session:
        token = test_session["_csrf_token"]
    signed = client.post(
        "/login", data={"username": "admin", "password": "admin123", "_csrf_token": token}
    )
    assert signed.status_code == 302
    forged = client.post(f"/alerts/{alert_id}/status", data={"alert_status": "Resolved"})
    assert forged.status_code == 400
    assert alert_row(alert_id)["alert_status"] == "Open"


# -- Cloud application --------------------------------------------------------------


def synced_attack(cloud, client):
    for row in attack_rows():
        result = post_json(client, "/predict", row).get_json()
        if result["prediction"] == "Attack":
            wait_for(lambda: client.get(f"/api/events/{result['event_uuid']}").get_json()
                     .get("synced"))
            alert = cloud.fake.rows("alert")[-1]
            return alert, result
    raise AssertionError("no attack verdict in the sample rows")


def test_cloud_alert_severity_and_review_go_through_row_level_security(cloud):
    client = signed_in(cloud.app)
    alert, result = synced_attack(cloud, client)
    assert alert["severity_level"] == severity_for_confidence(result["confidence"])

    page = client.get("/alerts").get_data(as_text=True)
    assert f'action="/alerts/{alert["alert_id"]}/status"' in page
    assert 'form="reportForm"' in page and 'id="reportForm"' in page

    resolved = client.post(
        f"/alerts/{alert['alert_id']}/status",
        data={"_csrf_token": client.csrf, "alert_status": "Resolved", "offset": "0",
              "filter": "Open"},
    )
    assert resolved.status_code == 302
    assert "alert_status=Open" in resolved.headers["Location"]
    assert cloud.fake.rows("alert")[-1]["alert_status"] == "Resolved"
    wait_for(lambda: [row for row in cloud.fake.rows("system_log")
                      if row.get("action") == "alert_status_changed"])

    row_form = f'action="/alerts/{alert["alert_id"]}/status"'
    listed = client.get("/alerts?alert_status=Resolved").get_data(as_text=True)
    assert row_form in listed and "review-resolved" in listed
    assert row_form not in client.get("/alerts?alert_status=Open").get_data(as_text=True)


def test_cloud_alert_review_refuses_hidden_alerts_and_unknown_states(cloud):
    client = signed_in(cloud.app)
    alert, _ = synced_attack(cloud, client)
    path = f"/alerts/{alert['alert_id']}/status"

    unknown = client.post(path, data={"_csrf_token": client.csrf, "alert_status": "Gone"},
                          follow_redirects=True).get_data(as_text=True)
    assert "Choose Open, Acknowledged, or Resolved." in unknown
    missing = client.post("/alerts/987654/status", data={
        "_csrf_token": client.csrf, "alert_status": "Resolved"}, follow_redirects=True)
    assert "Alert #987654 is unavailable on this installation." in missing.get_data(as_text=True)

    # A revoked member keeps the browser session but may no longer write the node's alerts.
    cloud.fake.revoke(cloud.analyst["profile_id"], cloud.node)
    client.post(path, data={"_csrf_token": client.csrf, "alert_status": "Resolved"})
    assert cloud.fake.rows("alert")[-1]["alert_status"] == "Open"


def test_cloud_destructive_admin_actions_ask_for_confirmation(cloud):
    admin = signed_in(cloud.app, ADMIN)
    accounts = admin.get("/admins").get_data(as_text=True)
    assert 'data-confirm="Deactivate analyst?' in accounts
    assert 'autocomplete="new-password"' in accounts
    nodes = admin.get("/nodes").get_data(as_text=True)
    assert 'data-confirm="Revoke analyst on this installation?' in nodes
