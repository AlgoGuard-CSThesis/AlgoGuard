import hashlib
import io
import urllib.error
from types import SimpleNamespace
from uuid import uuid4

import joblib
import pytest

from model_delivery import ModelCache
from model_runtime import (
    ModelDeliveryError,
    compatibility_metadata,
    training_runtime,
    verify_manifest,
)


@pytest.fixture
def delivery(tmp_path, monkeypatch):
    from services import deployment_service
    from services.flow_tracker_service import FLOW_FEATURE_COLUMNS

    artifact = {
        "workflow_version": "stacking-five-v3",
        "test": "verified bytes",
        "training_runtime": training_runtime(),
        "feature_columns": list(FLOW_FEATURE_COLUMNS),
    }
    buffer = io.BytesIO()
    joblib.dump(artifact, buffer)
    content = buffer.getvalue()
    digest = hashlib.sha256(content).hexdigest()
    manifest = {
        **compatibility_metadata(),
        "manifest_id": 1,
        "model_id": 2,
        "deployment_id": 3,
        "object_sha256": digest,
        "object_bytes": len(content),
        "object_path": f"releases/{uuid4()}/{digest}.joblib",
        "status": "active",
    }
    signed = {"signedURL": f"/object/sign/models/{manifest['object_path']}?token=secret"}
    calls = []

    def request(*args, **kwargs):
        calls.append((args, kwargs))
        return signed

    repository = SimpleNamespace(
        base_url="http://127.0.0.1:54321",
        list_records=lambda *a, **k: SimpleNamespace(rows=[manifest]),
        _request=request,
    )
    cache = ModelCache(repository, tmp_path)
    cache._opener = SimpleNamespace(open=lambda *a, **k: io.BytesIO(content))
    monkeypatch.setattr(deployment_service, "_validate_stacking_artifact", lambda value: None)
    return cache, manifest, content, signed, calls


def test_verified_cache_reuses_bytes_but_rechecks_manifest(delivery):
    cache, manifest, _, _, calls = delivery
    first = cache.load_active()
    second = cache.load_active()
    assert first.artifact == second.artifact
    assert first.cache_path == second.cache_path
    assert len(calls) == 1
    manifest["status"] = "draft"
    with pytest.raises(ModelDeliveryError):
        cache.load_active()


@pytest.mark.parametrize("fault", ["partial", "corrupt", "oversized", "expired"])
def test_failed_download_never_promotes_or_deserializes(delivery, monkeypatch, fault):
    cache, _, content, _, _ = delivery

    def read(*args, **kwargs):
        if fault == "expired":
            raise urllib.error.HTTPError("hidden-signed-url", 403, "expired", {}, None)
        value = {
            "partial": content[:-1],
            "corrupt": b"!" * len(content),
            "oversized": content + b"!",
        }[fault]
        return io.BytesIO(value)

    monkeypatch.setattr(joblib, "load", lambda *args: pytest.fail("unverified deserialization"))
    cache._opener = SimpleNamespace(open=read)
    with pytest.raises(ModelDeliveryError) as error:
        cache.load_active()
    assert "hidden-signed-url" not in str(error.value)
    assert list(cache.directory.iterdir()) == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("python_version", "0.0.0"),
        ("dependency_lock", "f" * 64),
        ("dependency_versions", "{}"),
        ("workflow_version", "old"),
        ("feature_schema_version", "wrong"),
        ("object_path", "../../escape"),
    ],
)
def test_incompatible_manifest_refused_before_download(delivery, field, value):
    cache, manifest, _, _, calls = delivery
    manifest[field] = value
    with pytest.raises(ModelDeliveryError):
        cache.load_active()
    assert calls == []


def test_cached_corruption_and_failed_update_keep_pinned_model(delivery):
    cache, manifest, _, _, _ = delivery
    pinned = cache.load_active()
    pinned.cache_path.write_bytes(b"corrupted")
    with pytest.raises(ModelDeliveryError, match="corrupt"):
        cache.load_active()
    assert pinned.artifact["test"] == "verified bytes"
    manifest["dependency_lock"] = "0" * 64
    with pytest.raises(ModelDeliveryError):
        cache.load_active()
    assert pinned.manifest["dependency_lock"] != manifest["dependency_lock"]


def test_signed_url_cannot_change_destination(delivery):
    cache, _, _, signed, _ = delivery
    signed["signedURL"] = "https://other.invalid/steal?token=secret"
    with pytest.raises(ModelDeliveryError, match="authorization"):
        cache.load_active()


def test_runtime_mismatch_prevents_deserialization(delivery, monkeypatch):
    _, manifest, _, _, _ = delivery
    monkeypatch.setattr("model_runtime.platform.python_version", lambda: "3.13.0")
    with pytest.raises(ModelDeliveryError, match="Python"):
        verify_manifest(manifest)
