import json
import sqlite3

import joblib
import pytest
from werkzeug.security import check_password_hash

from services import database_service as db
from services.deployment_service import (
    STACKING_QUALITY_THRESHOLDS,
    DeploymentError,
    deploy_model,
    stacking_deployment_eligibility,
)
from services.model_registry import MODEL_IDS, MODEL_WORKFLOW_VERSION
from services.simulation_service import run_simulation


def test_database_context_manager_closes_connection():
    with db.get_connection() as connection:
        connection.execute("SELECT 1").fetchone()

    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")


def test_first_admin_gets_random_one_time_password(monkeypatch, tmp_path, capsys):
    database_path = tmp_path / "fresh.sqlite3"
    monkeypatch.setattr(db, "DATABASE_PATH", str(database_path))
    monkeypatch.delenv("ALGOGUARD_ADMIN_PASSWORD", raising=False)

    db.initialize_database()

    output = capsys.readouterr().out
    password_line = next(line for line in output.splitlines() if line.startswith("Password: "))
    generated_password = password_line.removeprefix("Password: ")
    admin = db.get_admin_by_username("admin")
    assert generated_password != "admin123"
    assert len(generated_password) >= 20
    assert check_password_hash(admin["password_hash"], generated_password)


def synthetic_results():
    results = []
    for index, (model_id, model_name) in enumerate(MODEL_IDS.items(), start=1):
        value = 90.0 - index
        normalized = {
            "accuracy": value / 100,
            "precision": value / 100,
            "recall": value / 100,
            "f1_score": value / 100,
            "roc_auc": value / 100,
            "false_positive_rate": value / 100,
            "cpu_usage": value / 100,
            "ram_usage": value / 100,
            "model_size": value / 100,
        }
        comparison_metrics = normalized if index <= 5 else None
        results.append(
            {
                "model_id": model_id,
                "model_name": model_name,
                "model_type": "individual" if index <= 5 else "ensemble",
                "status": "completed",
                "accuracy": value,
                "precision": value,
                "recall": value,
                "f1_score": value,
                "roc_auc": value,
                "false_positive_rate": 100 - value,
                "cpu_usage": index / 10,
                "ram_usage": float(index),
                "model_size": index / 20,
                "processing_time": index / 10,
                "predicted_normal_count": 10,
                "predicted_anomaly_count": 10,
                "normalized_metrics": comparison_metrics,
                "overall_score": (
                    sum(comparison_metrics.values()) / 9 if comparison_metrics else None
                ),
                "rank": index if index <= 5 else None,
                "model_path": None,
            }
        )
    return {"model_results": results, "best_model": results[0]}


def model_id_for(run_id, model_name):
    with db.get_connection() as connection:
        row = connection.execute(
            "SELECT model_id FROM detection_model WHERE run_id = ? AND model_name = ?",
            (run_id, model_name),
        ).fetchone()
        assert row is not None
        return row["model_id"]


def test_synthetic_results_cover_every_registered_model():
    results = synthetic_results()["model_results"]
    assert len(results) == len(MODEL_IDS)
    assert sum(1 for result in results if result["model_type"] == "ensemble") == 1


def test_top_individual_cannot_be_deployed(trained_bundle):
    best_name = trained_bundle["result"]["best_model"]["model_name"]
    model_id = model_id_for(trained_bundle["run_id"], best_name)

    with pytest.raises(DeploymentError, match="Only the evaluated Stacking Ensemble"):
        deploy_model(model_id, 1)


def test_eligible_stacking_can_be_deployed(trained_bundle):
    model_id = model_id_for(trained_bundle["run_id"], "Stacking Ensemble")
    deploy_model(model_id, 1)
    active = db.get_active_deployment()
    assert active["model_id"] == model_id
    assert active["model_name"] == "Stacking Ensemble"


def test_failed_activation_restores_previous_artifact(trained_bundle, tmp_path, monkeypatch):
    from services import deployment_service

    active_path = tmp_path / "deployed_model.joblib"
    previous_contents = b"previous-active-artifact"
    active_path.write_bytes(previous_contents)
    model_id = model_id_for(trained_bundle["run_id"], "Stacking Ensemble")

    monkeypatch.setattr(deployment_service, "ACTIVE_MODEL_PATH", str(active_path))

    def fail_record_deployment(*_args, **_kwargs):
        raise RuntimeError("database activation failed")

    monkeypatch.setattr(deployment_service, "record_deployment", fail_record_deployment)

    with pytest.raises(DeploymentError, match="database activation failed"):
        deployment_service.deploy_model(model_id, 1)

    assert active_path.read_bytes() == previous_contents
    assert list(tmp_path.glob("*.tmp")) == []
    assert list(tmp_path.glob("*.backup")) == []
    assert list(tmp_path.glob("*.joblib")) == [active_path]


def test_active_artifact_with_invalid_format_fails_safely(tmp_path, monkeypatch):
    from services import deployment_service

    artifact_path = tmp_path / "invalid-active.joblib"
    joblib.dump(["not", "an", "artifact"], artifact_path)
    monkeypatch.setattr(
        deployment_service,
        "get_active_deployment",
        lambda: {
            "model_id": 999,
            "model_name": "Stacking Ensemble",
            "artifact_path": str(artifact_path),
        },
    )

    with pytest.raises(DeploymentError, match="invalid format"):
        deployment_service.load_active_artifact()


def test_deployed_stacking_can_predict(trained_bundle):
    model_id = model_id_for(trained_bundle["run_id"], "Stacking Ensemble")
    deploy_model(model_id, 1)
    result = run_simulation(trained_bundle["prepared"].feature_defaults)
    assert result["prediction"] in {"Normal", "Attack"}
    assert 0 <= result["confidence"] <= 100
    assert result["deployed_model_name"] == "Stacking Ensemble"


def test_stacking_quality_gate_blocks_low_results(monkeypatch):
    monkeypatch.setitem(STACKING_QUALITY_THRESHOLDS, "accuracy", 70.0)
    eligible, reason = stacking_deployment_eligibility(
        {
            "model_name": "Stacking Ensemble",
            "version": MODEL_WORKFLOW_VERSION,
            "run_training_status": "completed",
            "evaluation_status": "completed",
            "accuracy": 69.0,
            "f1_score": 80.0,
            "roc_auc": 80.0,
        }
    )
    assert eligible is False
    assert "Accuracy 69.00% is below 70.00%" in reason


def test_stacking_quality_gate_blocks_legacy_workflow():
    eligible, reason = stacking_deployment_eligibility(
        {
            "model_name": "Stacking Ensemble",
            "version": "legacy-workflow",
            "evaluation_status": "completed",
            "accuracy": 100.0,
            "f1_score": 100.0,
            "roc_auc": 100.0,
        }
    )
    assert eligible is False
    assert "legacy workflow" in reason


def test_stacking_quality_gate_blocks_failed_training_run():
    eligible, reason = stacking_deployment_eligibility(
        {
            "model_name": "Stacking Ensemble",
            "version": MODEL_WORKFLOW_VERSION,
            "run_training_status": "failed",
            "evaluation_status": "completed",
            "accuracy": 100.0,
            "f1_score": 100.0,
            "roc_auc": 100.0,
        }
    )
    assert eligible is False
    assert "training run must complete successfully" in reason


def test_dashboard_reports_detection_activity(authenticated_client, trained_bundle):
    deploy_stacking(trained_bundle)
    authenticated_client.post("/predict", json=payload_for_prediction(trained_bundle, 1))
    stats = db.get_detection_stats()
    response = authenticated_client.get("/")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert stats["total_flows"] >= 1
    assert stats["attack_count"] >= 1
    assert "Deployed Model" in html
    assert "Stacking Ensemble" in html
    assert "Flows Analyzed" in html
    assert "Model Comparison Snapshot" not in html


def payload_for_prediction(trained_bundle, expected_class):
    model_row = next(
        result
        for result in trained_bundle["result"]["model_results"]
        if result["model_name"] == "Stacking Ensemble"
    )
    pipeline = joblib.load(model_row["model_path"])["pipeline"]
    features = trained_bundle["frame"].iloc[:, :-1]
    predictions = pipeline.predict(features)
    index = next(index for index, value in enumerate(predictions) if int(value) == expected_class)
    return json.loads(features.iloc[index].to_json())


def deploy_stacking(trained_bundle):
    model_id = model_id_for(trained_bundle["run_id"], "Stacking Ensemble")
    deploy_model(model_id, 1)


def test_attack_prediction_creates_alert_and_logs(authenticated_client, trained_bundle):
    deploy_stacking(trained_bundle)
    before_alerts = len(db.list_alerts(limit=1000))
    response = authenticated_client.post(
        "/predict",
        json=payload_for_prediction(trained_bundle, 1),
    )
    payload = response.get_json()
    assert response.status_code == 200
    assert payload["prediction"] == "Attack"
    assert payload["alert_created"] is True
    assert len(db.list_alerts(limit=1000)) == before_alerts + 1
    logs = db.list_system_logs({"search": "classified as Attack"})
    assert logs["total"] >= 1


def test_normal_prediction_does_not_create_alert(authenticated_client, trained_bundle):
    deploy_stacking(trained_bundle)
    before_alerts = len(db.list_alerts(limit=1000))
    response = authenticated_client.post(
        "/predict",
        json=payload_for_prediction(trained_bundle, 0),
    )
    payload = response.get_json()
    assert response.status_code == 200
    assert payload["prediction"] == "Normal"
    assert payload["alert_created"] is False
    assert len(db.list_alerts(limit=1000)) == before_alerts


def reject_nonfinite_json(value):
    raise AssertionError(f"Response contains invalid JSON number: {value}")


@pytest.mark.parametrize("field", ["dur", "sbytes", "dbytes"])
@pytest.mark.parametrize("missing", [None, ""])
def test_missing_numeric_prediction_is_imputed_and_saved_as_null(
    authenticated_client, trained_bundle, field, missing
):
    deploy_stacking(trained_bundle)
    response = authenticated_client.post("/predict", json={field: missing})

    assert response.status_code == 200
    result = json.loads(response.get_data(as_text=True), parse_constant=reject_nonfinite_json)
    assert result["prediction"] in {"Normal", "Attack"}
    assert result["flow_data"][field] is None
    with db.get_connection() as connection:
        saved = connection.execute(
            """SELECT nt.feature_payload, p.input_payload, nt.packet_size
               FROM prediction p JOIN network_traffic nt ON nt.traffic_id = p.traffic_id
               WHERE p.prediction_id = ?""",
            (result["prediction_id"],),
        ).fetchone()
    assert saved["packet_size"] >= 0
    for column in ("feature_payload", "input_payload"):
        payload = json.loads(saved[column], parse_constant=reject_nonfinite_json)
        assert payload[field] is None


@pytest.mark.parametrize("invalid", ["NaN", "Infinity", "-Infinity", float("nan"), float("inf")])
def test_nonfinite_prediction_input_is_rejected_without_storing(
    authenticated_client, trained_bundle, invalid
):
    deploy_stacking(trained_bundle)
    before = db.get_detection_stats()["total_flows"]

    response = authenticated_client.post("/predict", json={"sbytes": invalid})

    assert response.status_code == 400
    assert "sbytes must contain a finite numerical value" in response.get_json()["message"]
    assert db.get_detection_stats()["total_flows"] == before


@pytest.mark.parametrize("field", ["sbytes", "dbytes"])
def test_large_finite_prediction_values_preserve_the_flow_without_integer_overflow(
    authenticated_client, trained_bundle, field
):
    deploy_stacking(trained_bundle)
    response = authenticated_client.post("/predict", json={field: 1e20})

    assert response.status_code == 200, response.get_json()
    result = response.get_json()
    with db.get_connection() as connection:
        saved = connection.execute(
            """SELECT nt.packet_size, nt.feature_payload FROM prediction p
               JOIN network_traffic nt ON nt.traffic_id = p.traffic_id
               WHERE p.prediction_id = ?""",
            (result["prediction_id"],),
        ).fetchone()
    assert saved["packet_size"] is None
    assert json.loads(saved["feature_payload"])[field] == 1e20


def test_classified_flow_transaction_rolls_back_if_alert_write_fails(
    monkeypatch,
    trained_bundle,
):
    model_id = model_id_for(trained_bundle["run_id"], "Stacking Ensemble")
    with db.get_connection() as connection:
        before = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("network_traffic", "prediction", "alert")
        }

    def fail_alert_write(*args, **kwargs):
        raise sqlite3.DatabaseError("simulated alert failure")

    monkeypatch.setattr(db, "_insert_alert", fail_alert_write)
    with pytest.raises(sqlite3.DatabaseError, match="simulated alert failure"):
        db.store_classified_flow(
            {"source_ip": "192.0.2.1", "destination_ip": "198.51.100.2"},
            "transaction_test",
            model_id,
            "Attack",
            99.0,
            create_alert=True,
        )

    with db.get_connection() as connection:
        after = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("network_traffic", "prediction", "alert")
        }
    assert after == before


def test_system_logs_ui_loads_and_filters(authenticated_client, trained_bundle):
    db.log_system_event(
        1,
        "QA Unique Module",
        "qa_filter_event",
        "Success",
        "127.0.0.1",
        message="Unique searchable audit event.",
        run_id=trained_bundle["run_id"],
        model_name="Random Forest",
    )
    response = authenticated_client.get(
        "/logs?module=QA+Unique+Module&status=Success&model=Random+Forest&search=Unique+searchable"
    )
    assert response.status_code == 200
    assert b"Unique searchable audit event" in response.data
    assert b"System Logs" in response.data


def test_required_log_categories_are_recorded(authenticated_client, trained_bundle):
    from train import _deploy_stacking

    assert _deploy_stacking(trained_bundle["run_id"], 1) is True
    # Training events can be on older pages after other tests create predictions.
    modules = set(db.get_log_filter_options()["modules"])
    assert {"Authentication", "Dataset", "Training", "Deployment"}.issubset(modules)


def test_authenticated_flask_pages_smoke(authenticated_client, trained_bundle):
    deploy_stacking(trained_bundle)
    paths = [
        "/",
        "/monitor",
        "/simulation",
        "/alerts",
        "/logs",
        "/admins",
    ]
    assert all(authenticated_client.get(path).status_code == 200 for path in paths)


def test_removed_training_routes_are_gone(authenticated_client):
    for path in ("/upload", "/history", "/results", "/runs/1"):
        assert authenticated_client.get(path).status_code == 404
