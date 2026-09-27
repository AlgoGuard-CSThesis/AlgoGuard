import copy
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from cloud_repository import CloudRepository, RepositoryError, UserNodeContext

pytestmark = pytest.mark.integration


def event(scope, *, alert=True):
    result = {
        "event_uuid": str(uuid.uuid4()),
        "event_time": "2026-09-26T01:02:03Z",
        "model_id": scope["model"],
        "deployment_id": scope["deployment"],
        "traffic": {
            "source_ip": "10.0.0.1",
            "destination_port": 443,
            "packet_size": 1024,
            "feature_payload": '{"duration":1.25}',
        },
        "prediction": {"predicted_label": "Attack", "confidence_score": 94.5, "latency_ms": 12},
    }
    if alert:
        result["alert"] = {"severity_level": "High", "description": "fixture alert"}
    return result


def repository(cloud, scope, local_stack, user_index=1, node_index=0):
    actor = cloud["users"][user_index]
    return CloudRepository(
        local_stack["api_url"],
        local_stack["publishable_key"],
        UserNodeContext(
            uuid.UUID(actor["id"]),
            actor["profile"],
            uuid.UUID(scope["nodes"][node_index]),
            actor["token"],
        ),
    )


def counts(cloud, profile):
    result = []
    for table in ("network_traffic", "prediction", "alert", "ingest_event", "system_log"):
        owner = "profile_id" if table == "system_log" else "owner_profile_id"
        result.append(
            cloud["sql"](f"select count(*) from public.{table} where {owner}=%s", (profile,))[0][0]
        )
    return result


def test_atomic_flow_lost_acknowledgement_and_changed_content(cloud, scope, local_stack):
    repo = repository(cloud, scope, local_stack)
    actor = cloud["users"][1]
    before = counts(cloud, actor["profile"])
    payload = event(scope)
    first = repo.store_flows([payload])[0]
    assert first.persistence == "committed" and not first.replayed and first.alert_id is not None
    again = repo.store_flows([copy.deepcopy(payload)])[0]
    assert again.replayed and again.prediction_id == first.prediction_id
    assert counts(cloud, actor["profile"]) == [value + 1 for value in before]
    payload["traffic"]["source_ip"] = "different"
    with pytest.raises(RepositoryError) as caught:
        repo.store_flows([payload])
    assert caught.value.category == "conflict"
    assert counts(cloud, actor["profile"]) == [value + 1 for value in before]
    no_alert = repo.store_flows([event(scope, alert=False)])[0]
    assert no_alert.alert_id is None
    statistics = repo.statistics()
    assert statistics is not None and statistics["total_flows"] >= 2
    assert repo.traffic_sources()


def test_batch_failure_rolls_back_every_group(cloud, scope, local_stack):
    repo = repository(cloud, scope, local_stack)
    profile = cloud["users"][1]["profile"]
    before = counts(cloud, profile)
    valid = event(scope)
    invalid = event(scope)
    invalid["model_id"] = 9223372036854775806
    with pytest.raises(RepositoryError) as caught:
        repo.store_flows([valid, invalid])
    assert caught.value.category == "validation"
    assert counts(cloud, profile) == before
    conflict = copy.deepcopy(valid)
    conflict["prediction"]["confidence_score"] = 1
    with pytest.raises(RepositoryError) as caught:
        repo.store_flows([valid, conflict])
    assert caught.value.category == "conflict"
    assert counts(cloud, profile) == before


def test_concurrent_retry_and_forged_context(cloud, scope, local_stack):
    actor = cloud["users"][1]
    payload = event(scope)
    before = counts(cloud, actor["profile"])
    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = list(
            pool.map(
                lambda _: repository(cloud, scope, local_stack).store_flows([payload])[0], range(2)
            )
        )
    assert receipts[0].prediction_id == receipts[1].prediction_id
    assert sorted(receipt.replayed for receipt in receipts) == [False, True]
    assert counts(cloud, actor["profile"]) == [value + 1 for value in before]
    for owner, node in (
        (cloud["users"][2]["profile"], scope["nodes"][0]),
        (actor["profile"], scope["nodes"][1]),
    ):
        forged = cloud["rpc"](
            "store_flow_batch",
            {"p_profile_id": owner, "p_node_id": node, "p_events": [event(scope)]},
            actor,
        )
        assert forged.status_code == 403
    # An authorized user on another node cannot reuse a hidden event UUID.
    with pytest.raises(RepositoryError) as caught:
        repository(cloud, scope, local_stack, 2, 1).store_flows([payload])
    assert caught.value.category == "conflict"


def test_event_anchor_refuses_partial_groups_and_tampering(cloud, scope, local_stack):
    actor = cloud["users"][1]
    repo = repository(cloud, scope, local_stack)
    receipt = repo.store_flows([event(scope)])[0]
    rows = cloud["request"](
        "GET",
        "/rest/v1/ingest_event",
        actor,
        params={"event_uuid": "eq." + str(receipt.event_uuid)},
    ).json()
    assert len(rows) == 1
    for forged in (
        {**rows[0], "event_uuid": str(uuid.uuid4()), "traffic_id": None},
        {**rows[0], "event_uuid": str(uuid.uuid4()), "audit_log_id": None},
        {**rows[0], "event_uuid": str(uuid.uuid4()), "content_sha256": "0" * 64},
    ):
        result = cloud["request"]("POST", "/rest/v1/ingest_event", actor, json=forged)
        assert result.status_code in (400, 409)
    for method in ("PATCH", "DELETE"):
        result = cloud["request"](
            method,
            "/rest/v1/ingest_event",
            actor,
            params={"event_uuid": "eq." + str(receipt.event_uuid)},
            **({"json": {"content_sha256": "0" * 64}} if method == "PATCH" else {}),
        )
        assert result.status_code == 403
    foreign = cloud["request"](
        "GET",
        "/rest/v1/ingest_event",
        cloud["users"][2],
        params={"event_uuid": "eq." + str(receipt.event_uuid)},
    )
    assert foreign.status_code == 200 and foreign.json() == []


@pytest.mark.parametrize(
    "change",
    [
        {"event_time": "bad"},
        {"event_time": "2026-09-26T00:00:00"},
        {"prediction": {"predicted_label": "Unknown"}},
        {"prediction": {"predicted_label": "Attack", "confidence_score": 101}},
        {"traffic": {"source_port": 65536}},
        {"traffic": {"packet_size": -1}},
        {"owner_profile_id": 1},
    ],
)
def test_server_validation_rolls_back(cloud, scope, local_stack, change):
    with pytest.raises(RepositoryError) as caught:
        repository(cloud, scope, local_stack).store_flows([{**event(scope), **change}])
    assert caught.value.category == "validation"


def test_repository_pagination_and_bigint_results(cloud, scope, local_stack):
    repo = repository(cloud, scope, local_stack)
    repo.store_flows([event(scope), event(scope)])
    first = repo.list_records("prediction", page_size=1)
    assert first.next_offset is not None
    second = repo.list_records("prediction", page_size=1, offset=first.next_offset)
    assert first.rows[0]["prediction_id"] != second.rows[0]["prediction_id"]
    # Exercise actual values beyond JavaScript's exact-integer range.
    with_sequence = cloud["sql"]("select last_value from public.network_traffic_traffic_id_seq")[0][
        0
    ]
    cloud["sql"]("select setval('public.network_traffic_traffic_id_seq',9007199254740993,true)")
    try:
        receipt = repo.store_flows([event(scope, alert=False)])[0]
        assert receipt.traffic_id == 9007199254740994
    finally:
        cloud["sql"](
            "select setval('public.network_traffic_traffic_id_seq',%s,true)", (with_sequence,)
        )
