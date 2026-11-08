"""Stage 6C.2 on the local stack: injected write failures, kill/restart, and a stalled network.

Uploads go through ``FaultProxy``, a loopback relay that breaks chosen requests on purpose.
The checks are the server's own rows (each event stored once, never partly) and the local
spool's states (synced only after an acknowledgement, never lost).
"""

import json
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from cloud_auth import CloudAuth
from cloud_outbox import Outbox
from cloud_repository import CloudRepository, RepositoryError, UserNodeContext
from cloud_sync import SyncWorker
from tests.cloud_support import monitor_until, post_json, wait_for
from tests.integration.test_atomic_flows import event

from .app_fixtures import make_account, sign_in, start_app
from .fault_proxy import FaultProxy, route_through

pytestmark = pytest.mark.integration
CHILD = Path(__file__).with_name("upload_child.py")
STORE = "/rest/v1/rpc/store_flow_batch"


@pytest.fixture
def proxy(local_stack):
    relay = FaultProxy(local_stack["api_url"])
    yield relay
    relay.close()


def uploader(cloud, scope, local_stack, proxy, tmp_path, name="spool"):
    actor = cloud["users"][1]
    auth = route_through(CloudAuth(local_stack["api_url"], local_stack["publishable_key"],
                                   scope["nodes"][0]), proxy)
    identity = auth.authenticate_access(actor["token"])
    spool = Outbox(tmp_path / name)
    spool.start()
    return identity, spool, SyncWorker(auth, spool)


def fill(spool, identity, events):
    for item in events:
        assert spool.submit(identity, "drill", item, capped=False)["persistence"] == "in_memory"
    deadline = time.monotonic() + 60
    while not spool.drain():
        assert time.monotonic() < deadline


def server_rows(cloud, event_ids):
    """(ingest events, predictions, traffic rows) the server holds for these event UUIDs."""
    return tuple(cloud["sql"](
        "select count(*),count(distinct prediction_id),count(distinct traffic_id) "
        "from public.ingest_event where event_uuid=any(%s::uuid[])", (list(event_ids),))[0])


def test_failures_before_commit_and_lost_acknowledgements_store_each_event_once(
        cloud, scope, local_stack, proxy, tmp_path):
    identity, spool, worker = uploader(cloud, scope, local_stack, proxy, tmp_path)
    events = [event(scope) for _ in range(200)]
    ids = [item["event_uuid"] for item in events]
    try:
        fill(spool, identity, events)
        before = cloud["sql"]("select count(*) from public.prediction where owner_profile_id=%s",
                              (identity.profile_id,))[0][0]
        # Four batches go at once: one never reaches the cloud, one is committed but its answer
        # is lost, one is cut off mid-body, one succeeds.
        proxy.rule(STORE, "refuse")
        proxy.rule(STORE, "drop")
        proxy.rule(STORE, "truncate")
        worker.sync_once(identity)
        assert sorted(proxy.actions(STORE)) == ["drop", "pass", "refuse", "truncate"]
        assert worker.retry[str(identity.user_id)][0] == 1
        counts = spool.counts(identity)
        assert (counts["synced"], counts["pending"]) == (50, 150)
        assert server_rows(cloud, ids) == (100, 100, 100)  # the passed and the dropped batch
        worker.retry.clear()
        worker.sync_once(identity)
        counts = spool.counts(identity)
        assert (counts["synced"], counts["pending"], counts["rejected"]) == (200, 0, 0)
        assert server_rows(cloud, ids) == (200, 200, 200)
        after = cloud["sql"]("select count(*) from public.prediction where owner_profile_id=%s",
                             (identity.profile_id,))[0][0]
        assert after - before == 200  # the replayed batch added nothing
    finally:
        spool.close()


def test_malformed_and_changed_duplicate_events_are_rejected_alone(
        cloud, scope, local_stack, proxy, tmp_path):
    identity, spool, worker = uploader(cloud, scope, local_stack, proxy, tmp_path)
    actor = cloud["users"][1]
    direct = CloudRepository(local_stack["api_url"], local_stack["publishable_key"],
                             UserNodeContext(uuid.UUID(actor["id"]), actor["profile"],
                                             uuid.UUID(scope["nodes"][0]), actor["token"]))
    original = event(scope)
    direct.store_flows([original])
    changed = json.loads(json.dumps(original))
    changed["traffic"]["source_ip"] = "10.9.9.9"  # same UUID, different content
    malformed = event(scope)
    malformed["prediction"]["confidence_score"] = 150
    good = [event(scope) for _ in range(48)]
    try:
        fill(spool, identity, good[:20] + [malformed] + good[20:40] + [changed] + good[40:])
        worker.sync_once(identity)
        states = spool.statuses(identity, [malformed["event_uuid"], changed["event_uuid"]])
        assert states[malformed["event_uuid"]] == {"persistence": "rejected",
                                                   "reason": "validation"}
        assert states[changed["event_uuid"]] == {"persistence": "rejected", "reason": "conflict"}
        counts = spool.counts(identity)
        assert (counts["synced"], counts["rejected"], counts["pending"]) == (48, 2, 0)
        assert server_rows(cloud, [item["event_uuid"] for item in good]) == (48, 48, 48)
        assert server_rows(cloud, [malformed["event_uuid"]]) == (0, 0, 0)
        kept = cloud["sql"]("select payload->'traffic'->>'source_ip' from public.ingest_event "
                            "where event_uuid=%s", (original["event_uuid"],))
        assert kept == [("10.0.0.1",)]  # the cloud's copy was never changed
        exported = {row["event_uuid"]: row for row in spool.export_pending(identity)}
        assert set(exported) == {malformed["event_uuid"], changed["event_uuid"]}
    finally:
        spool.close()


def run_child(config):
    return subprocess.Popen([sys.executable, "-I", str(CHILD), json.dumps(config)],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            cwd=str(CHILD.parents[2]))


def test_a_killed_uploader_restarts_without_duplicates_and_closes_its_capture_late(
        cloud, scope, local_stack, published, proxy, tmp_path):
    actor = cloud["users"][1]
    node = scope["nodes"][0]
    repo = CloudRepository(local_stack["api_url"], local_stack["publishable_key"],
                           UserNodeContext(uuid.UUID(actor["id"]), actor["profile"],
                                           uuid.UUID(node), actor["token"]))
    capture_id = repo.open_capture(uuid.uuid4(), published["deployment_id"], "csv")
    flows = []
    for _ in range(200):
        item = event({"model": published["model_id"], "deployment": published["deployment_id"]})
        item["capture_id"] = capture_id
        flows.append(item)
    ids = [item["event_uuid"] for item in flows]
    spool = Outbox(tmp_path / "spool")
    spool.start()
    fill(spool, repo.context, flows)
    # The final capture summary was queued before the crash; it arrives after the restart.
    spool.save_lifecycle(repo.context, str(uuid.uuid4()), {
        "capture_id": capture_id, "status": "completed", "flows_emitted": 200,
        "packets_captured": 4000, "packets_dropped": 3})
    spool.close()
    config = {"mode": "upload", "api_url": local_stack["api_url"], "proxy_url": proxy.url,
              "publishable_key": local_stack["publishable_key"], "node_id": node,
              "token": actor["token"], "spool": str(tmp_path / "spool")}

    # Every batch reaches the cloud and commits, then the process dies before any answer.
    proxy.rule(STORE, "hold", times=4)
    child = run_child(config)
    try:
        wait_for(lambda: proxy.waiting == 4, timeout=60)
        wait_for(lambda: server_rows(cloud, ids)[0] == 200, timeout=30)
        child.kill()
        child.wait(timeout=30)
    finally:
        if child.poll() is None:
            child.kill()
    proxy.release()
    status = cloud["sql"]("select status,closed_at from public.capture_session "
                          "where capture_id=%s", (capture_id,))[0]
    assert status == ("running", None)  # the summary had not been sent yet

    restarted = run_child(config)
    output, _ = restarted.communicate(timeout=120)
    assert restarted.returncode == 0, output[-2000:]
    assert 'done {"synced": 200, "rejected": 0}' in output
    assert server_rows(cloud, ids) == (200, 200, 200)
    assert cloud["sql"]("select count(*) from public.network_traffic where capture_id=%s",
                        (capture_id,))[0][0] == 200
    closed = cloud["sql"]("select status,flows_emitted,packets_captured,packets_dropped,"
                          "closed_at is not null from public.capture_session "
                          "where capture_id=%s", (capture_id,))[0]
    assert closed == ("completed", 200, 4000, 3, True)
    recovered = Outbox(tmp_path / "spool")
    try:
        counts = recovered.counts(repo.context)
        assert (counts["synced"], counts["pending"], counts["summaries_pending"]) == (200, 0, 0)
    finally:
        recovered.close()


def test_a_process_killed_while_writing_leaves_a_consistent_spool(cloud, scope, tmp_path):
    actor = cloud["users"][1]
    config = {"mode": "write", "user_id": actor["id"], "profile_id": actor["profile"],
              "node_id": scope["nodes"][0], "spool": str(tmp_path / "spool")}
    child = run_child(config)
    assert child.stdout is not None
    try:
        assert child.stdout.readline().strip() == "ready"
        while int(child.stdout.readline().strip() or 0) < 400:
            pass
        time.sleep(0.5)  # the writer is mid-transaction more often than not
        child.kill()
        child.wait(timeout=30)
    finally:
        if child.poll() is None:
            child.kill()
    spool = Outbox(tmp_path / "spool")
    try:
        assert spool.db.execute("pragma integrity_check").fetchone()[0] == "ok"
        owner = UserNodeContext(uuid.UUID(actor["id"]), actor["profile"],
                                uuid.UUID(scope["nodes"][0]), "memory-only-token")
        rows = spool.db.execute("select count(*),count(distinct event_uuid) from event")
        total, distinct = rows.fetchone()
        assert total == distinct > 0  # every committed write survived, none duplicated
        assert spool.counts(owner)["pending"] == total
        assert all(json.loads(row[0])["padding"] == "x" * 2000
                   for row in spool.db.execute("select payload from event"))
    finally:
        spool.close()


def test_a_stalled_network_keeps_stop_bounded_and_records_pending_until_it_returns(
        cloud, local_stack, local_http, published, proxy, tmp_path, monkeypatch):
    from services import live_monitor_service

    monkeypatch.setitem(live_monitor_service.SPEED_CHOICES, "fast", 0.001)
    analyst = make_account(cloud, local_stack, local_http, "stall")
    env = start_app(cloud, local_stack, tmp_path / "pc", [analyst])
    route_through(env.services.auth, proxy)
    try:
        client = sign_in(env.app, analyst)
        started = post_json(client, "/monitor/start", {"source_type": "csv", "speed": "fast",
                                                       "persist": "all"})
        assert started.status_code == 200, started.get_json()
        monitor_until(client, lambda data: data["session"]["totals"]["flows"] >= 40, timeout=60)
        proxy.rule("/", "stall", times=10_000)  # from now on nothing answers
        time.sleep(2)
        begun = time.monotonic()
        stopped = post_json(client, "/monitor/stop").get_json()["session"]
        assert time.monotonic() - begun < 6 and stopped["state"] == "stopped"
        queued = stopped["totals"]["queued"]
        status = client.get("/monitor/status").get_json()["session"]
        assert status["synchronization"]["pending"] >= 1
        assert status["synchronization"]["pending"] + status["synchronization"]["synced"] \
            + status["synchronization"]["in_memory"] == queued
        dashboard = client.get("/").get_data(as_text=True)
        assert "pending" in dashboard.lower()
        proxy.clear()
        proxy.release()
        env.services.sync.retry.clear()
        capture = status["capture_id"]
        wait_for(lambda: cloud["sql"]("select count(*) from public.ingest_event "
                                      "where capture_id=%s", (capture,))[0][0] == queued,
                 timeout=90)
        wait_for(lambda: cloud["sql"]("select closed_at is not null from public.capture_session "
                                      "where capture_id=%s", (capture,))[0][0], timeout=60)
        final = client.get("/monitor/status").get_json()["session"]["synchronization"]
        assert (final["synced"], final["pending"]) == (queued, 0)
    finally:
        proxy.release()
        env.services.close()


def test_store_failures_inside_the_database_roll_back_the_whole_batch(cloud, scope, local_stack):
    actor = cloud["users"][1]
    repo = CloudRepository(local_stack["api_url"], local_stack["publishable_key"],
                           UserNodeContext(uuid.UUID(actor["id"]), actor["profile"],
                                           uuid.UUID(scope["nodes"][0]), actor["token"]))
    batch = [event(scope) for _ in range(10)]
    batch[7]["prediction"]["latency_ms"] = -1  # valid shape, refused by the database
    with pytest.raises(RepositoryError) as caught:
        repo.store_flows(batch)
    assert caught.value.category == "validation"
    assert server_rows(cloud, [item["event_uuid"] for item in batch]) == (0, 0, 0)
