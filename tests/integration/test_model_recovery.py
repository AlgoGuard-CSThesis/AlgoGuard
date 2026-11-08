"""Stage 6C.3 on the local stack: model updates under failure, permission attacks, cleanup of
unreferenced uploads, and backup coverage of every application table.

Maintenance credentials publish fixtures and observe results; every attack uses a real user's
token. The offline counterparts (interrupted promotion, corrupt and oversized downloads,
incompatible manifests, cache pruning) are in ``tests/test_model_delivery.py``.
"""

import hashlib
import time
import uuid
from datetime import timedelta

import pytest
from stack_support import connect_local_database

import cleanup_model_objects
from cloud_recovery import RECOVERY_EXCLUDED, RECOVERY_TABLES, ModelObjects
from cloud_repository import CloudRepository, UserNodeContext
from model_delivery import ModelCache
from model_runtime import ModelDeliveryError
from tests.cloud_support import monitor_until, post_json, wait_for

from .app_fixtures import make_account, publish_fixture_model, sign_in, start_app

psycopg2 = pytest.importorskip("psycopg2")
pytestmark = pytest.mark.integration


def analyst_repo(cloud, scope, local_stack, index=1, node=0):
    actor = cloud["users"][index]
    return CloudRepository(local_stack["api_url"], local_stack["publishable_key"],
                           UserNodeContext(uuid.UUID(actor["id"]), actor["profile"],
                                           uuid.UUID(scope["nodes"][node]), actor["token"]))


def object_status(local_http, local_stack, cloud, path):
    return local_http.get(local_stack["api_url"] + "/storage/v1/object/models/" + path,
                          headers=cloud["maintenance"]).status_code


def test_every_application_table_is_backed_up_or_deliberately_excluded(cloud):
    tables = {row[0] for row in cloud["sql"](
        "select table_schema||'.'||table_name from information_schema.tables "
        "where table_schema in ('public','private') and table_type='BASE TABLE'")}
    assert tables == set(RECOVERY_TABLES) | set(RECOVERY_EXCLUDED)


def test_manifest_and_model_object_attacks_are_refused(cloud, scope, local_stack, local_http,
                                                       published):
    path = published["object_path"]
    original = local_http.get(local_stack["api_url"] + "/storage/v1/object/models/" + path,
                              headers=cloud["maintenance"]).content
    for actor in cloud["users"][:2]:  # an administrator account is not a maintainer either
        inserted = cloud["request"]("POST", "/rest/v1/model_manifest", actor, json={
            "deployment_id": scope["deployment"], "model_id": scope["model"],
            "object_path": f"releases/{uuid.uuid4()}/{'b' * 64}.joblib",
            "object_sha256": "b" * 64, "object_bytes": 1, "workflow_version": "x",
            "feature_schema_version": "x", "python_version": "x", "dependency_versions": "{}",
            "status": "active"})
        assert inserted.status_code in (401, 403)
        changed = cloud["request"]("PATCH", "/rest/v1/model_manifest", actor,
                                   params={"manifest_id": f"eq.{published['manifest_id']}"},
                                   json={"object_sha256": "c" * 64, "status": "superseded"})
        assert changed.status_code in (401, 403) or changed.json() == []
        activated = cloud["request"]("PATCH", "/rest/v1/model_deployment", actor,
                                     params={"deployment_id": f"eq.{scope['deployment']}"},
                                     json={"is_active": 1})
        assert activated.status_code in (401, 403) or activated.json() == []
        assert cloud["rpc"]("activate_model_publication",
                            {"p_publication_id": str(published["publication_id"])},
                            actor).status_code in (401, 403, 404)
        overwrite = local_http.post(
            local_stack["api_url"] + "/storage/v1/object/models/" + path, data=b"replacement",
            headers={"apikey": local_stack["publishable_key"], "x-upsert": "true",
                     "Authorization": "Bearer " + actor["token"]})
        assert overwrite.status_code in (400, 403)
        replaced = cloud["request"]("PUT", "/storage/v1/object/models/" + path, actor,
                                    data=b"replacement bytes")
        assert replaced.status_code in (400, 403)
        cloud["request"]("DELETE", "/storage/v1/object/models", actor, json={"prefixes": [path]})
    row = cloud["sql"]("select status,object_sha256 from public.model_manifest "
                       "where manifest_id=%s", (published["manifest_id"],))[0]
    assert row == ("active", published["object_sha256"])
    assert cloud["sql"]("select is_active from public.model_deployment where deployment_id=%s",
                        (scope["deployment"],))[0][0] == 0
    after = local_http.get(local_stack["api_url"] + "/storage/v1/object/models/" + path,
                           headers=cloud["maintenance"]).content
    assert after == original and hashlib.sha256(after).hexdigest() == published["object_sha256"]


def test_an_expired_download_link_is_refused(cloud, scope, local_stack, published):
    actor = cloud["users"][1]
    signed = cloud["request"]("POST", "/storage/v1/object/sign/models/"
                              + published["object_path"], actor, json={"expiresIn": 1})
    assert signed.status_code == 200
    link = local_stack["api_url"] + "/storage/v1" + signed.json()["signedURL"]
    time.sleep(2.5)
    import requests

    with requests.Session() as session:
        session.trust_env = False
        assert session.get(link, timeout=10).status_code in (400, 403)


def test_a_missing_model_file_fails_safely_and_recovers_when_restored(
        cloud, scope, local_stack, local_http, tmp_path, stacking_artifact_bytes):
    manifest = publish_fixture_model(cloud, local_stack, tmp_path, stacking_artifact_bytes)
    repo = analyst_repo(cloud, scope, local_stack)
    pinned = ModelCache(repo, tmp_path / "running").load_active()
    content = (pinned.cache_path).read_bytes()
    removed = local_http.delete(local_stack["api_url"] + "/storage/v1/object/models",
                                headers=cloud["maintenance"],
                                json={"prefixes": [manifest["object_path"]]})
    assert removed.status_code == 200
    try:
        with pytest.raises(ModelDeliveryError, match="unavailable. Tell your administrator"):
            ModelCache(repo, tmp_path / "fresh").load_active()
        assert list((tmp_path / "fresh").glob("*")) == []  # nothing promoted or half-written
        # A capture that already pinned the model keeps it in memory and on disk.
        assert pinned.artifact["feature_columns"]
        again = ModelCache(repo, tmp_path / "running").load_active()
        assert again.cache_path == pinned.cache_path
    finally:
        # Restoring the same verified bytes makes the release downloadable again.
        ModelObjects(local_stack["api_url"], local_stack["secret_key"]).ensure(
            manifest["object_path"], content)
    restored = ModelCache(repo, tmp_path / "restored").load_active()
    assert restored.manifest["manifest_id"] == manifest["manifest_id"]
    assert hashlib.sha256(restored.cache_path.read_bytes()).hexdigest() == \
        manifest["object_sha256"]


def test_a_running_capture_keeps_its_model_while_new_sessions_use_the_new_one(
        cloud, local_stack, local_http, published, tmp_path, monkeypatch,
        stacking_artifact_bytes):
    from services import live_monitor_service

    monkeypatch.setitem(live_monitor_service.SPEED_CHOICES, "fast", 0.02)
    analyst = make_account(cloud, local_stack, local_http, "pinned")
    env = start_app(cloud, local_stack, tmp_path / "pc", [analyst])
    newer = None
    try:
        client = sign_in(env.app, analyst)
        started = post_json(client, "/monitor/start", {"source_type": "csv", "speed": "fast",
                                                       "persist": "all"})
        assert started.status_code == 200, started.get_json()
        first = monitor_until(client, lambda data: data["session"]["totals"]["flows"] >= 5,
                              timeout=60)["session"]
        old = first["deployment"]["deployment_id"]
        newer = publish_fixture_model(cloud, local_stack, tmp_path, stacking_artifact_bytes)
        assert newer["deployment_id"] != old
        assert cloud["sql"]("select count(*) from public.model_manifest "
                            "where status='active'")[0][0] == 1
        flows = first["totals"]["flows"]
        monitor_until(client, lambda data: data["session"]["totals"]["flows"] >= flows + 20,
                      timeout=60)
        stopped = post_json(client, "/monitor/stop").get_json()["session"]
        capture = stopped["capture_id"]
        queued = stopped["totals"]["queued"]
        wait_for(lambda: cloud["sql"]("select count(*) from public.ingest_event "
                                      "where capture_id=%s", (capture,))[0][0] == queued,
                 timeout=60)
        # Every event of the running capture, before and after the activation, kept its model.
        assert cloud["sql"]("select array_agg(distinct deployment_id) from public.ingest_event "
                            "where capture_id=%s", (capture,))[0][0] == [old]
        assert cloud["sql"]("select deployment_id from public.capture_session "
                            "where capture_id=%s", (capture,))[0][0] == old
        # A new session resolves the current manifest.
        # (Right after a stop, the previous capture may still be releasing its resources.)
        wait_for(lambda: post_json(client, "/monitor/start", {
            "source_type": "csv", "speed": "fast", "persist": "none"}).status_code == 200,
            timeout=30, interval=0.5)
        running = monitor_until(client, lambda data: data["session"]["state"] == "running",
                                timeout=60)["session"]
        assert running["deployment"]["deployment_id"] == newer["deployment_id"]
        post_json(client, "/monitor/stop")
    finally:
        env.services.close()
        if newer is not None:
            local_http.delete(local_stack["api_url"] + "/storage/v1/object/models",
                              headers=cloud["maintenance"],
                              json={"prefixes": [newer["object_path"]]})


def test_cleanup_removes_only_proven_unreferenced_uploads(cloud, scope, local_stack, local_http,
                                                          published):
    run = uuid.uuid4().hex[:8]

    def upload(label, days_old):
        content = f"{label} {run}".encode()
        path = f"releases/{uuid.uuid4()}/{hashlib.sha256(content).hexdigest()}.joblib"
        response = local_http.post(local_stack["api_url"] + "/storage/v1/object/models/" + path,
                                   headers=cloud["maintenance"], data=content)
        assert response.status_code == 200
        if days_old:
            cloud["sql"]("update storage.objects set created_at=now()-%s where bucket_id='models' "
                         "and name=%s", (timedelta(days=days_old), path))
        return path, content

    def manifest(path, content, status, days_old):
        cloud["sql"](
            "insert into public.model_manifest(deployment_id,model_id,object_path,object_sha256,"
            "object_bytes,workflow_version,feature_schema_version,python_version,"
            "dependency_versions,status,activated_at,superseded_at,created_by,created_at) "
            "values(%s,%s,%s,%s,%s,'fixture','fixture','fixture','{}',%s,%s,%s,%s,now()-%s)",
            (scope["deployment"], scope["model"], path, hashlib.sha256(content).hexdigest(),
             len(content), status, "2026-01-01" if status == "superseded" else None,
             "2026-01-02" if status == "superseded" else None, cloud["users"][0]["profile"],
             timedelta(days=days_old)))

    orphaned_old = upload("orphaned old", 3)
    manifest(*orphaned_old, "orphaned", 3)
    draft_recent = upload("draft recent", 3)
    manifest(*draft_recent, "draft", 0)  # a publication that may still be running
    stray_old = upload("stray old", 3)
    stray_recent = upload("stray recent", 0)
    superseded_old = upload("superseded old", 3)
    manifest(*superseded_old, "superseded", 3)  # a rollback artifact
    raced = upload("raced", 3)
    manifest(*raced, "orphaned", 3)
    ours = {orphaned_old[0], draft_recent[0], stray_old[0], stray_recent[0], superseded_old[0],
            raced[0], published["object_path"]}
    connection = connect_local_database(psycopg2.connect, local_stack["db_url"])
    try:
        candidates = {item["object_path"]: item
                      for item in cleanup_model_objects.plan(connection, timedelta(hours=1))}
        assert set(candidates) & ours == {orphaned_old[0], stray_old[0], raced[0]}
        assert candidates[orphaned_old[0]]["manifest_statuses"] == ["orphaned"]
        assert candidates[stray_old[0]]["manifest_statuses"] == []
        # The cleanup plans with this list (this module's objects only, since the local bucket
        # is shared), then a retried publication claims one of them before its removal.
        first_plan = [item for item in candidates.values() if item["object_path"] in ours]
        cloud["sql"]("update public.model_manifest set status='published' where object_path=%s",
                     (raced[0],))
        original_plan = cleanup_model_objects.plan

        def staged(conn, grace, name=None):
            return first_plan if name is None else original_plan(conn, grace, name)

        cleanup_model_objects.plan = staged
        try:
            removed, kept = cleanup_model_objects.clean(
                connection, local_stack["api_url"], local_stack["secret_key"],
                timedelta(hours=1))
        finally:
            cleanup_model_objects.plan = original_plan
        assert {item["object_path"] for item in removed} == {orphaned_old[0], stray_old[0]}
        assert {item["object_path"] for item in kept} == {raced[0]}  # re-checked, then kept
    finally:
        connection.close()
    for path in (orphaned_old[0], stray_old[0]):
        assert object_status(local_http, local_stack, cloud, path) in (400, 404)
    for path in (draft_recent[0], stray_recent[0], superseded_old[0], raced[0],
                 published["object_path"]):
        assert object_status(local_http, local_stack, cloud, path) == 200
    # Manifest rows are records; the orphaned one stays, pointing at a removed object.
    assert cloud["sql"]("select status from public.model_manifest where object_path=%s",
                        (orphaned_old[0],)) == [("orphaned",)]
    local_http.delete(local_stack["api_url"] + "/storage/v1/object/models",
                      headers=cloud["maintenance"],
                      json={"prefixes": [draft_recent[0], stray_recent[0], superseded_old[0],
                                         raced[0]]})
    cloud["sql"]("delete from public.model_manifest where object_path=any(%s)",
                 ([orphaned_old[0], draft_recent[0], superseded_old[0], raced[0]],))
