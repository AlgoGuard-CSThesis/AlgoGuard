import os
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

TEST_RUNTIME = Path(tempfile.mkdtemp(prefix="algoguard-tests-"))
# Tests use their own configuration source and paths, even when the developer's
# shell or .env selects a different admin, quality gate, or cloud mode.
for name in list(os.environ):
    if name.startswith("ALGOGUARD_") or name in (
        "FLASK_DEBUG", "NEXT_PUBLIC_SUPABASE_URL", "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY"
    ):
        os.environ.pop(name)
os.environ["ALGOGUARD_DATABASE_PATH"] = str(TEST_RUNTIME / "test.sqlite3")
os.environ["ALGOGUARD_DEPLOYED_MODEL_PATH"] = str(TEST_RUNTIME / "models" / "deployed_model.joblib")
os.environ["ALGOGUARD_SAVED_MODEL_FOLDER"] = str(TEST_RUNTIME / "models")
os.environ["ALGOGUARD_REPORT_FOLDER"] = str(TEST_RUNTIME / "reports")
os.environ["ALGOGUARD_CAPTURE_FOLDER"] = str(TEST_RUNTIME / "captures")
os.environ["ALGOGUARD_SECRET_KEY"] = "test-secret"
os.environ["ALGOGUARD_ADMIN_PASSWORD"] = "admin123"

import config  # noqa: E402
from config import reset_config_cache  # noqa: E402

config.ENV_FILE = TEST_RUNTIME / ".env"
reset_config_cache()


@pytest.fixture(autouse=True)
def reset_config_between_tests():
    """Ensure env-based configuration is fresh for each test case."""
    reset_config_cache()
    yield
    reset_config_cache()


@pytest.fixture(scope="session")
def app_module(tmp_path_factory):
    import app as module

    runtime = tmp_path_factory.mktemp("flask-runtime")
    module.app.config.update(
        TESTING=True,
        SAVED_MODEL_FOLDER=str(runtime / "models"),
    )
    Path(module.app.config["SAVED_MODEL_FOLDER"]).mkdir(parents=True, exist_ok=True)
    return module


@pytest.fixture(autouse=True)
def stop_live_monitor_sessions():
    """Never let a monitoring thread outlive the test that started it."""
    from services.live_monitor_service import reset_for_tests

    yield
    reset_for_tests()


@pytest.fixture()
def client(app_module):
    return app_module.app.test_client()


@pytest.fixture()
def authenticated_client(client):
    client.get("/login")
    with client.session_transaction() as test_session:
        login_token = test_session["_csrf_token"]
    response = client.post(
        "/login",
        data={
            "username": "admin",
            "password": "admin123",
            "_csrf_token": login_token,
        },
    )
    assert response.status_code == 302

    original_post = client.post

    def csrf_post(*args, **kwargs):
        with client.session_transaction() as test_session:
            token = test_session.get("_csrf_token", "")
        headers = dict(kwargs.pop("headers", {}) or {})
        headers.setdefault("X-CSRF-Token", token)
        return original_post(*args, headers=headers, **kwargs)

    client.post = csrf_post
    return client


@pytest.fixture(scope="session")
def binary_dataframe():
    rng = np.random.default_rng(42)
    rows_per_class = 48
    normal = pd.DataFrame(
        {
            "dur": rng.normal(1.0, 0.15, rows_per_class),
            "rate": rng.normal(30, 4, rows_per_class),
            "sbytes": rng.normal(500, 60, rows_per_class),
            "dbytes": rng.normal(300, 40, rows_per_class),
            "proto": rng.choice(["tcp", "udp"], rows_per_class),
            "state": rng.choice(["CON", "FIN"], rows_per_class),
            "label": "Normal",
        }
    )
    attack = pd.DataFrame(
        {
            "dur": rng.normal(5.5, 0.25, rows_per_class),
            "rate": rng.normal(900, 45, rows_per_class),
            "sbytes": rng.normal(9000, 400, rows_per_class),
            "dbytes": rng.normal(4000, 200, rows_per_class),
            "proto": rng.choice(["tcp", "icmp"], rows_per_class),
            "state": rng.choice(["REQ", "INT"], rows_per_class),
            "label": "Attack",
        }
    )
    return (
        pd.concat([normal, attack], ignore_index=True)
        .sample(
            frac=1,
            random_state=42,
        )
        .reset_index(drop=True)
    )


@pytest.fixture(scope="session")
def prepared_dataset(binary_dataframe, tmp_path_factory):
    from services.preprocessing_service import prepare_dataset

    csv_path = tmp_path_factory.mktemp("fixtures") / "binary_traffic.csv"
    binary_dataframe.to_csv(csv_path, index=False)
    return prepare_dataset(csv_path)


@pytest.fixture(scope="session")
def trained_bundle(app_module, binary_dataframe, prepared_dataset, tmp_path_factory):
    from services import database_service as db
    from services.training_service import train_and_compare_models

    run_id = db.create_training_run(1, "binary_traffic.csv", "binary_traffic.csv")
    db.update_training_run(
        run_id,
        row_count=prepared_dataset.summary["rows"],
        feature_count=prepared_dataset.summary["features"],
        normal_count=prepared_dataset.summary["normal_count"],
        anomaly_count=prepared_dataset.summary["anomaly_count"],
        target_column=prepared_dataset.summary["target_column"],
        class_distribution=prepared_dataset.summary["label_counts"],
        validation_status="passed",
        preprocessing_status="completed",
        training_status="in_progress",
    )

    def event_logger(module, action, status, model_name=None, message=None):
        db.log_system_event(
            1,
            module,
            action,
            status,
            "127.0.0.1",
            message=message,
            run_id=run_id,
            dataset_filename="binary_traffic.csv",
            model_name=model_name,
        )

    model_folder = tmp_path_factory.mktemp("trained-models")
    progress_events = []

    def progress_callback(completed, total, model_name, status):
        progress_events.append((completed, total, model_name, status))

    result = train_and_compare_models(
        prepared_dataset,
        str(model_folder),
        run_id,
        event_logger=event_logger,
        progress_callback=progress_callback,
    )
    db.save_training_results(run_id, result["model_results"], result["best_model"])
    db.log_system_event(
        1,
        "Dataset",
        "upload_completed",
        "Success",
        "127.0.0.1",
        message="Fixture upload completed.",
        run_id=run_id,
        dataset_filename="binary_traffic.csv",
    )
    return {
        "run_id": run_id,
        "result": result,
        "frame": binary_dataframe,
        "prepared": prepared_dataset,
        "progress_events": progress_events,
    }
