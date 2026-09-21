from pathlib import Path

import pytest

import config
import train
from services import database_service as db
from services import deployment_service as deployment
from services import live_monitor_service as monitor
from services import traffic_source_service as traffic
from services.model_registry import MODEL_WORKFLOW_VERSION


@pytest.mark.parametrize("name,value", [
    ("ALGOGUARD_PORT", "500O"),
    ("ALGOGUARD_PORT", "0"),
    ("ALGOGUARD_PORT", "65536"),
    ("ALGOGUARD_SECURE_COOKIES", "maybe"),
    ("FLASK_DEBUG", "sometimes"),
    ("ALGOGUARD_DB_MODE", "unknown"),
])
def test_invalid_configuration_is_rejected(name, value):
    with pytest.raises(config.ConfigError, match=name):
        config.load_config({name: value})


@pytest.mark.parametrize("credentials", [
    {},
    {"NEXT_PUBLIC_SUPABASE_URL": "https://project.example.invalid"},
    {
        "NEXT_PUBLIC_SUPABASE_URL": "https://project.example.invalid",
        "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY": "synthetic-key",
    },
])
def test_cloud_mode_never_falls_back_to_sqlite(credentials):
    with pytest.raises(config.ConfigError, match="supabase"):
        config.load_config({"ALGOGUARD_DB_MODE": "supabase", **credentials})


def test_blank_settings_keep_local_defaults():
    empty = config.load_config({})
    blank = config.load_config({name: "  " for name in (
        "ALGOGUARD_HOST", "ALGOGUARD_PORT", "ALGOGUARD_DATABASE_PATH",
        "ALGOGUARD_DEPLOYED_MODEL_PATH", "ALGOGUARD_ADMIN_PASSWORD",
        "ALGOGUARD_SECRET_KEY", "ALGOGUARD_SAVED_MODEL_FOLDER",
        "ALGOGUARD_REPORT_FOLDER", "ALGOGUARD_CAPTURE_FOLDER",
    )})
    assert blank == empty
    assert blank.secret_key_ephemeral
    assert blank.admin_password is None


def test_file_precedence_reload_and_credential_separation(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("ALGOGUARD_PORT=5077\nDATABASE_URL=synthetic-private-dsn\n")
    monkeypatch.setattr(config, "ENV_FILE", env_file)
    monkeypatch.delenv("ALGOGUARD_PORT", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert config.load_config().port == 5077
    # Reading analyst configuration must not make any private file value a
    # process override that maintainer tooling might later inherit.
    assert "DATABASE_URL" not in config.os.environ
    assert "ALGOGUARD_PORT" not in config.os.environ
    assert config.load_config({}).port == 5000

    monkeypatch.setenv("ALGOGUARD_PORT", "6000")
    assert config.load_config().port == 6000
    monkeypatch.delenv("ALGOGUARD_PORT")
    config.reset_config_cache()
    assert config.get_config().port == 5077
    env_file.write_text("ALGOGUARD_PORT=5088\n")
    assert config.load_config().port == 5088
    assert config.get_config().port == 5077
    config.reset_config_cache()
    assert config.get_config().port == 5088
    env_file.unlink()
    assert config.load_config().port == 5000


def test_database_connections_follow_reset_paths(monkeypatch, tmp_path):
    first = tmp_path / "first" / "test.sqlite3"
    second = tmp_path / "second" / "test.sqlite3"
    monkeypatch.setenv("ALGOGUARD_DATABASE_PATH", str(first))
    config.reset_config_cache()
    with db.get_connection() as connection:
        connection.execute("CREATE TABLE marker (value TEXT)")
        connection.execute("INSERT INTO marker VALUES ('first')")

    monkeypatch.setenv("ALGOGUARD_DATABASE_PATH", str(second))
    config.reset_config_cache()
    with db.get_connection() as connection:
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'marker'"
        ).fetchone() is None
    assert config.get_config().database_folder == second.parent
    assert first.is_file() and second.is_file()


def test_quality_gate_follows_reset_thresholds(monkeypatch):
    model = {
        "model_name": "Stacking Ensemble", "version": MODEL_WORKFLOW_VERSION,
        "run_training_status": "completed", "evaluation_status": "completed",
        "accuracy": 85.0, "f1_score": 85.0, "roc_auc": 85.0,
    }
    assert deployment.stacking_deployment_eligibility(model)[0]
    monkeypatch.setenv("ALGOGUARD_MIN_STACKING_ACCURACY", "95")
    config.reset_config_cache()
    eligible, reason = deployment.stacking_deployment_eligibility(model)
    assert not eligible
    assert "95.00%" in reason


def test_deployment_follows_reset_model_path(trained_bundle, monkeypatch, tmp_path):
    model = next(row for row in db.list_model_results(trained_bundle["run_id"])
                 if row["model_name"] == "Stacking Ensemble")
    config.get_config()  # Populate the cache before the fixture changes it.
    monkeypatch.setenv("ALGOGUARD_DEPLOYED_MODEL_PATH", str(tmp_path / "active.joblib"))
    config.reset_config_cache()
    result = deployment.deploy_model(model["model_id"], 1)
    assert Path(result["artifact_path"]).parent == tmp_path
    assert deployment.load_active_artifact()[0]["database_model_id"] == model["model_id"]


def test_training_reports_and_capture_use_configured_paths(monkeypatch, tmp_path):
    config.get_config()
    monkeypatch.setenv("ALGOGUARD_SAVED_MODEL_FOLDER", str(tmp_path / "models"))
    monkeypatch.setenv("ALGOGUARD_REPORT_FOLDER", str(tmp_path / "reports"))
    monkeypatch.setenv("ALGOGUARD_CAPTURE_FOLDER", str(tmp_path / "captures"))
    monkeypatch.delenv("ALGOGUARD_DEPLOYED_MODEL_PATH")
    config.reset_config_cache()

    assert train.build_parser().parse_args(["data.csv"]).models_dir == str(tmp_path / "models")
    assert config.get_config().active_model_path == tmp_path / "models" / "deployed_model.joblib"
    name = train.save_report(42, [{"model_name": "synthetic-model"}])
    assert "synthetic-model" in (tmp_path / "reports" / name).read_text()
    capture = tmp_path / "captures" / "example.pcap"
    capture.parent.mkdir()
    capture.touch()
    assert traffic.list_capture_files() == [{"value": capture.name, "label": capture.name}]
    assert monitor._resolve_capture_path(capture.name) == str(capture)


def test_capture_filter_follows_reset_port(monkeypatch):
    traffic.algoguard_port()
    monkeypatch.setenv("ALGOGUARD_PORT", "5077")
    config.reset_config_cache()
    assert "5077" in traffic.build_capture_filter()
