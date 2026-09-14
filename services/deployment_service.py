import math
import os
import threading
from datetime import datetime, timezone
from uuid import uuid4

import joblib
from sklearn.ensemble import StackingClassifier
from sklearn.exceptions import NotFittedError
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.utils.validation import check_is_fitted

from config import get_config
from services.database_service import get_active_deployment, get_model_result, record_deployment
from services.model_registry import (
    INDIVIDUAL_MODEL_IDS,
    MODEL_WORKFLOW_VERSION,
    STACKING_MODEL_ID,
    STACKING_MODEL_NAME,
    build_individual_estimators,
)

_cfg = get_config()
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DEFAULT_ACTIVE_MODEL_PATH = os.path.join(BASE_DIR, "saved_models", "deployed_model.joblib")
ACTIVE_MODEL_PATH = str(_cfg.active_model_path)
_DEPLOYMENT_LOCK = threading.Lock()


class DeploymentError(RuntimeError):
    pass


STACKING_QUALITY_THRESHOLDS = {
    "accuracy": _cfg.min_stacking_accuracy,
    "f1_score": _cfg.min_stacking_f1,
    "roc_auc": _cfg.min_stacking_roc_auc,
}

STACKING_THRESHOLD_LABELS = {
    "accuracy": "Accuracy",
    "f1_score": "F1-score",
    "roc_auc": "ROC-AUC",
}


def stacking_deployment_eligibility(model):
    """Return whether one persisted Stacking evaluation may be deployed."""
    if not model:
        return False, "The Stacking evaluation is unavailable."
    if model.get("model_name") != STACKING_MODEL_NAME:
        return False, "Only the evaluated Stacking Ensemble can be deployed."
    if model.get("version") != MODEL_WORKFLOW_VERSION:
        return False, "This Stacking result uses a legacy workflow. Train a new run first."
    if model.get("run_training_status") != "completed":
        return False, "The training run must complete successfully before deployment."
    if model.get("evaluation_status") != "completed":
        return False, "Stacking must complete training and evaluation before deployment."

    failures = []
    for metric_name, minimum in STACKING_QUALITY_THRESHOLDS.items():
        value = model.get(metric_name)
        if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            failures.append(f"{STACKING_THRESHOLD_LABELS[metric_name]} is unavailable")
        elif float(value) < minimum:
            failures.append(
                f"{STACKING_THRESHOLD_LABELS[metric_name]} {float(value):.2f}% "
                f"is below {minimum:.2f}%"
            )

    if failures:
        return False, "Quality gate failed: " + "; ".join(failures) + "."
    return True, "Stacking passed every deployment quality requirement."


def _validate_stacking_artifact(artifact):
    """Verify that an artifact implements the current five-model stack."""
    if not isinstance(artifact, dict):
        raise DeploymentError("The Stacking artifact has an invalid format.")
    if (
        artifact.get("model_id") != STACKING_MODEL_ID
        or artifact.get("model_name") != STACKING_MODEL_NAME
    ):
        raise DeploymentError("The artifact is not the current Stacking model.")
    if artifact.get("workflow_version") != MODEL_WORKFLOW_VERSION:
        raise DeploymentError("The Stacking artifact uses a legacy workflow version.")

    pipeline = artifact.get("pipeline")
    if not isinstance(pipeline, Pipeline):
        raise DeploymentError("The artifact does not contain a Scikit-learn pipeline.")
    classifier = pipeline.named_steps.get("classifier")
    if not isinstance(classifier, StackingClassifier):
        raise DeploymentError("The artifact does not contain a Stacking classifier.")

    classifier_parameters = classifier.get_params(deep=False)
    estimator_configuration = classifier_parameters.get("estimators")
    if not isinstance(estimator_configuration, (list, tuple)):
        raise DeploymentError("The Stacking base-model configuration is invalid.")
    try:
        configured_estimators = dict(estimator_configuration)
    except (TypeError, ValueError) as error:
        raise DeploymentError("The Stacking base-model configuration is invalid.") from error
    expected_estimators = build_individual_estimators()
    if tuple(configured_estimators) != INDIVIDUAL_MODEL_IDS:
        raise DeploymentError("The Stacking artifact does not contain all five base models.")
    if any(
        not isinstance(configured_estimators[model_id], type(expected_estimator))
        for model_id, expected_estimator in expected_estimators.items()
    ):
        raise DeploymentError("The Stacking artifact contains an unexpected base model type.")
    if not isinstance(classifier_parameters.get("final_estimator"), LogisticRegression):
        raise DeploymentError("The Stacking artifact does not use Logistic Regression.")
    if classifier_parameters.get("stack_method") != "predict_proba":
        raise DeploymentError("The Stacking artifact does not combine probabilities.")

    try:
        check_is_fitted(classifier, attributes=("estimators_", "final_estimator_"))
    except NotFittedError as error:
        raise DeploymentError("The Stacking artifact is not fitted.") from error
    fitted_estimators = getattr(classifier, "estimators_", None)
    if not isinstance(fitted_estimators, (list, tuple)) or len(fitted_estimators) != len(
        INDIVIDUAL_MODEL_IDS
    ):
        raise DeploymentError("The fitted Stacking artifact is missing a base model.")
    fitted_final_estimator = getattr(classifier, "final_estimator_", None)
    if not isinstance(fitted_final_estimator, LogisticRegression):
        raise DeploymentError("The fitted Stacking meta-learner is invalid.")


def _remove_temporary_file(path):
    """Best-effort cleanup that never hides the deployment outcome."""
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError:
        pass


def _deploy_model_unlocked(model_id, admin_id):
    """Promote one model while the process-level deployment lock is held."""
    model = get_model_result(model_id)
    if not model:
        raise DeploymentError("The selected model result does not exist.")
    eligible, reason = stacking_deployment_eligibility(model)
    if not eligible:
        raise DeploymentError(reason)

    source_path = model.get("artifact_path")
    if not source_path or not os.path.exists(source_path):
        raise DeploymentError("The selected model artifact is missing.")

    try:
        artifact = joblib.load(source_path)
    except Exception as error:
        raise DeploymentError(
            f"The selected model artifact could not be loaded: {error}"
        ) from error
    _validate_stacking_artifact(artifact)

    metric_summary = {
        "accuracy": model.get("accuracy"),
        "precision": model.get("precision_score"),
        "recall": model.get("recall"),
        "f1_score": model.get("f1_score"),
        "roc_auc": model.get("roc_auc"),
        "false_positive_rate": model.get("fpr"),
        "cpu_time_seconds": model.get("cpu_usage"),
        "peak_ram_increase_mb": model.get("ram_usage"),
        "model_size_mb": model.get("model_size"),
        "overall_score": model.get("overall_score"),
    }
    artifact["database_model_id"] = model["model_id"]
    artifact["source_run_id"] = model["run_id"]
    artifact["model_name"] = model["model_name"]
    artifact["model_type"] = model["model_type"]
    artifact["metric_summary"] = metric_summary
    artifact["deployment_timestamp"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    os.makedirs(os.path.dirname(ACTIVE_MODEL_PATH), exist_ok=True)
    operation_id = uuid4().hex
    base_path, extension = os.path.splitext(ACTIVE_MODEL_PATH)
    published_path = f"{base_path}.{operation_id}{extension or '.joblib'}"
    temporary_path = f"{published_path}.tmp"

    try:
        joblib.dump(artifact, temporary_path)
        staged_artifact = joblib.load(temporary_path)
        _validate_stacking_artifact(staged_artifact)
    except DeploymentError:
        _remove_temporary_file(temporary_path)
        raise
    except Exception as error:
        _remove_temporary_file(temporary_path)
        raise DeploymentError(f"Deployment artifact validation failed: {error}") from error

    try:
        # Published files are immutable. Readers that already fetched the old
        # deployment record can keep loading it while SQLite activates this one.
        os.replace(temporary_path, published_path)
        deployment = record_deployment(
            model["model_id"],
            model["run_id"],
            admin_id,
            published_path,
        )
    except Exception as error:
        _remove_temporary_file(published_path)
        raise DeploymentError(f"Model deployment failed: {error}") from error
    finally:
        _remove_temporary_file(temporary_path)

    return {"model": model, "deployment": deployment, "artifact_path": published_path}


def deploy_model(model_id, admin_id):
    """Atomically deploy one eligible Stacking model within this process."""
    with _DEPLOYMENT_LOCK:
        return _deploy_model_unlocked(model_id, admin_id)


def load_active_artifact():
    """Load only the model referenced by the active deployment record."""
    deployment = get_active_deployment()
    if not deployment:
        raise DeploymentError("No model has been deployed. Deploy an eligible Stacking run first.")
    if deployment.get("model_name") != STACKING_MODEL_NAME:
        raise DeploymentError(
            "The active deployment is not Stacking. Deploy an eligible Stacking run."
        )

    artifact_path = deployment.get("artifact_path") or ACTIVE_MODEL_PATH
    if not os.path.exists(artifact_path):
        raise DeploymentError("The active deployed model artifact is missing.")

    try:
        artifact = joblib.load(artifact_path)
    except Exception as error:
        raise DeploymentError(f"The active model could not be loaded: {error}") from error

    _validate_stacking_artifact(artifact)
    if artifact.get("database_model_id") != deployment.get("model_id"):
        raise DeploymentError("The active artifact does not match the deployment record.")
    return artifact, deployment
