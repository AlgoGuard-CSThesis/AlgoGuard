from uuid import uuid4

import pytest

from cloud_auth import CloudAuth
from cloud_outbox import Outbox
from cloud_repository import RepositoryError
from cloud_sync import SyncWorker
from tests.integration.test_atomic_flows import event

pytestmark = pytest.mark.integration


def test_lost_acknowledgement_restart_and_revocation(
    cloud, scope, local_stack, tmp_path, monkeypatch
):
    actor = cloud["users"][1]
    auth = CloudAuth(local_stack["api_url"], local_stack["publishable_key"], scope["nodes"][0])
    identity = auth.authenticate_access(actor["token"])
    repo = auth.repository(identity)
    spool = Outbox(tmp_path / "outbox")
    spool.start()
    payload = event(scope)
    original = repo.store_flows
    attempts = []

    def lose_ack(events):
        receipts = original(events)
        attempts.append(receipts)
        if len(attempts) == 1:
            raise RepositoryError("transient")
        return receipts

    monkeypatch.setattr(repo, "store_flows", lose_ack)
    monkeypatch.setattr(auth, "repository", lambda value: repo)
    try:
        spool.submit(repo.context, "capture", payload)
        assert spool.drain()
        worker = SyncWorker(auth, spool)
        worker.sync_once(identity)
        assert spool.counts(repo.context)["pending"] == 1
        assert str(identity.user_id) in worker.retry
        assert (
            cloud["sql"](
                "select count(*) from public.ingest_event where event_uuid=%s",
                (payload["event_uuid"],),
            )[0][0]
            == 1
        )
        spool.close()
        spool = Outbox(tmp_path / "outbox")
        spool.start()
        worker = SyncWorker(auth, spool)
        worker.sync_once(identity)
        assert attempts[-1][0].replayed
        assert spool.counts(repo.context)["synced"] == 1
        spool.submit(repo.context, "capture", event(scope))
        assert spool.drain()
        cloud["sql"](
            "update public.node_membership set status='revoked',is_default=false "
            "where profile_id=%s",
            (actor["profile"],),
        )
        worker.last_authorization.clear()
        worker.sync_once(identity)
        assert worker.blocked[str(identity.user_id)] == "permission"
        # Uploads stop, but the user stays signed in to see the actionable
        # status and export the records, which remain pending, not rejected.
        assert identity.active
        exported = spool.export_pending(repo.context)
        assert len(exported) == 1 and exported[0]["status"] == "pending"
    finally:
        spool.close()
        cloud["sql"](
            "update public.node_membership set status='approved' where profile_id=%s",
            (actor["profile"],),
        )


def test_capture_terminal_state_and_late_summary(cloud, scope):
    admin, actor, _ = cloud["users"]
    cloud["sql"](
        "update public.model_deployment set is_active=1 where deployment_id=%s",
        (scope["deployment"],),
    )
    cloud["sql"](
        "insert into public.model_manifest(deployment_id,model_id,object_path,object_sha256,"
        "object_bytes,workflow_version,feature_schema_version,python_version,dependency_versions,"
        "status,activated_at,created_by) values(%s,%s,%s,%s,1,'fixture','fixture','fixture','{}',"
        "'active',now(),%s)",
        (
            scope["deployment"],
            scope["model"],
            cloud["prefix"] + str(uuid4()),
            "a" * 64,
            admin["profile"],
        ),
    )
    capture_uuid = str(uuid4())
    args = dict(
        p_capture_uuid=capture_uuid,
        p_node_id=scope["nodes"][0],
        p_profile_id=actor["profile"],
        p_deployment_id=scope["deployment"],
        p_source="csv",
    )
    first = cloud["rpc"]("open_cloud_capture", args, actor)
    assert first.status_code == 200
    assert cloud["rpc"]("open_cloud_capture", args, actor).json() == first.json()
    capture_id = first.json()["capture_id"]
    summary = {"p_capture_id": capture_id, "p_values": {"status": "stopped", "flows_emitted": 10}}
    closed = cloud["rpc"]("finalize_cloud_capture", summary, actor)
    assert closed.status_code == 200 and closed.json()["status"] == "stopped"
    summary["p_values"] = {"status": "completed", "flows_emitted": 20}
    assert cloud["rpc"]("finalize_cloud_capture", summary, actor).json()["status"] == "stopped"
    result = cloud["request"](
        "PATCH",
        "/rest/v1/capture_session",
        actor,
        params={"capture_id": "eq." + capture_id},
        json={"status": "running", "closed_at": None},
    )
    assert result.status_code == 400
    assert (
        cloud["sql"](
            "select flows_emitted from public.capture_session where capture_id=%s", (capture_id,)
        )[0][0]
        == 20
    )
