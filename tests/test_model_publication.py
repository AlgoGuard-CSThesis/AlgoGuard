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


def test_artifact_requires_producer_versions_and_exact_feature_schema():
    artifact: dict = {"feature_columns": list(FLOW_FEATURE_COLUMNS)}
    with pytest.raises(ModelDeliveryError, match="runtime"):
        verify_artifact_contract(artifact)
    artifact["training_runtime"] = training_runtime()
    verify_artifact_contract(artifact)
    artifact["feature_columns"].append("dur")
    with pytest.raises(ModelDeliveryError, match="schema"):
        verify_artifact_contract(artifact)
