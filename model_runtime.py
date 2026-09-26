"""Protected model compatibility contract, checked before deserialization."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
import re
from pathlib import Path

WORKFLOW_VERSION = "stacking-five-v3"
FEATURE_SCHEMA_VERSION = "unsw-nb15-fifteen-v1"
PYTHON_VERSION = "3.14.6"
LOCK_PATH = Path(__file__).with_name("requirements-model.lock")
MAX_MODEL_BYTES = 512 * 1024 * 1024


class ModelDeliveryError(RuntimeError):
    """Safe user-facing failure; never attach signed URLs or credential text."""


def locked_dependencies():
    return dict(
        line.split("==", 1)
        for raw in LOCK_PATH.read_text(encoding="utf-8").splitlines()
        if (line := raw.strip()) and not line.startswith("#")
    )


def lock_identifier():
    content = json.dumps(locked_dependencies(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256((PYTHON_VERSION + "\n" + content).encode()).hexdigest()


def verify_runtime():
    if platform.python_version() != PYTHON_VERSION:
        raise ModelDeliveryError("Install the locked Python model runtime.")
    try:
        matches = all(
            importlib.metadata.version(name) == version
            for name, version in locked_dependencies().items()
        )
    except importlib.metadata.PackageNotFoundError:
        matches = False
    if not matches:
        raise ModelDeliveryError("Install requirements-model.lock before loading models.")


def compatibility_metadata():
    verify_runtime()
    return {
        "workflow_version": WORKFLOW_VERSION,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "python_version": PYTHON_VERSION,
        "dependency_versions": json.dumps(locked_dependencies(), sort_keys=True),
        "dependency_lock": lock_identifier(),
    }


def training_runtime():
    """Record actual producer versions without changing legacy training defaults."""
    return {
        "python": platform.python_version(),
        "dependencies": {name: importlib.metadata.version(name) for name in locked_dependencies()},
    }


def verify_artifact_contract(artifact):
    from services.flow_tracker_service import FLOW_FEATURE_COLUMNS

    expected = {"python": PYTHON_VERSION, "dependencies": locked_dependencies()}
    if artifact.get("training_runtime") != expected:
        raise ModelDeliveryError("Retrain this artifact with the locked model runtime.")
    columns = artifact.get("feature_columns", [])
    if len(columns) != len(FLOW_FEATURE_COLUMNS) or set(columns) != set(FLOW_FEATURE_COLUMNS):
        raise ModelDeliveryError("Retrain using the supported fifteen-feature schema.")


def verify_manifest(manifest):
    verify_runtime()
    expected = compatibility_metadata()
    try:
        if any(
            manifest[key] != value
            for key, value in expected.items()
            if key != "dependency_versions"
        ):
            raise ValueError
        if json.loads(manifest["dependency_versions"]) != locked_dependencies():
            raise ValueError
        if manifest["status"] not in {"active", "published", "superseded"}:
            raise ValueError
        if not re.fullmatch(r"[0-9a-f]{64}", manifest["object_sha256"]):
            raise ValueError
        if type(manifest["object_bytes"]) is not int or not (
            0 < manifest["object_bytes"] <= MAX_MODEL_BYTES
        ):
            raise ValueError
        if not re.fullmatch(
            r"releases/[0-9a-f-]{36}/[0-9a-f]{64}\.joblib", manifest["object_path"]
        ):
            raise ValueError
        for key in ("manifest_id", "model_id", "deployment_id"):
            if type(manifest[key]) is not int or not 0 < manifest[key] < 2**63:
                raise ValueError
    except (KeyError, ValueError, TypeError):
        raise ModelDeliveryError("The published model is incompatible or invalid.") from None
