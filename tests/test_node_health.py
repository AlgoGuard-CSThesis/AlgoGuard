"""Stage 6B.2: node health reported by signed-in installations, shown to administrators."""

import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import cloud_sync
from app_version import RELEASE, app_version
from cloud_repository import RepositoryError
from tests.cloud_support import ADMIN, ANALYST, OTHER, login, signed_in, wait_for


def health_row(cloud):
    rows = [row for row in cloud.fake.rows("node_health") if row["node_id"] == cloud.node]
    return rows[0] if rows else None


def test_a_signed_in_installation_reports_its_health_with_server_time(cloud):
    signed_in(cloud.app)
    report = wait_for(lambda: health_row(cloud))
    assert report["reported_by"] == cloud.analyst["profile_id"]
    assert report["app_version"] == app_version() and report["app_version"].startswith(RELEASE)
    assert report["capture_status"] == "idle"
    assert report["pending_events"] >= 0 and report["rejected_events"] >= 0
    received = datetime.fromisoformat(report["received_at"])
    assert abs(datetime.now(timezone.utc) - received) < timedelta(seconds=30)
    node = next(row for row in cloud.fake.rows("node") if row["node_id"] == cloud.node)
    assert node["last_seen_at"] == report["received_at"]


def test_logout_stops_heartbeats_and_offline_startup_reports_nothing(cloud):
    # Nobody has signed in: the installation registers no identity, so it reports nothing.
    assert cloud.services.sync.identities == {}
    assert health_row(cloud) is None
    client = signed_in(cloud.app)
    wait_for(lambda: health_row(cloud))
    client.post("/logout", data={"_csrf_token": client.csrf})
    assert wait_for(lambda: cloud.services.sync.identities == {} or None)
    calls = cloud.fake.count_calls("POST", "/rest/v1/rpc/report_node_health")
    cloud.services.sync.last_heartbeat.clear()  # a heartbeat would be due at once
    for identity in list(cloud.services.sync.identities.values()):
        cloud.services.sync.sync_once(identity)
    assert cloud.fake.count_calls("POST", "/rest/v1/rpc/report_node_health") == calls


def test_an_account_without_an_approved_membership_reports_nothing(cloud):
    login(cloud.app.test_client(), OTHER[0], OTHER[1])  # enrollment requested, not approved
    time.sleep(2.5)  # several upload ticks
    assert all(row["reported_by"] != cloud.other["profile_id"]
               for row in cloud.fake.rows("node_health"))


def test_a_failed_report_never_stops_uploads():
    class Refusing:
        context = SimpleNamespace(node_id=uuid4())

        def report_node_health(self, health):
            raise RepositoryError("not_found")  # a database without the 6B.2 migration

    worker = cloud_sync.SyncWorker(None, None, health=lambda context: {"app_version": "x"})
    worker._heartbeat(Refusing(), "owner")
    assert worker.heartbeat_error["owner"] == "not_found"
    # The next report waits for the interval instead of retrying every second.
    worker._heartbeat(Refusing(), "owner")
    assert "owner" in worker.last_heartbeat


def test_administrators_see_computers_and_stale_reports_are_marked(cloud):
    signed_in(cloud.app)
    wait_for(lambda: health_row(cloud))
    admin = signed_in(cloud.app, ADMIN)
    page = admin.get("/computers").get_data(as_text=True)
    assert "Reporting" in page and app_version() in page and "this installation" in page
    # No further heartbeat for a while, then make the last report ten minutes old.
    for owner in list(cloud.services.sync.identities):
        cloud.services.sync.last_heartbeat[owner] = cloud.services.sync.clock()
    old = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    with cloud.fake.lock:
        for row in cloud.fake.tables["node_health"]:
            row["received_at"] = old
    page = admin.get("/computers").get_data(as_text=True)
    assert "Stale" in page and "min ago" in page
    assert "Start" not in page and "Stop" not in page  # no remote capture controls


def test_analysts_cannot_open_the_computers_page(cloud):
    analyst = signed_in(cloud.app, ANALYST)
    response = analyst.get("/computers")
    assert response.status_code == 302
