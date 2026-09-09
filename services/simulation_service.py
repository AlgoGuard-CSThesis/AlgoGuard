import time

import numpy as np
import pandas as pd

from services.deployment_service import DeploymentError, load_active_artifact


class SimulationServiceError(RuntimeError):
    """Raised when manual prediction cannot safely use the active deployment."""


def _display_default(value, is_numeric):
    """Render a stored feature default without float noise (0.0045000000000000005)."""
    if value is None:
        return ""
    if not is_numeric:
        return str(value)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if not np.isfinite(number):
        return ""
    if number.is_integer():
        return str(int(number))
    return f"{number:.6f}".rstrip("0").rstrip(".")


def get_simulation_schema():
    """Return dynamic feature metadata for the current deployed pipeline."""
    try:
        artifact, deployment = load_active_artifact()
    except DeploymentError as error:
        return {"available": False, "message": str(error), "fields": [], "deployment": None}

    numeric_columns = set(artifact.get("numeric_columns") or [])
    defaults = artifact.get("feature_defaults") or {}
    fields = [
        {
            "name": column,
            "label": str(column).replace("_", " ").title(),
            "type": "number" if column in numeric_columns else "text",
            "default": _display_default(defaults.get(column), column in numeric_columns),
        }
        for column in artifact.get("feature_columns", [])
    ]
    return {
        "available": True,
        "message": None,
        "fields": fields,
        "deployment": deployment,
    }


def _build_input_frame(payload, artifact):
    if not isinstance(payload, dict):
        raise SimulationServiceError("Prediction input must be a JSON object.")
    feature_columns = artifact.get("feature_columns") or []
    numeric_columns = set(artifact.get("numeric_columns") or [])
    defaults = artifact.get("feature_defaults") or {}
    if not feature_columns:
        raise SimulationServiceError("The deployed artifact has no feature schema.")

    row = {}
    for column in feature_columns:
        value = payload.get(column, defaults.get(column))
        if column in numeric_columns:
            if value in (None, ""):
                row[column] = None
            else:
                try:
                    row[column] = float(value)
                except (TypeError, ValueError, OverflowError) as error:
                    raise SimulationServiceError(
                        f"{column} must contain a finite numerical value."
                    ) from error
                if not np.isfinite(row[column]):
                    raise SimulationServiceError(
                        f"{column} must contain a finite numerical value."
                    )
        else:
            row[column] = None if value in (None, "") else str(value)
    # Scikit-learn imputes NaN; persistence and JSON keep missing values as null.
    frame_row = {column: np.nan if value is None else value for column, value in row.items()}
    return pd.DataFrame([frame_row], columns=feature_columns), row


def run_simulation(payload):
    """Predict one manual record using only the active deployed artifact."""
    try:
        artifact, deployment = load_active_artifact()
    except DeploymentError as error:
        raise SimulationServiceError(str(error)) from error

    frame, flow_data = _build_input_frame(payload, artifact)
    pipeline = artifact.get("pipeline")
    if pipeline is None:
        raise SimulationServiceError("The deployed artifact does not contain a pipeline.")

    start = time.perf_counter()
    try:
        predicted_value = int(pipeline.predict(frame)[0])
        probabilities = np.asarray(pipeline.predict_proba(frame))[0]
        classes = list(pipeline.classes_)
        if predicted_value not in classes:
            raise ValueError("Predicted class is not present in the probability output.")
        confidence = float(probabilities[classes.index(predicted_value)])
    except Exception as error:
        raise SimulationServiceError(f"Prediction failed: {error}") from error
    latency_ms = (time.perf_counter() - start) * 1000

    prediction = "Attack" if predicted_value == 1 else "Normal"
    return {
        "prediction": prediction,
        "confidence": round(confidence * 100, 2),
        "deployed_model_name": deployment["model_name"],
        "deployment_id": deployment["deployment_id"],
        "model_id": deployment["model_id"],
        "source_run_id": deployment["run_id"],
        "latency_ms": round(latency_ms, 3),
        "alert_created": prediction == "Attack",
        "flow_data": flow_data,
    }
