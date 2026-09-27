"""Stage 5D repository queries and SQL functions through the real Data API.

The offline fake cannot prove PostgREST syntax (quoted logic trees, ``in.()``,
descending order) or the new functions' RLS behaviour; these tests do.
"""

import uuid

import pytest

from cloud_repository import RepositoryError
from tests.integration.test_atomic_flows import event, repository

pytestmark = pytest.mark.integration


def activate(cloud, scope):
    admin = cloud["users"][0]
    cloud["sql"]("update public.model_deployment set is_active=1 where deployment_id=%s",
                 (scope["deployment"],))
    if not cloud["sql"]("select 1 from public.model_manifest where deployment_id=%s",
                        (scope["deployment"],)):
        cloud["sql"](
            "insert into public.model_manifest(deployment_id,model_id,object_path,object_sha256,"
            "object_bytes,workflow_version,feature_schema_version,python_version,"
            "dependency_versions,status,activated_at,created_by) values(%s,%s,%s,%s,1,"
            "'fixture','fixture','fixture','{}','active',now(),%s)",
            (scope["deployment"], scope["model"],
             f"releases/{uuid.uuid4()}/{'b' * 64}.joblib", "b" * 64, admin["profile"]),
        )


def test_new_repository_queries_parse_in_postgrest(cloud, scope, local_stack):
    repo = repository(cloud, scope, local_stack)
    stored = repo.store_flows([event(scope), event(scope), event(scope, alert=False)])
    alert_ids = [receipt.alert_id for receipt in stored if receipt.alert_id]
    newest = repo.list_records("alert_detail", descending=True, page_size=2)
    assert [row["alert_id"] for row in newest.rows] == sorted(alert_ids, reverse=True)
    assert newest.rows[0]["source_ip"] == "10.0.0.1"
    listed = repo.list_records("alert_detail", filters={"alert_id": alert_ids})
    assert sorted(row["alert_id"] for row in listed.rows) == sorted(alert_ids)
    stamp = cloud["sql"]("select min(\"timestamp\"),max(\"timestamp\") from public.system_log "
                         "where profile_id=%s", (cloud["users"][1]["profile"],))[0]
    ranged = repo.list_records("system_log", between=(stamp[0], stamp[1]), descending=True)
    assert ranged.rows and all(stamp[0] <= row["timestamp"] <= stamp[1] for row in ranged.rows)
    assert repo.list_records("system_log", between=("2000-01-01 00:00:00",
                                                    "2000-01-01 23:59:59")).rows == ()
    assert repo.list_records("system_log", search="flow_accep").rows
    # Reserved PostgREST characters inside a search are data, never syntax.
    for hostile in ('a,b)', 'x"y', "back\\slash", "*.(),:"):
        assert repo.list_records("system_log", search=hostile).rows == ()


def test_audit_entries_are_idempotent_scoped_and_filterable(cloud, scope, local_stack):
    repo = repository(cloud, scope, local_stack)
    other = repository(cloud, scope, local_stack, user_index=2, node_index=1)
    entry = {"module": "Live Monitor", "action": "monitor_started", "status": "Success",
             "message": "integration", "model_name": "Stacking Ensemble",
             "timestamp": "2026-09-27 01:02:03"}
    event_uuid = str(uuid.uuid4())
    body = {"p_node_id": str(repo.context.node_id), "p_profile_id": repo.context.profile_id,
            "p_event_uuid": event_uuid, "p_values": entry}
    first = repo.rpc("append_system_log", body)
    assert first["replayed"] is False
    assert repo.rpc("append_system_log", body) == {**first, "replayed": True}
    with pytest.raises(RepositoryError, match="conflict"):
        repo.rpc("append_system_log", {**body, "p_values": {**entry, "status": "Failed"}})
    with pytest.raises(RepositoryError, match="conflict"):
        other.rpc("append_system_log", {**body, "p_node_id": str(other.context.node_id),
                                        "p_profile_id": other.context.profile_id})
    with pytest.raises(RepositoryError, match="permission"):
        repo.rpc("append_system_log", {**body, "p_event_uuid": str(uuid.uuid4()),
                                       "p_node_id": str(other.context.node_id)})
    options = repo.log_filter_options()
    assert "Live Monitor" in options["modules"] and "Stacking Ensemble" in options["models"]
    hidden = other.rpc("system_log_filter_options", {"p_node_id": str(repo.context.node_id)})
    assert hidden == {"modules": [], "statuses": [], "models": []}
    assert other.list_records("system_log", filters={"module": "Live Monitor"},
                              all_visible_nodes=True).rows == ()


def test_capture_lifecycle_and_reports_through_the_repository(cloud, scope, local_stack):
    activate(cloud, scope)
    repo = repository(cloud, scope, local_stack)
    other = repository(cloud, scope, local_stack, user_index=2, node_index=1)
    capture_uuid = uuid.uuid4()
    capture_id = repo.open_capture(capture_uuid, scope["deployment"], "csv")
    assert repo.open_capture(capture_uuid, scope["deployment"], "csv") == capture_id
    with pytest.raises(RepositoryError, match="conflict"):
        repo.open_capture(capture_uuid, scope["deployment"], "pcap")
    flows = [dict(event(scope), capture_id=capture_id) for _ in range(2)]
    receipts = repo.store_flows(flows)
    assert repo.finalize_capture(capture_id, {"status": "stopped", "flows_emitted": 2})[
        "status"] == "stopped"
    assert repo.finalize_capture(capture_id, {"status": "completed", "flows_emitted": 5})[
        "status"] == "stopped"
    with pytest.raises(RepositoryError):
        other.finalize_capture(capture_id, {"status": "error"})
    alerts = [receipt.alert_id for receipt in receipts]
    request_id = uuid.uuid4()
    report_id, replayed = repo.create_report(request_id, alerts)
    assert replayed is False
    assert repo.create_report(request_id, alerts) == (report_id, True)
    with pytest.raises(RepositoryError, match="conflict"):
        repo.create_report(request_id, alerts[:1])
    with pytest.raises(RepositoryError):
        other.create_report(uuid.uuid4(), alerts)
    links = repo.list_records("report_alert", filters={"report_id": report_id})
    assert sorted(row["alert_id"] for row in links.rows) == sorted(alerts)
    assert other.list_records("report", filters={"report_id": report_id},
                              all_visible_nodes=True).rows == ()
    assert repo.node_access() is True
    assert repository(cloud, scope, local_stack, user_index=2, node_index=0).node_access() is False
