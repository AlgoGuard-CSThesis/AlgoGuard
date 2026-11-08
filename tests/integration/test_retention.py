"""Stage 6B.3 rehearsal on the local stack: preview, batched cleanup and restore of fixtures."""

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from tests.integration.test_atomic_flows import event, repository

pytestmark = pytest.mark.integration

FLOW_TABLES = (("network_traffic", "traffic_id"), ("prediction", "prediction_id"),
               ("alert", "alert_id"))


def backdate_flow(cloud, receipt, days):
    """Move a stored flow's server receipt times into the past."""
    sql, when = cloud["sql"], f"now() - interval '{days} days'"
    sql(f"update public.network_traffic set recorded_at={when} where traffic_id=%s",
        (int(receipt.traffic_id),))
    sql(f"update public.prediction set created_at={when} where prediction_id=%s",
        (int(receipt.prediction_id),))
    sql(f"update public.alert set created_at={when} where alert_id=%s", (int(receipt.alert_id),))
    sql(f"update public.system_log set created_at={when} where log_id=%s",
        (int(receipt.audit_log_id),))
    sql(f"update public.ingest_event set received_at={when} where event_uuid=%s",
        (str(receipt.event_uuid),))


def report_on(cloud, scope, local_stack, alert_id, days):
    repo = repository(cloud, scope, local_stack)
    report_id, _ = repo.create_report(uuid.uuid4(), [int(alert_id)])
    cloud["sql"](f"update public.report set created_at=now() - interval '{days} days' "
                 "where report_id=%s", (int(report_id),))
    return int(report_id)


def alert_of(receipt):
    assert receipt.alert_id is not None
    return int(receipt.alert_id)


def exists(cloud, table, key, value):
    return bool(cloud["sql"](f"select 1 from public.{table} where {key}=%s", (value,)))


def snapshot(cloud, receipt):
    """The rows of one flow, as a backup would hold them."""
    rows = {}
    for table, key in FLOW_TABLES:
        rows[table] = cloud["sql"](f"select row_to_json(t) from public.{table} t where {key}=%s",
                                   (int(getattr(receipt, key)),))[0][0]
    rows["system_log"] = [row[0] for row in cloud["sql"](
        "select row_to_json(t) from public.system_log t where prediction_id=%s order by log_id",
        (int(receipt.prediction_id),))]
    rows["ingest_event"] = cloud["sql"](
        "select row_to_json(t) from public.ingest_event t where event_uuid=%s",
        (str(receipt.event_uuid),))[0][0]
    return rows


def restore(cloud, rows):
    """Reinsert a flow in dependency order; the event-group trigger re-validates it."""
    def put(table, row):
        cloud["sql"](f"insert into public.{table} select * from "
                     f"jsonb_populate_record(null::public.{table}, %s::jsonb)", (json.dumps(row),))

    for table, _ in FLOW_TABLES[:2]:
        put(table, rows[table])
    put("alert", rows["alert"])
    for log in rows["system_log"]:
        put("system_log", log)
    put("ingest_event", rows["ingest_event"])


def test_retention_preview_cleanup_and_restore_rehearsal(cloud, scope, local_stack):
    sql, admin, analyst = cloud["sql"], cloud["users"][0], cloud["users"][1]
    repo = repository(cloud, scope, local_stack)
    old_plain, held, old_report, recent = (repo.store_flows([event(scope)])[0] for _ in range(4))
    clock_skewed = event(scope)
    clock_skewed["event_time"] = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    skewed = repo.store_flows([clock_skewed])[0]
    for receipt in (old_plain, held, old_report):
        backdate_flow(cloud, receipt, 45)
    backdate_flow(cloud, recent, 5)
    kept_report = report_on(cloud, scope, local_stack, held.alert_id, 10)
    expired_report = report_on(cloud, scope, local_stack, old_report.alert_id, 100)
    node = scope["nodes"][0]
    old_log = sql("insert into public.system_log(node_id,profile_id,module,action,status,"
                  "\"timestamp\",created_at) values(%s,%s,'Live Monitor','fixture','Success',"
                  "'2026-09-01 00:00:00',now()-interval '40 days') returning log_id",
                  (node, analyst["profile"]))[0][0]
    new_log = sql("insert into public.system_log(node_id,profile_id,module,action,status,"
                  "\"timestamp\") values(%s,%s,'Live Monitor','fixture','Success',"
                  "'2026-10-27 00:00:00') returning log_id", (node, analyst["profile"]))[0][0]
    closed = sql("insert into public.capture_session(node_id,owner_profile_id,started_at,status,"
                 "opened_at,closed_at) values(%s,%s,'2026-09-01 00:00:00','completed',"
                 "now()-interval '41 days',now()-interval '40 days') returning capture_id",
                 (node, analyst["profile"]))[0][0]
    active = sql("insert into public.capture_session(node_id,owner_profile_id,started_at,status,"
                 "opened_at) values(%s,%s,'2026-09-01 00:00:00','running',"
                 "now()-interval '40 days') returning capture_id", (node, analyst["profile"]))[0][0]
    backup = snapshot(cloud, old_plain)

    # Only administrators preview or clean up.
    assert cloud["rpc"]("retention_preview", {}, analyst).status_code == 403
    assert cloud["rpc"]("retention_apply", {"p_request_id": str(uuid.uuid4())},
                        analyst).status_code == 403
    for policy in ({"p_operational_days": 7}, {"p_report_days": 30}):
        assert cloud["rpc"]("retention_preview", policy, admin).status_code == 400

    preview = cloud["rpc"]("retention_preview", {}, admin).json()
    assert preview["expiring"]["reports"] >= 1 and preview["expiring"]["flows"] >= 2
    assert preview["kept"]["alerts_held_by_reports"] >= 1
    assert preview["kept"]["active_sessions"] >= 1
    assert preview["suspect_client_times"].get(node, 0) >= 1
    assert preview["capacity"]["database_bytes"] > 0
    # A preview changes nothing.
    assert exists(cloud, "prediction", "prediction_id", int(old_plain.prediction_id))

    request_id = str(uuid.uuid4())
    response = cloud["rpc"]("retention_apply", {"p_request_id": request_id, "p_batch_size": 1},
                            admin)
    assert response.status_code == 200, response.text
    first = response.json()
    # A batch bounds reports, flows and sessions; a flow takes its own log rows with it.
    assert all(first["removed"][key] <= 1
               for key in ("reports", "flows", "alerts", "closed_sessions"))
    replay = cloud["rpc"]("retention_apply", {"p_request_id": request_id, "p_batch_size": 1},
                          admin).json()
    assert replay["replayed"] is True and replay["removed"] == first["removed"]
    conflict = cloud["rpc"]("retention_apply", {"p_request_id": request_id, "p_batch_size": 2},
                            admin)
    assert conflict.status_code == 409
    batches, result = 1, first
    while result["more"] and batches < 200:
        result = cloud["rpc"]("retention_apply", {"p_request_id": str(uuid.uuid4()),
                                                  "p_batch_size": 1}, admin).json()
        batches += 1
    assert result["more"] is False

    gone, kept = int(old_plain.prediction_id), int(held.prediction_id)
    assert not exists(cloud, "prediction", "prediction_id", gone)
    assert not exists(cloud, "network_traffic", "traffic_id", int(old_plain.traffic_id))
    assert not exists(cloud, "ingest_event", "event_uuid", str(old_plain.event_uuid))
    assert not exists(cloud, "prediction", "prediction_id", int(old_report.prediction_id))
    assert not exists(cloud, "report", "report_id", expired_report)
    for receipt in (held, recent, skewed):  # cited by a kept report, or recent
        assert exists(cloud, "alert", "alert_id", alert_of(receipt))
        assert exists(cloud, "ingest_event", "event_uuid", str(receipt.event_uuid))
    assert exists(cloud, "report", "report_id", kept_report)
    assert exists(cloud, "report_alert", "report_id", kept_report)
    assert not exists(cloud, "system_log", "log_id", old_log)
    assert exists(cloud, "system_log", "log_id", new_log)
    assert not exists(cloud, "capture_session", "capture_id", closed)
    assert exists(cloud, "capture_session", "capture_id", active)
    audits = sql("select count(*) from public.system_log where module='Retention' "
                 "and profile_id=%s", (admin["profile"],))[0][0]
    assert audits >= batches

    # Restore rehearsal: the removed flow comes back whole and readable by its node's analyst.
    restore(cloud, backup)
    assert exists(cloud, "ingest_event", "event_uuid", str(old_plain.event_uuid))
    rows = cloud["request"]("GET", "/rest/v1/alert_detail", analyst,
                            params={"alert_id": f"eq.{alert_of(old_plain)}"}).json()
    assert len(rows) == 1 and rows[0]["node_id"] == node
    assert kept  # the held flow's prediction id, kept above
