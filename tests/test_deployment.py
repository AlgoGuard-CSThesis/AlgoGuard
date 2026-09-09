import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import joblib
import pytest

from services import database_service as db
from services import deployment_service as deployment


@pytest.fixture(scope="session")
def deployment_models(trained_bundle, tmp_path_factory):
    """Two distinct persisted model identities using the same fitted test weights."""
    first = next(
        row for row in db.list_model_results(trained_bundle["run_id"])
        if row["model_name"] == "Stacking Ensemble"
    )
    run_id = db.create_training_run(1, "replacement.csv", "replacement.csv")
    results = [dict(row) for row in trained_bundle["result"]["model_results"]]
    stack = next(row for row in results if row["model_name"] == "Stacking Ensemble")
    artifact = joblib.load(stack["model_path"])
    artifact["source_run_id"] = run_id
    path = tmp_path_factory.mktemp("replacement-model") / "stacking.joblib"
    joblib.dump(artifact, path)
    stack["model_path"] = str(path)
    db.save_training_results(run_id, results, trained_bundle["result"]["best_model"])
    second = next(
        row for row in db.list_model_results(run_id) if row["model_name"] == "Stacking Ensemble"
    )
    return first, second


@pytest.mark.parametrize("legacy", [False, True])
def test_readers_keep_working_across_activation(
    deployment_models, tmp_path, monkeypatch, legacy
):
    first, second = deployment_models
    base_path = tmp_path / "deployed_model.joblib"
    monkeypatch.setattr(deployment, "ACTIVE_MODEL_PATH", str(base_path))
    initial = deployment.deploy_model(first["model_id"], 1)
    if legacy:
        shutil.copy2(initial["artifact_path"], base_path)
        db.record_deployment(first["model_id"], first["run_id"], 1, str(base_path))
    previous = db.get_active_deployment()
    original_record = deployment.record_deployment
    reads = []

    def activate(*args):
        # This executes after the new file is published but before the DB switches.
        artifact, active = deployment.load_active_artifact()
        reads.append(active["model_id"])
        assert artifact["database_model_id"] == first["model_id"]
        return original_record(*args)

    monkeypatch.setattr(deployment, "record_deployment", activate)
    replacement = deployment.deploy_model(second["model_id"], 1)
    assert reads == [first["model_id"]]
    assert replacement["artifact_path"] != previous["artifact_path"]
    artifact, active = deployment.load_active_artifact()
    assert artifact["database_model_id"] == active["model_id"] == second["model_id"]

    # A reader may fetch the old DB row and only open its file after activation.
    monkeypatch.setattr(deployment, "get_active_deployment", lambda: previous)
    artifact, active = deployment.load_active_artifact()
    assert artifact["database_model_id"] == active["model_id"] == first["model_id"]


def test_failed_activation_keeps_working_model_and_removes_staged_files(
    deployment_models, tmp_path, monkeypatch
):
    first, second = deployment_models
    monkeypatch.setattr(deployment, "ACTIVE_MODEL_PATH", str(tmp_path / "active.joblib"))
    deployment.deploy_model(first["model_id"], 1)
    previous = db.get_active_deployment()
    files_before = set(tmp_path.iterdir())

    def fail_activation(*args):
        raise RuntimeError("activation failed")

    monkeypatch.setattr(deployment, "record_deployment", fail_activation)
    with pytest.raises(deployment.DeploymentError, match="activation failed"):
        deployment.deploy_model(second["model_id"], 1)

    assert db.get_active_deployment() == previous
    assert set(tmp_path.iterdir()) == files_before
    assert deployment.load_active_artifact()[0]["database_model_id"] == first["model_id"]


def test_independent_writers_preserve_one_active_deployment_and_history(
    deployment_models, tmp_path, monkeypatch
):
    monkeypatch.setattr(deployment, "ACTIVE_MODEL_PATH", str(tmp_path / "active.joblib"))
    barrier = threading.Barrier(2)
    original_record = deployment.record_deployment

    def simultaneous_activation(*args):
        barrier.wait(timeout=10)
        return original_record(*args)

    monkeypatch.setattr(deployment, "record_deployment", simultaneous_activation)
    # Bypass the process lock to exercise separate SQLite connections as CLI writers do.
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(
            lambda row: deployment._deploy_model_unlocked(row["model_id"], 1), deployment_models
        ))

    earlier, later = sorted(results, key=lambda item: item["deployment"]["deployment_id"])
    assert (
        later["deployment"]["previous"]["deployment_id"]
        == earlier["deployment"]["deployment_id"]
    )
    with db.get_connection() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM model_deployment WHERE is_active = 1"
        ).fetchone()[0] == 1
    artifact, active = deployment.load_active_artifact()
    assert artifact["database_model_id"] == active["model_id"] == later["model"]["model_id"]
    assert all(Path(result["artifact_path"]).is_file() for result in results)
