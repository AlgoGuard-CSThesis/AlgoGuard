"""Workflow versions v3 (model 60) and v4 (training run 13): what each check accepts (DEF-24)."""

from uuid import uuid4

import numpy as np
import pandas as pd
import pytest
from sklearn.ensemble import GradientBoostingClassifier, StackingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline

from model_runtime import ModelDeliveryError, compatibility_metadata, verify_manifest
from publish_model import publication_metadata
from services.deployment_service import (
    DeploymentError,
    _validate_stacking_artifact,
    stacking_deployment_eligibility,
)
from services.model_registry import (
    STACKING_MODEL_ID,
    STACKING_MODEL_NAME,
    build_individual_estimators,
)
from services.preprocessing_service import build_feature_preprocessor

V3, V4 = "stacking-five-v3", "stacking-five-v4"


def _artifact(version, old_gradient_boosting):
    rng = np.random.default_rng(7)
    values = rng.normal(size=(60, 2))
    features = pd.DataFrame(values, columns=["a", "b"])
    labels = pd.Series((values[:, 0] + values[:, 1] > 0).astype(int))
    members = build_individual_estimators(n_neighbors=3)
    if old_gradient_boosting:
        members["gradient_boosting"] = GradientBoostingClassifier(random_state=42)
    stack = StackingClassifier(
        estimators=list(members.items()),
        final_estimator=LogisticRegression(max_iter=1000),
        stack_method="predict_proba",
        cv=2,
    )
    pipeline = Pipeline(
        [("preprocessor", build_feature_preprocessor(["a", "b"], [])), ("classifier", stack)]
    )
    pipeline.fit(features, labels)
    return {"model_id": STACKING_MODEL_ID, "model_name": STACKING_MODEL_NAME,
            "workflow_version": version, "pipeline": pipeline}


@pytest.mark.parametrize("version, old_member", [(V3, True), (V4, False)])
def test_each_version_is_accepted_with_its_own_gradient_boosting_member(version, old_member):
    _validate_stacking_artifact(_artifact(version, old_member))


@pytest.mark.parametrize("version, old_member", [(V3, False), (V4, True)])
def test_a_member_from_the_other_version_is_refused(version, old_member):
    with pytest.raises(DeploymentError, match="unexpected base model type"):
        _validate_stacking_artifact(_artifact(version, old_member))


def test_an_unknown_version_is_refused():
    with pytest.raises(DeploymentError, match="legacy workflow version"):
        _validate_stacking_artifact(_artifact("stacking-five-v2", True))


@pytest.mark.parametrize("version", [V3, V4])
def test_both_versions_may_be_deployed_and_published(version):
    eligible, reason = stacking_deployment_eligibility({
        "model_name": STACKING_MODEL_NAME, "version": version,
        "run_training_status": "completed", "evaluation_status": "completed",
        "accuracy": 95.0, "f1_score": 95.0, "roc_auc": 95.0,
    })
    assert eligible, reason


def _manifest(version):
    return {**compatibility_metadata(), "workflow_version": version, "manifest_id": 1,
            "model_id": 2, "deployment_id": 3, "object_sha256": "a" * 64, "object_bytes": 10,
            "object_path": f"releases/{uuid4()}/{'a' * 64}.joblib", "status": "active"}


@pytest.mark.parametrize("version", [V3, V4])
def test_a_manifest_of_either_version_is_accepted(version):
    verify_manifest(_manifest(version))


def test_a_manifest_of_another_version_is_refused():
    with pytest.raises(ModelDeliveryError, match="does not match this version"):
        verify_manifest(_manifest("stacking-five-v2"))


@pytest.mark.parametrize("version", [V3, V4])
def test_publication_stamps_the_artifacts_own_version(version):
    metadata = publication_metadata({"workflow_version": version}, {"version": version})
    assert metadata["workflow_version"] == version


def test_publication_refuses_a_file_that_differs_from_its_record():
    with pytest.raises(ModelDeliveryError, match="does not match its training record"):
        publication_metadata({"workflow_version": V4}, {"version": V3})


def test_publication_refuses_an_unknown_version():
    with pytest.raises(ModelDeliveryError, match="not supported"):
        publication_metadata({"workflow_version": "stacking-five-v2"},
                             {"version": "stacking-five-v2"})
