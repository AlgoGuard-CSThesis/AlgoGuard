
import pytest

pytestmark = pytest.mark.integration


def insert(cloud, actor, table, body):
    result = cloud["request"]("POST", "/rest/v1/" + table, actor, json=body)
    assert result.status_code == 201, result.json()
    return result.json()[0]


def flow_group(cloud, scope, actor, node):
    common = {"node_id": node, "owner_profile_id": actor["profile"]}
    traffic = insert(
        cloud,
        actor,
        "network_traffic",
        {**common, "timestamp": "2026-09-26 00:00:00", "source_ip": "PRIVATE_ENDPOINT"},
    )
    prediction = insert(
        cloud,
        actor,
        "prediction",
        {
            **common,
            "traffic_id": traffic["traffic_id"],
            "model_id": scope["model"],
            "deployment_id": scope["deployment"],
            "predicted_label": "Attack",
            "prediction_timestamp": "2026-09-26 00:00:00",
        },
    )
    alert = insert(
        cloud,
        actor,
        "alert",
        {
            **common,
            "prediction_id": prediction["prediction_id"],
            "severity_level": "High",
            "alert_status": "Open",
            "detected_at": "2026-09-26 00:00:00",
            "description": "PRIVATE_ALERT_TEXT",
        },
    )
    return traffic, prediction, alert


def test_real_api_crud_and_administrator_write_scope(cloud, scope):
    admin, a, b = cloud["users"]
    node_a, node_b = scope["nodes"]
    traffic_b, prediction_b, alert_b = flow_group(cloud, scope, b, node_b)
    traffic_a, prediction_a, alert_a = flow_group(cloud, scope, a, node_a)
    records = [
        ("network_traffic", "traffic_id", traffic_b, {"source_ip": "changed"}),
        ("prediction", "prediction_id", prediction_b, {"predicted_label": "Normal"}),
        ("alert", "alert_id", alert_b, {"alert_status": "Closed"}),
    ]
    for table, key, record, update in records:
        query = {key: "eq." + str(record[key])}
        hidden = cloud["request"]("GET", "/rest/v1/" + table, a, params=query)
        assert hidden.status_code == 200 and hidden.json() == []
        observed = cloud["request"]("GET", "/rest/v1/" + table, admin, params=query)
        assert observed.status_code == 200 and len(observed.json()) == 1
        for actor in (a, admin):
            for method, body in (("PATCH", update), ("DELETE", None)):
                response = cloud["request"](
                    method,
                    "/rest/v1/" + table,
                    actor,
                    params=query,
                    **({"json": body} if body else {}),
                )
                assert response.status_code == 403 or response.json() == []
                assert "PRIVATE_" not in response.text
        for actor, body in (
            (a, {"node_id": node_b, "owner_profile_id": a["profile"]}),
            (admin, {"node_id": node_b, "owner_profile_id": admin["profile"]}),
            (a, {"node_id": node_a, "owner_profile_id": b["profile"]}),
        ):
            forged = {**record, **body}
            forged.pop(key)
            response = cloud["request"]("POST", "/rest/v1/" + table, actor, json=forged)
            assert response.status_code in (403, 409)
    # Positive update and administrator insert on its assigned node.
    assert (
        cloud["request"](
            "PATCH",
            "/rest/v1/alert",
            a,
            params={"alert_id": "eq." + str(alert_a["alert_id"])},
            json={"alert_status": "Closed"},
        ).json()[0]["alert_status"]
        == "Closed"
    )
    insert(
        cloud,
        admin,
        "network_traffic",
        {
            "node_id": node_a,
            "owner_profile_id": admin["profile"],
            "timestamp": "2026-09-26 00:00:00",
        },
    )
    cross = {
        "node_id": node_a,
        "owner_profile_id": a["profile"],
        "traffic_id": traffic_b["traffic_id"],
        "model_id": scope["model"],
        "predicted_label": "Normal",
        "prediction_timestamp": "now",
    }
    assert cloud["request"]("POST", "/rest/v1/prediction", a, json=cross).status_code == 409


def test_sessions_logs_reports_and_inherited_links(cloud, scope):
    admin, a, b = cloud["users"]
    node_a, node_b = scope["nodes"]
    _, _, alert_b = flow_group(cloud, scope, b, node_b)
    _, _, alert_a = flow_group(cloud, scope, a, node_a)
    for table, key, extra in (
        ("capture_session", "capture_id", {"started_at": "2026-09-26 00:00:00"}),
        (
            "system_log",
            "log_id",
            {
                "module": "test",
                "action": "test",
                "status": "ok",
                "timestamp": "2026-09-26 00:00:00",
            },
        ),
        ("report", "report_id", {"report_type": "test", "generated_at": "2026-09-26 00:00:00"}),
    ):
        owner = "profile_id" if table == "system_log" else "owner_profile_id"
        foreign = insert(cloud, b, table, {"node_id": node_b, owner: b["profile"], **extra})
        own = insert(cloud, a, table, {"node_id": node_a, owner: a["profile"], **extra})
        params = {key: "eq." + str(foreign[key])}
        assert cloud["request"]("GET", "/rest/v1/" + table, a, params=params).json() == []
        assert len(cloud["request"]("GET", "/rest/v1/" + table, admin, params=params).json()) == 1
        for method in ("PATCH", "DELETE"):
            response = cloud["request"](
                method,
                "/rest/v1/" + table,
                a,
                params=params,
                **({"json": extra} if method == "PATCH" else {}),
            )
            assert response.status_code == 403 or response.json() == []
        assert (
            cloud["request"](
                "POST",
                "/rest/v1/" + table,
                a,
                json={"node_id": node_a, owner: b["profile"], **extra},
            ).status_code
            == 403
        )
        if table == "report":
            link = {"report_id": own[key], "alert_id": alert_a["alert_id"], "node_id": node_a}
            insert(cloud, a, "report_alert", link)
            for forged in (
                {**link, "alert_id": alert_b["alert_id"]},
                {**link, "node_id": None},
                {**link, "report_id": foreign[key]},
            ):
                assert cloud["request"](
                    "POST", "/rest/v1/report_alert", a, json=forged
                ).status_code in (403, 409)
            query = {"report_id": "eq." + str(own[key])}
            assert cloud["request"]("GET", "/rest/v1/report_alert", b, params=query).json() == []
            assert (
                cloud["request"]("DELETE", "/rest/v1/report_alert", a, params=query).status_code
                == 200
            )
            assert (
                cloud["request"](
                    "DELETE", "/rest/v1/report", a, params={key: "eq." + str(own[key])}
                ).status_code
                == 200
            )


def test_protected_tables_views_and_revocation(cloud, scope):
    admin, a, b = cloud["users"]
    protected = {
        "profile": {"is_active": False},
        "user_role": {"role": "administrator"},
        "node": {"display_name": "forged"},
        "node_membership": {"status": "approved"},
        "training_run": {"filename": "forged"},
        "detection_model": {"model_name": "forged"},
        "model_deployment": {"is_active": 1},
        "model_manifest": {"status": "active"},
        "deployment_activation_lock": {"lock_id": True},
    }
    for table, body in protected.items():
        for method in ("POST", "PATCH", "DELETE"):
            response = cloud["request"](
                method, "/rest/v1/" + table, a, **({"json": body} if method != "DELETE" else {})
            )
            assert response.status_code in (400, 403, 404)
    promoted = cloud["request"](
        "POST",
        "/rest/v1/user_role",
        a,
        json={"profile_id": a["profile"], "role": "administrator", "granted_by": None},
    )
    assert promoted.status_code == 403
    assert (
        cloud["request"](
            "GET", "/rest/v1/profile", a, params={"profile_id": "eq." + str(b["profile"])}
        ).json()
        == []
    )
    for view in ("training_summary", "model_summary", "deployment_summary"):
        result = cloud["request"]("GET", "/rest/v1/" + view, a)
        assert result.status_code == 200
        assert "PRIVATE_" not in result.text and "artifact_path" not in result.text
    assert (
        cloud["request"](
            "GET", "/rest/v1/detection_model", a, params={"select": "artifact_path"}
        ).status_code
        == 403
    )
    cloud["sql"](
        "update public.node_membership set status='revoked',is_default=false where profile_id=%s",
        (a["profile"],),
    )
    try:
        assert cloud["request"]("GET", "/rest/v1/network_traffic", a).json() == []
        assert (
            cloud["request"](
                "POST",
                "/rest/v1/network_traffic",
                a,
                json={
                    "node_id": scope["nodes"][0],
                    "owner_profile_id": a["profile"],
                    "timestamp": "now",
                },
            ).status_code
            == 403
        )
    finally:
        cloud["sql"](
            "update public.node_membership set status='approved' where profile_id=%s",
            (a["profile"],),
        )


def test_private_storage_requires_readable_published_manifest(
    cloud, scope, local_stack, local_http
):
    admin, a, _ = cloud["users"]
    path = cloud["prefix"] + "/model.joblib"
    endpoint = local_stack["api_url"] + "/storage/v1/object/models/" + path
    content = b"disposable model bytes"
    uploaded = local_http.post(endpoint, headers=cloud["maintenance"], data=content)
    assert uploaded.status_code == 200
    try:
        assert cloud["request"]("GET", "/storage/v1/object/models/" + path, a).status_code in (
            400,
            403,
            404,
        )
        cloud["sql"](
            "insert into public.model_manifest(deployment_id,model_id,object_path,"
            "object_sha256,object_bytes,workflow_version,feature_schema_version,"
            "python_version,dependency_versions,status,created_by) "
            "values(%s,%s,%s,%s,%s,'test','test','3.14','{}','published',%s)",
            (scope["deployment"], scope["model"], path, "a" * 64, len(content), admin["profile"]),
        )
        downloaded = cloud["request"]("GET", "/storage/v1/object/models/" + path, a)
        assert downloaded.status_code == 200 and downloaded.content == content
        assert cloud["request"]("GET", "/storage/v1/object/models/" + path).status_code in (
            400,
            401,
            403,
            404,
        )
        assert cloud["request"](
            "POST", "/storage/v1/object/models/" + path + "-forged", a, data=content
        ).status_code in (400, 403)
        removed = cloud["request"](
            "DELETE", "/storage/v1/object/models", a, json={"prefixes": [path]}
        )
        assert removed.status_code in (200, 400, 403)
        assert cloud["request"]("GET", "/storage/v1/object/models/" + path, a).status_code == 200
        cloud["sql"](
            "update public.model_manifest set status='draft' where object_path=%s", (path,)
        )
        assert cloud["request"]("GET", "/storage/v1/object/models/" + path, a).status_code in (
            400,
            403,
            404,
        )
    finally:
        local_http.delete(
            local_stack["api_url"] + "/storage/v1/object/models",
            headers=cloud["maintenance"],
            json={"prefixes": [path]},
        )
