"""Stage 6C.3: post-cutover recovery repeated with two computers, each holding pending records.

The 5E rehearsal (``test_legacy_transfer.py``) recovered one imported installation. Here two
cloud-native computers each have a capture, one record committed whose acknowledgement was lost,
one never sent, a queued capture summary, and a health report; an administrator's cleanup audit
exists too. The package is exported, restored into a separate database, and each computer's
pending records are replayed there under their own user only.
"""

import json
from uuid import UUID, uuid4

import pytest

from app_version import app_version
from cloud_outbox import Outbox
from cloud_recovery import (
    FORMAT,
    RECOVERY_TABLES,
    ModelObjects,
    export_cloud,
    restore_cloud,
    restore_outbox,
    verify_recovery,
)
from cloud_repository import CloudRepository, UserNodeContext

from .test_atomic_flows import event
from .test_legacy_transfer import recovery_database  # noqa: F401

psycopg2 = pytest.importorskip("psycopg2")
pytestmark = pytest.mark.integration


def replay(database, actor, node, events):
    """store_flow_batch in the recovery database as this user, the way the API would call it."""
    with database, database.cursor() as cursor:
        cursor.execute("select set_config('request.jwt.claims',%s,true)",
                       (json.dumps({"sub": actor["id"], "role": "authenticated"}),))
        cursor.execute("select public.store_flow_batch(%s,%s,%s::jsonb)",
                       (node, actor["profile"], json.dumps(events)))
        return cursor.fetchone()[0]


def test_two_computers_with_pending_records_restore_and_replay_under_their_own_users(
        cloud, scope, local_stack, published, recovery_database, tmp_path):  # noqa: F811
    from stack_support import connect_local_database

    admin = cloud["users"][0]
    model = {"model": published["model_id"], "deployment": published["deployment_id"]}
    computers = []
    for actor, node, label in ((cloud["users"][1], scope["nodes"][0], "first"),
                               (cloud["users"][2], scope["nodes"][1], "second")):
        repo = CloudRepository(local_stack["api_url"], local_stack["publishable_key"],
                               UserNodeContext(UUID(actor["id"]), actor["profile"], UUID(node),
                                               actor["token"]))
        capture = repo.open_capture(uuid4(), published["deployment_id"], "csv")
        committed, unsent = event(model), event(model)
        for item in (committed, unsent):
            item["capture_id"] = capture
        receipt = repo.store_flows([committed])[0]  # its acknowledgement is "lost" below
        repo.report_node_health({"app_version": app_version(), "deployment_id":
                                 published["deployment_id"], "capture_status": "running",
                                 "pending_events": 2, "dropped_events": 0,
                                 "rejected_events": 0})
        spool = Outbox(tmp_path / f"pending-{label}")
        spool.start()
        for item in (committed, unsent):
            spool.submit(repo.context, str(capture), item, capped=False)
        assert spool.drain()
        spool.save_lifecycle(repo.context, str(uuid4()), {"capture_id": capture,
                                                         "status": "stopped",
                                                         "flows_emitted": 2})
        computers.append(dict(actor=actor, node=node, repo=repo, capture=capture, spool=spool,
                              committed=committed, unsent=unsent, receipt=receipt))
    audit_id = str(uuid4())
    cloud["sql"]("insert into private.retention_run(request_id,actor_profile_id,operational_days,"
                 "report_days,batch_size,result) values(%s,%s,30,90,500,%s::jsonb)",
                 (audit_id, admin["profile"], json.dumps({"removed": {"flows": 0}})))
    source = connect_local_database(psycopg2.connect, local_stack["db_url"])
    try:
        for computer in computers:
            computer["spool"].close()
        folder = tmp_path / "recovery"
        exported = export_cloud(source, folder, ModelObjects(local_stack["api_url"],
                                                             local_stack["secret_key"]),
                                outboxes=[computer["spool"].directory for computer in computers])
        summary, rows = verify_recovery(folder)
        assert summary["format"] == FORMAT and set(rows) == set(RECOVERY_TABLES)
        health = {row["node_id"]: row for row in rows["public.node_health"]}
        assert {computer["node"] for computer in computers} <= set(health)
        assert audit_id in {row["request_id"] for row in rows["private.retention_run"]}

        # The recovery database needs the original Auth UUIDs; credentials are never exported.
        with recovery_database, recovery_database.cursor() as cursor:
            for profile in rows["public.profile"]:
                if profile["auth_user_id"]:
                    cursor.execute("insert into auth.users(id) values(%s) on conflict do nothing",
                                   (profile["auth_user_id"],))
        artifacts = tmp_path / "restored-objects"

        class RestoredObjects:
            def ensure(self, path, content):
                destination = artifacts / path
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(content)

        assert restore_cloud(recovery_database, folder, RestoredObjects())["counts"] == \
            exported["counts"]
        assert restore_cloud(recovery_database, folder, RestoredObjects())["replayed"]
        with recovery_database, recovery_database.cursor() as cursor:
            cursor.execute("select row_to_json(h) from public.node_health h "
                           "where node_id=any(%s::uuid[])",
                           ([computer["node"] for computer in computers],))
            restored_health = {row[0]["node_id"]: row[0] for row in cursor.fetchall()}
            cursor.execute("select count(*) from private.retention_run where request_id=%s",
                           (audit_id,))
            assert cursor.fetchone()[0] == 1
        for computer in computers:
            assert restored_health[computer["node"]] == health[computer["node"]]

        first, second = computers
        for index, computer in enumerate(computers):
            restored = tmp_path / f"restored-{index}"
            restore_outbox(folder, index, restored)
            spool = Outbox(restored)
            try:
                pending = spool.pending(computer["repo"].context)
                assert pending == [computer["committed"], computer["unsent"]]
                other = second if computer is first else first
                assert spool.pending(other["repo"].context) == []  # one owner per spool
                # Another user's token can never replay these records.
                with pytest.raises(psycopg2.Error):
                    replay(recovery_database, other["actor"], computer["node"], pending)
                answers = replay(recovery_database, computer["actor"], computer["node"], pending)
                assert answers[0]["replayed"] is True
                assert int(answers[0]["prediction_id"]) == computer["receipt"].prediction_id
                assert answers[1]["replayed"] is False
                summary_id, kind, payload = spool.pending_summaries(computer["repo"].context)[0]
                assert kind == "capture" and payload["capture_id"] == computer["capture"]
            finally:
                spool.close()
        with recovery_database, recovery_database.cursor() as cursor:
            cursor.execute("select owner_profile_id,count(*) from public.ingest_event "
                           "where event_uuid=any(%s::uuid[]) group by 1 order by 1",
                           ([computer[key]["event_uuid"] for computer in computers
                             for key in ("committed", "unsent")],))
            assert cursor.fetchall() == sorted((computer["actor"]["profile"], 2)
                                               for computer in computers)
    finally:
        source.close()
