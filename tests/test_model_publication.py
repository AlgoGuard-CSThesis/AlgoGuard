import pytest

from model_runtime import ModelDeliveryError, training_runtime, verify_artifact_contract
from publish_model import _required_manifest, validate_api_url
from services.flow_tracker_service import FLOW_FEATURE_COLUMNS


@pytest.mark.parametrize("response", [None, [], "invalid"])
def test_publication_requires_a_manifest_before_reporting_success(monkeypatch, response):
    monkeypatch.setattr("publish_model._manifest", lambda *args: response)
    with pytest.raises(ModelDeliveryError, match="manifest"):
        _required_manifest(None, "publication-id")


def test_required_manifest_preserves_the_database_record(monkeypatch):
    manifest = {"manifest_id": 1, "status": "active"}
    monkeypatch.setattr("publish_model._manifest", lambda *args: manifest)
    assert _required_manifest(None, "publication-id") is manifest


@pytest.mark.parametrize(
    "url",
    [
        "http://remote.invalid",
        "https://user:password@remote.invalid",
        "https://remote.invalid/path",
        "https://remote.invalid?token=secret",
        "file:///private",
        "https://remote.invalid/#token",
    ],
)
def test_publisher_rejects_unsafe_project_urls(url):
    with pytest.raises(ModelDeliveryError):
        validate_api_url(url)


def test_the_publisher_says_why_it_refused(monkeypatch, capsys):
    """A refusal such as an unknown model names its reason, not one generic line (full review)."""
    import cloud_migrate
    import maintainer_env
    import publish_model
    from services import database_service

    class Connection:
        def close(self):
            pass

    monkeypatch.setattr(maintainer_env, "load_maintainer_env", lambda: False)
    monkeypatch.setattr(maintainer_env, "require_database_url",
                        lambda: "postgresql://postgres@127.0.0.1:54322/postgres")
    monkeypatch.setattr(cloud_migrate, "connect", lambda dsn: Connection())
    monkeypatch.setattr(database_service, "get_model_result", lambda model_id: None)
    monkeypatch.setenv("SUPABASE_API_URL", "http://127.0.0.1:54321")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "sb_secret_test")
    # --yes: without the maintainer-only database driver (as in CI) every target counts as remote.
    assert publish_model.main(["--model-id", "999", "--publication-id",
                               "00000000-0000-4000-8000-000000000001", "--yes"]) == 1
    assert "The evaluated local model does not exist." in capsys.readouterr().out


def test_artifact_requires_producer_versions_and_exact_feature_schema():
    artifact: dict = {"feature_columns": list(FLOW_FEATURE_COLUMNS)}
    with pytest.raises(ModelDeliveryError, match="runtime"):
        verify_artifact_contract(artifact)
    artifact["training_runtime"] = training_runtime()
    verify_artifact_contract(artifact)
    artifact["feature_columns"].append("dur")
    with pytest.raises(ModelDeliveryError, match="supported flow features"):
        verify_artifact_contract(artifact)
