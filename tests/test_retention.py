"""Stage 6B.3: administrators preview and run the audited cleanup from the Retention page."""

import re
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import cloud_app
from tests.cloud_support import ADMIN, ANALYST, signed_in
from tests.test_cross_node_views import seed_alert

NOW = datetime.now(timezone.utc)


def days_ago(days):
    return NOW - timedelta(days=days)


def seed_history(cloud):
    """Two expired flows, one old flow a recent report cites, one recent flow, one old log."""
    fake, owner = cloud.fake, cloud.analyst["profile_id"]
    expired = [seed_alert(fake, cloud.node, owner, f"old alert {n}", received=days_ago(45))
               for n in range(2)]
    held = seed_alert(fake, cloud.node, owner, "cited alert", received=days_ago(45))
    recent = seed_alert(fake, cloud.node, owner, "recent alert", received=days_ago(5))
    report = fake.insert("report", {"node_id": cloud.node, "owner_profile_id": owner,
                                    "report_type": "Operational evidence", "run_id": None,
                                    "generated_at": "2026-10-17 00:00:00",
                                    "created_at": days_ago(10).isoformat()})
    fake.insert("report_alert", {"report_id": report["report_id"], "alert_id": held["alert_id"],
                                 "node_id": cloud.node})
    old_log = fake.insert("system_log", {
        "node_id": cloud.node, "profile_id": owner, "module": "Live Monitor", "action": "fixture",
        "status": "Success", "message": "old standalone log", "prediction_id": None,
        "timestamp": "2026-09-01 00:00:00", "created_at": days_ago(40).isoformat()})
    return expired, held, recent, old_log


def alert_ids(cloud):
    return {row["alert_id"] for row in cloud.fake.rows("alert")}


def cleanup(client, request_id):
    return client.post("/retention", data={"_csrf_token": client.csrf, "request_id": request_id},
                       follow_redirects=True).get_data(as_text=True)


def figure(page, label):
    return int(re.search(re.escape(label) + r"</dt><dd>([\d,]+)</dd>", page).group(1))


def test_the_preview_shows_what_would_go_what_stays_and_clocks_to_check(cloud):
    expired, held, recent, _ = seed_history(cloud)
    cloud.fake.insert("ingest_event", {
        "event_uuid": str(uuid4()), "node_id": cloud.node,
        "owner_profile_id": cloud.analyst["profile_id"], "prediction_id": recent["prediction_id"],
        "traffic_id": None, "alert_id": recent["alert_id"], "audit_log_id": None,
        "payload": {"event_time": (NOW + timedelta(days=1)).isoformat()}})
    page = signed_in(cloud.app, ADMIN).get("/retention").get_data(as_text=True)
    assert "Retention and Capacity" in page
    assert figure(page, "Flows (traffic, prediction and alert)") == 2
    assert figure(page, "Alerts cited by a kept report") == 1
    assert figure(page, "Other log entries") == 1
    assert "Clocks to check" in page and "No cleanup has run yet" in page
    # A preview changes nothing.
    assert {row["alert_id"] for row in expired + [held, recent]} <= alert_ids(cloud)


def test_cleanup_runs_bounded_batches_keeps_cited_and_recent_flows_and_is_audited(
        cloud, monkeypatch):
    monkeypatch.setattr(cloud_app, "RETENTION_BATCH", 1)
    expired, held, recent, old_log = seed_history(cloud)
    admin = signed_in(cloud.app, ADMIN)
    request_id = str(uuid4())
    page = cleanup(admin, request_id)
    done = ("Removed 2 flows (2 alerts), 1 log entries, 0 reports and 0 closed capture sessions "
            "in 2 batches.")
    assert done in page
    remaining = alert_ids(cloud)
    assert not {row["alert_id"] for row in expired} & remaining
    assert {held["alert_id"], recent["alert_id"]} <= remaining
    assert old_log["log_id"] not in {row["log_id"] for row in cloud.fake.rows("system_log")}
    audits = [row for row in cloud.fake.rows("system_log") if row["module"] == "Retention"]
    assert len(audits) == 2 and all(row["profile_id"] == cloud.admin["profile_id"]
                                    for row in audits)
    assert "Removed 1 flows (1 alerts), 1 log entries" in page  # under Recent cleanup batches

    # Submitting the same form again replays the recorded batches instead of repeating them.
    assert done in cleanup(admin, request_id)
    assert sum(row["module"] == "Retention" for row in cloud.fake.rows("system_log")) == 2


def test_a_click_stops_after_its_batch_limit_and_says_more_remain(cloud, monkeypatch):
    monkeypatch.setattr(cloud_app, "RETENTION_BATCH", 1)
    monkeypatch.setattr(cloud_app, "RETENTION_MAX_BATCHES", 1)
    seed_history(cloud)
    admin = signed_in(cloud.app, ADMIN)
    assert "More remain: run the cleanup again." in cleanup(admin, str(uuid4()))
    page = cleanup(admin, str(uuid4()))
    assert "Removed 1 flows (1 alerts), 0 log entries, 0 reports and 0 closed capture " in page
    assert "More remain" not in page


def test_analysts_can_neither_preview_nor_run_a_cleanup(cloud):
    seed_history(cloud)
    before = alert_ids(cloud)
    analyst = signed_in(cloud.app, ANALYST)
    assert analyst.get("/retention").status_code == 302
    response = analyst.post("/retention", data={"_csrf_token": analyst.csrf,
                                                "request_id": str(uuid4())})
    assert response.status_code == 302
    assert alert_ids(cloud) == before
    assert "Retention" not in analyst.get("/", follow_redirects=True).get_data(as_text=True)
