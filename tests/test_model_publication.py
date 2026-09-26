import pytest

from model_runtime import ModelDeliveryError, training_runtime, verify_artifact_contract
from publish_model import validate_api_url
from services.flow_tracker_service import FLOW_FEATURE_COLUMNS


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
    artifact = {"feature_columns": list(FLOW_FEATURE_COLUMNS)}
    with pytest.raises(ModelDeliveryError, match="runtime"):
        verify_artifact_contract(artifact)
    artifact["training_runtime"] = training_runtime()
    verify_artifact_contract(artifact)
    artifact["feature_columns"].append("dur")
    with pytest.raises(ModelDeliveryError, match="schema"):
        verify_artifact_contract(artifact)
