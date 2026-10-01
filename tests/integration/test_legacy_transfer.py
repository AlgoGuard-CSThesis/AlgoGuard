"""5E rehearsal through real Auth/Data API/Storage plus a separate recovery database."""

import json
import shutil
import subprocess
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4

import pytest
from stack_support import connect_local_database

from cloud_outbox import Outbox
from cloud_recovery import (
    ModelObjects,
    export_cloud,
    restore_cloud,
    restore_outbox,
    verify_recovery,
)
from cloud_repository import CloudRepository, RepositoryError, UserNodeContext
from legacy_backup import TABLE_KEYS, TransferError, backup, restore_legacy
from legacy_import import TARGETS, import_bundle, set_write_gate
from tests.legacy_support import BIG_ID, STAMP, make_legacy

from . import test_cloud_app_stack
from .test_atomic_flows import event

psycopg2 = pytest.importorskip("psycopg2")
pytestmark = pytest.mark.integration
published = test_cloud_app_stack.published


@pytest.fixture
def installations(cloud, local_stack, tmp_path, monkeypatch):
    from psycopg2 import sql

    sources = []
    mapped_auth_ids = []
    conn = connect_local_database(psycopg2.connect, local_stack["db_url"])

    def create(identity_map=None):
        source_id, node_id = uuid4(), uuid4()
        for auth_id in (identity_map or {}).values():
            mapped_auth_ids.append(str(UUID(str(auth_id))))
            cloud["sql"]("insert into auth.users(id) values(%s)", (str(auth_id),))
        root = tmp_path / str(source_id)
        database = make_legacy(root, monkeypatch)
        bundle = tmp_path / (str(source_id) + "-backup")
        manifest = backup(database, bundle, source_id, root)
        sources.append((source_id, node_id))
        cloud["sql"](
            "insert into public.node(node_id,display_name,status,approved_at,approved_by) "
            "values(%s,%s,'approved',now(),%s)",
            (str(node_id), cloud["prefix"] + "_legacy", cloud["users"][0]["profile"]),
        )
        for actor in cloud["users"][:2]:
            cloud["sql"](
                "insert into public.node_membership(node_id,profile_id,status,decided_at,"
                "decided_by) values(%s,%s,'approved',now(),%s)",
                (str(node_id), actor["profile"], cloud["users"][0]["profile"]),
            )
        return source_id, node_id, bundle, manifest

    try:
        yield conn, create
    finally:
        conn.rollback()
        for source_id, node_id in sources:
            with conn, conn.cursor() as cursor:
                cursor.execute(
                    "select source_table,target_key from private.legacy_record where source_id=%s",
                    (str(source_id),),
                )
                receipts = cursor.fetchall()
                # Remove post-cutover test writes before the imported reference chain.
                cursor.execute("delete from public.ingest_event where node_id=%s", (str(node_id),))
                cursor.execute("delete from public.system_log where node_id=%s", (str(node_id),))
                cursor.execute(
                    "delete from public.report_alert where report_id in "
                    "(select report_id from public.report where node_id=%s)",
                    (str(node_id),),
                )
                for table in (
                    "report",
                    "alert",
                    "prediction",
                    "network_traffic",
                    "capture_session",
                ):
                    cursor.execute(f"delete from public.{table} where node_id=%s", (str(node_id),))
                for table in reversed(TABLE_KEYS):
                    for original_table, keys in receipts:
                        if original_table != table:
                            continue
                        cursor.execute(
                            sql.SQL("delete from public.{} where {}").format(
                                sql.Identifier(TARGETS[table]),
                                sql.SQL(" and ").join(
                                    sql.SQL("{}=%s").format(sql.Identifier(k)) for k in keys
                                ),
                            ),
                            tuple(keys.values()),
                        )
                cursor.execute(
                    "delete from public.profile where legacy_source_id=%s", (str(source_id),)
                )
                cursor.execute(
                    "delete from private.legacy_record where source_id=%s", (str(source_id),)
                )
                cursor.execute(
                    "delete from private.legacy_source where source_id=%s", (str(source_id),)
                )
                cursor.execute(
                    "delete from public.node_membership where node_id=%s", (str(node_id),)
                )
                cursor.execute("delete from public.node where node_id=%s", (str(node_id),))
        conn.close()
        for auth_id in mapped_auth_ids:
            cloud["sql"]("delete from auth.users where id=%s", (auth_id,))


def repo(cloud, local_stack, node_id, index=1):
    actor = cloud["users"][index]
    return CloudRepository(
        local_stack["api_url"],
        local_stack["publishable_key"],
        UserNodeContext(UUID(actor["id"]), actor["profile"], node_id, actor["token"]),
    )


def test_collision_safe_import_preserves_evidence_and_hides_unresolved(
    installations,
    cloud,
    local_stack,
):
    conn, create = installations
    imported_ids = []
    for _ in range(2):
        source_id, node_id, bundle, manifest = create()
        result = import_bundle(conn, bundle, node_id)
        assert result["counts"] == manifest["counts"] and result["unresolved"] >= 4
        assert import_bundle(conn, bundle, node_id)["replayed"]
        rows = cloud["sql"](
            "select original_record,target_key from private.legacy_record "
            "where source_id=%s and source_table='network_traffic'",
            (str(source_id),),
        )
        assert {r[0]["traffic_id"] for r in rows} == {BIG_ID + 1, BIG_ID + 2}
        imported_ids.append({r[1]["traffic_id"] for r in rows})
        ledger = cloud["sql"](
            "select original_record from private.legacy_record where source_id=%s",
            (str(source_id),),
        )
        assert "password_hash" not in json.dumps(ledger)
        profiles = cloud["sql"](
            "select auth_user_id,is_active,legacy_username from public.profile "
            "where legacy_source_id=%s",
            (str(source_id),),
        )
        assert all(r[0] is None and not r[1] for r in profiles)
        assert "historical analyst" in {r[2] for r in profiles}
        analyst = repo(cloud, local_stack, node_id)
        admin = repo(cloud, local_stack, node_id, 0)
        assert len(analyst.list_records("prediction").rows) == 1
        assert len(admin.list_records("prediction").rows) == 2
        assert not repo(cloud, local_stack, node_id, 2).list_records("prediction").rows
        predictions = admin.list_records("prediction").rows
        assert {p["confidence_score"] for p in predictions} == {94.5}
        assert {p["prediction_timestamp"] for p in predictions} == {STAMP}
        assert not analyst.list_records("report").rows
        reports = admin.list_records("report").rows
        assert len(reports) == 1
        assert (
            len(
                admin.list_records(
                    "report_alert", filters={"report_id": reports[0]["report_id"]}
                ).rows
            )
            == 2
        )
        assert "Prediction" in admin.log_filter_options()["modules"]
        assert not analyst.node_access()  # Import does not switch on cloud writes.
        for index, expected in ((0, sum(manifest["counts"].values())), (1, 0)):
            review = cloud["request"](
                "GET",
                "/rest/v1/legacy_import_review",
                cloud["users"][index],
                params={"source_id": "eq." + str(source_id)},
            )
            assert review.status_code == 200
            assert len(review.json()) == expected
        assert not cloud["sql"](
            "select 1 from public.model_deployment d join "
            "private.legacy_record r on r.target_table='model_deployment' "
            "and d.deployment_id=(r.target_key->>'deployment_id')::bigint "
            "where r.source_id=%s and d.is_active=1",
            (str(source_id),),
        )
    assert imported_ids[0].isdisjoint(imported_ids[1])


def test_failed_import_rolls_back_and_the_same_snapshot_can_resume(
    installations,
    cloud,
    local_stack,
    monkeypatch,
    tmp_path,
):
    import legacy_import

    conn, create = installations
    source_id, node_id, bundle, _ = create()
    original = legacy_import._insert

    def fail(cursor, table, values):
        if table == "prediction":
            raise TransferError("Injected interruption")
        return original(cursor, table, values)

    with monkeypatch.context() as patch:
        patch.setattr(legacy_import, "_insert", fail)
        with pytest.raises(TransferError, match="interruption"):
            import_bundle(conn, bundle, node_id)
    assert not cloud["sql"](
        "select 1 from private.legacy_source where source_id=%s", (str(source_id),)
    )
    assert not cloud["sql"](
        "select 1 from public.profile where legacy_source_id=%s", (str(source_id),)
    )
    assert not repo(cloud, local_stack, node_id).node_access()
    assert restore_legacy(bundle, tmp_path / "pre-write-restore").is_file()
    assert not import_bundle(conn, bundle, node_id)["replayed"]
    with pytest.raises(TransferError):
        import_bundle(conn, bundle, uuid4())


def test_explicit_fresh_auth_mapping_never_copies_roles(installations, cloud):
    conn, create = installations
    auth_id = uuid4()
    source_id, node_id, bundle, _ = create({1: auth_id})
    import_bundle(conn, bundle, node_id, {1: auth_id})
    profile = cloud["sql"](
        "select profile_id,is_active,legacy_username from public.profile "
        "where legacy_source_id=%s and legacy_admin_id=1",
        (str(source_id),),
    )[0]
    assert profile[1:] == (True, "historical analyst")
    assert not cloud["sql"]("select 1 from public.user_role where profile_id=%s", (profile[0],))
    assert not cloud["sql"](
        "select 1 from public.node_membership where profile_id=%s", (profile[0],)
    )


@pytest.fixture
def recovery_database(local_stack):
    """Schema-only clone in a uniquely named database; source data is never reset."""
    from psycopg2 import sql

    if not shutil.which("docker"):
        pytest.skip("Docker is required for the schema-clone recovery rehearsal.")
    control = connect_local_database(psycopg2.connect, local_stack["db_url"])
    control.autocommit = True
    name = "zz_migration_smoke_recovery_" + uuid4().hex
    with control.cursor() as cursor:
        cursor.execute(
            sql.SQL("create database {} template template0").format(sql.Identifier(name))
        )
    target = None
    try:
        dumped = subprocess.run(
            [
                "docker",
                "exec",
                "supabase_db_AlgoGuard",
                "pg_dump",
                "-U",
                "postgres",
                "-d",
                "postgres",
                "--schema-only",
                "--no-publications",
                "--no-subscriptions",
                "--no-owner",
                "--no-acl",
                "--schema=public",
                "--schema=private",
                "--schema=auth",
                "--schema=storage",
                "--schema=extensions",
            ],
            capture_output=True,
            timeout=60,
        )
        assert dumped.returncode == 0, "Could not export local schema for recovery rehearsal."
        schema = dumped.stdout.replace(
            b"CREATE SCHEMA extensions;",
            b"CREATE SCHEMA extensions;\nCREATE EXTENSION pgcrypto WITH SCHEMA extensions;",
        )
        restored = subprocess.run(
            [
                "docker",
                "exec",
                "-i",
                "supabase_db_AlgoGuard",
                "psql",
                "-U",
                "postgres",
                "-d",
                name,
                "-v",
                "ON_ERROR_STOP=1",
            ],
            input=b"DROP SCHEMA public;\n" + schema,
            capture_output=True,
            timeout=60,
        )
        assert restored.returncode == 0, restored.stderr.decode(errors="replace")[-2000:]
        parts = urlsplit(local_stack["db_url"])
        target = connect_local_database(
            psycopg2.connect, urlunsplit(parts._replace(path="/" + name))
        )
        with target, target.cursor() as cursor:
            cursor.execute("insert into public.deployment_activation_lock(lock_id) values(true)")
        yield target
    finally:
        if target:
            target.close()
        with control.cursor() as cursor:
            cursor.execute(sql.SQL("drop database {} with (force)").format(sql.Identifier(name)))
        control.close()


def test_post_cutover_forward_recovery_retains_new_writes_artifacts_and_pending_events(
    installations,
    cloud,
    local_stack,
    published,
    recovery_database,
    tmp_path,
):
    conn, create = installations
    source_id, node_id, bundle, _ = create()
    import_bundle(conn, bundle, node_id)
    actor = repo(cloud, local_stack, node_id)
    flow = event({"model": published["model_id"], "deployment": published["deployment_id"]})
    with pytest.raises(RepositoryError, match="permission"):
        actor.store_flows([flow])
    set_write_gate(conn, source_id, enabled=True)
    capture_id = actor.open_capture(uuid4(), published["deployment_id"], "csv")
    flow["capture_id"] = capture_id
    receipt = actor.store_flows([flow])[0]
    assert actor.store_flows([flow])[0].prediction_id == receipt.prediction_id
    actor.finalize_capture(capture_id, {"status": "completed", "flows_emitted": 1})
    assert receipt.alert_id is not None
    report_id, _ = actor.create_report(uuid4(), [receipt.alert_id])
    spool = Outbox(tmp_path / "pending")
    try:
        # Simulate a lost acknowledgement: the durable client copy remains pending.
        spool.submit(actor.context, "manual", flow, capped=False)
        spool.start()
        assert spool.drain()
        spool.save_lifecycle(
            actor.context, "pending-summary", {"capture_id": capture_id, "status": "completed"}
        )
        set_write_gate(conn, source_id, enabled=False)
        assert not actor.node_access()
        objects = ModelObjects(local_stack["api_url"], local_stack["secret_key"])
        folder = tmp_path / "recovery"
        exported = export_cloud(
            conn, folder, objects, legacy_bundles=[bundle], outboxes=[spool.directory]
        )
        _, rows = verify_recovery(folder)
        assert str(receipt.event_uuid) in {row["event_uuid"] for row in rows["public.ingest_event"]}
        assert report_id in {row["report_id"] for row in rows["public.report"]}
        # A compatible recovery database needs the original Auth UUIDs; credentials
        # are restored by the identity provider, never from the business export.
        with recovery_database, recovery_database.cursor() as cursor:
            for profile in rows["public.profile"]:
                if profile["auth_user_id"]:
                    cursor.execute(
                        "insert into auth.users(id) values(%s) on conflict do nothing",
                        (profile["auth_user_id"],),
                    )
        artifact_directory = tmp_path / "recovered-model-objects"

        class RecoveryObjects:
            def ensure(self, path, content):
                destination = artifact_directory / path
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(content)

        result = restore_cloud(recovery_database, folder, RecoveryObjects())
        assert result["counts"] == exported["counts"]
        assert restore_cloud(recovery_database, folder, RecoveryObjects())["replayed"]
        assert (artifact_directory / published["object_path"]).read_bytes() == objects.get(
            published["object_path"]
        )
        restore_outbox(folder, 0, tmp_path / "restored-pending")
        recovered_spool = Outbox(tmp_path / "restored-pending")
        try:
            assert recovered_spool.pending(actor.context) == [flow]
            assert recovered_spool.pending_summaries(actor.context)[0][0] == "pending-summary"
            # Exercise restored deduplication in the isolated database. Real Auth/RLS
            # authorization is checked through the source stack's API above.
            set_write_gate(recovery_database, source_id, enabled=True)
            with recovery_database, recovery_database.cursor() as cursor:
                cursor.execute("select count(*) from public.prediction")
                before = cursor.fetchone()[0]
                cursor.execute(
                    "select set_config('request.jwt.claims',%s,true)",
                    (json.dumps({"sub": str(actor.context.user_id), "role": "authenticated"}),),
                )
                cursor.execute(
                    "select public.store_flow_batch(%s,%s,%s::jsonb)",
                    (str(node_id), actor.context.profile_id, json.dumps([flow])),
                )
                replay = cursor.fetchone()[0][0]
                assert replay["replayed"] and int(replay["prediction_id"]) == receipt.prediction_id
                cursor.execute("select count(*) from public.prediction")
                assert cursor.fetchone()[0] == before
                cursor.execute(
                    "select prediction_id from public.ingest_event where event_uuid=%s",
                    (str(receipt.event_uuid),),
                )
                assert cursor.fetchone()[0] == receipt.prediction_id
            recovered_spool.acknowledge(actor.context, [receipt])
            assert not recovered_spool.pending(actor.context)
            with recovery_database, recovery_database.cursor() as cursor:
                cursor.execute("update public.alert set description='Changed after restore'")
            with pytest.raises(TransferError, match="different application data"):
                restore_cloud(recovery_database, folder, RecoveryObjects())
        finally:
            recovered_spool.close()
    finally:
        spool.close()
