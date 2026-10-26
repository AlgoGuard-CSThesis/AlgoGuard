"""Stage 6B.1: alerts, logs and reports across nodes for administrators, scoped for analysts."""

import re
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from tests.cloud_support import ADMIN, ANALYST, signed_in

HOUR_AGO = datetime.now(timezone.utc) - timedelta(hours=1)


def seed_alert(fake, node_id, profile_id, description, detected=HOUR_AGO, received=None):
    """One alert with its prediction and traffic, as store_flow_batch would leave them."""
    received = received or detected
    common = {"node_id": node_id, "owner_profile_id": profile_id,
              "created_at": received.isoformat()}
    traffic = fake.insert("network_traffic", {**common, "source_ip": "10.0.0.1",
                                              "destination_ip": "10.0.0.2"})
    prediction = fake.insert("prediction", {**common, "traffic_id": traffic["traffic_id"],
                                            "predicted_label": "Attack", "confidence_score": 99.0,
                                            "latency_ms": 1.0, "alert_created": 1,
                                            "prediction_timestamp": detected.strftime(
                                                "%Y-%m-%d %H:%M:%S"),
                                            "model_name": "Stacking Ensemble"})
    return fake.insert("alert", {**common, "prediction_id": prediction["prediction_id"],
                                 "severity_level": "High", "alert_status": "Open",
                                 "detected_at": detected.strftime("%Y-%m-%d %H:%M:%S"),
                                 "description": description})


def second_node(cloud, name="Lab PC B"):
    node = str(uuid4())
    cloud.fake.approve(cloud.other["profile_id"], node)
    with cloud.fake.lock:
        cloud.fake._node(node)["display_name"] = name
    return node


def test_administrators_choose_one_or_all_computers(cloud):
    other = second_node(cloud)
    seed_alert(cloud.fake, cloud.node, cloud.analyst["profile_id"], "alert on this computer")
    seed_alert(cloud.fake, other, cloud.other["profile_id"], "alert on PC B")
    admin = signed_in(cloud.app, ADMIN)

    local = admin.get("/alerts").get_data(as_text=True)
    assert "alert on this computer" in local and "alert on PC B" not in local

    everything = admin.get("/alerts?node=all").get_data(as_text=True)
    assert "alert on this computer" in everything and "alert on PC B" in everything
    assert "Lab PC B" in everything and "this computer" in everything
    # The administrator is not a member of PC B: its alerts can be observed, not reviewed here.
    assert "View only" in everything
    # Report selection is limited to this installation's own alerts.
    assert len(re.findall(r'name="alert_id"', everything)) == 1

    one = admin.get(f"/alerts?node={other}").get_data(as_text=True)
    assert "alert on PC B" in one and "alert on this computer" not in one


def test_analysts_see_only_their_computers_even_with_a_hand_edited_url(cloud):
    other = second_node(cloud)
    seed_alert(cloud.fake, other, cloud.other["profile_id"], "alert on PC B")
    analyst = signed_in(cloud.app, ANALYST)
    page = analyst.get("/alerts").get_data(as_text=True)
    assert "Lab PC B" not in page  # not offered in the computer list
    for url in (f"/alerts?node={other}", "/alerts?node=all", "/logs?node=all",
                f"/reports?node={other}"):
        assert "alert on PC B" not in analyst.get(url).get_data(as_text=True)
    assert "not visible to this account" in analyst.get(
        f"/alerts?node={other}", follow_redirects=True).get_data(as_text=True)


def test_rows_separate_the_event_time_from_a_late_upload(cloud):
    detected = datetime.now(timezone.utc) - timedelta(hours=3)
    seed_alert(cloud.fake, cloud.node, cloud.analyst["profile_id"], "uploaded late",
               detected=detected, received=detected + timedelta(minutes=95))
    page = signed_in(cloud.app, ANALYST).get("/alerts").get_data(as_text=True)
    assert detected.strftime("%Y-%m-%d %H:%M:%S") in page
    assert "Received" in page and "95 min after the event" in page


def test_alert_dates_filter_and_the_review_returns_to_the_same_view(cloud):
    old = datetime.now(timezone.utc) - timedelta(days=40)
    seed_alert(cloud.fake, cloud.node, cloud.analyst["profile_id"], "forty days ago", detected=old)
    recent = seed_alert(cloud.fake, cloud.node, cloud.analyst["profile_id"], "this week")
    analyst = signed_in(cloud.app, ANALYST)
    since = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
    page = analyst.get(f"/alerts?date_from={since}").get_data(as_text=True)
    assert "this week" in page and "forty days ago" not in page
    response = analyst.post(f"/alerts/{recent['alert_id']}/status", data={
        "_csrf_token": analyst.csrf, "alert_status": "Acknowledged", "node": "all",
        "date_from": since, "offset": "0"})
    assert response.status_code == 302
    assert "node=all" in response.headers["Location"]
    assert f"date_from={since}" in response.headers["Location"]


def test_logs_and_reports_follow_the_computer_choice(cloud):
    other = second_node(cloud)
    cloud.fake.insert("system_log", {"node_id": other, "profile_id": cloud.other["profile_id"],
                                     "module": "Live Monitor", "action": "monitor_started",
                                     "status": "Success", "message": "started on PC B",
                                     "timestamp": HOUR_AGO.strftime("%Y-%m-%d %H:%M:%S")})
    cloud.fake.insert("report", {"node_id": other, "owner_profile_id": cloud.other["profile_id"],
                                 "report_type": "Operational evidence",
                                 "generated_at": HOUR_AGO.strftime("%Y-%m-%d %H:%M:%S")})
    admin = signed_in(cloud.app, ADMIN)
    assert "started on PC B" not in admin.get("/logs").get_data(as_text=True)
    logs = admin.get("/logs?node=all").get_data(as_text=True)
    assert "started on PC B" in logs and "Lab PC B" in logs
    reports = admin.get(f"/reports?node={other}").get_data(as_text=True)
    assert "Lab PC B" in reports and "Operational evidence" in reports
