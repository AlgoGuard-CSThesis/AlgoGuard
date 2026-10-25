import time

import numpy as np
import pandas as pd

from services.alert_policy import attack_probability, attack_score_fields
from services.deployment_service import DeploymentError, load_active_artifact


class SimulationServiceError(RuntimeError):
    """Raised when manual prediction cannot safely use the active deployment."""


# Readable labels and hints for the UNSW-NB15 features the bundled models use.
# Units follow services/flow_tracker_service.py, which computes the same features.
FEATURE_GUIDE = {
    "dur": ("Duration", "Flow length in seconds."),
    "proto": ("Protocol", "Transport protocol, for example tcp, udp, or icmp."),
    "service": ("Service", "Application service such as http, dns, or ftp; - when unknown."),
    "state": ("Connection state", "Connection state, for example FIN, CON, INT, or REQ."),
    "spkts": ("Source packets", "Packets sent by the source."),
    "dpkts": ("Destination packets", "Packets sent by the destination."),
    "sbytes": ("Source bytes", "Bytes sent by the source."),
    "dbytes": ("Destination bytes", "Bytes sent by the destination."),
    "rate": ("Packet rate", "Packets per second across both directions."),
    "sttl": ("Source TTL", "Time-to-live of the source's packets."),
    "dttl": ("Destination TTL", "Time-to-live of the destination's packets."),
    "sload": ("Source load", "Source bits per second."),
    "dload": ("Destination load", "Destination bits per second."),
    "sinpkt": ("Source packet gap", "Mean time between source packets, in milliseconds."),
    "dinpkt": ("Destination packet gap", "Mean time between destination packets, in milliseconds."),
    "ct_dst_src_ltm": (
        "Same host pair",
        "Of the last 100 connections, how many had this source and destination (1 or more).",
    ),
    "ct_dst_ltm": (
        "Same destination",
        "Of the last 100 connections, how many went to this destination (1 or more).",
    ),
    "ct_srv_dst": (
        "Same service and destination",
        "Of the last 100 connections, how many used this service to this destination "
        "(1 or more).",
    ),
}


# Every numeric flow feature is a duration, count, size, rate, TTL or gap, so none can be negative.
NON_NEGATIVE_FEATURES = frozenset(
    name for name in FEATURE_GUIDE if name not in {"proto", "service", "state"}
)
# The fitted pipeline works in float32; anything larger cannot be classified.
MAX_MODEL_VALUE = float(np.finfo(np.float32).max)


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
    return schema_for_artifact(artifact, deployment)


def schema_for_artifact(artifact, deployment):
    """Form metadata for one verified artifact (legacy deployment or cloud manifest)."""
    numeric_columns = set(artifact.get("numeric_columns") or [])
    defaults = artifact.get("feature_defaults") or {}
    fields = []
    for column in artifact.get("feature_columns", []):
        # Custom datasets keep their own column names, shown title-cased without a hint.
        label, hint = FEATURE_GUIDE.get(column, (str(column).replace("_", " ").title(), None))
        fields.append({
            "name": column,
            "label": label,
            "hint": hint,
            "type": "number" if column in numeric_columns else "text",
            "min": 0 if column in numeric_columns and column in NON_NEGATIVE_FEATURES else None,
            "default": _display_default(defaults.get(column), column in numeric_columns),
        })
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
    if not feature_columns:
        raise SimulationServiceError("The deployed artifact has no feature schema.")
    # A manual record is classified only as entered: a guessed default would be stored as if
    # it had been observed (DEF-12).
    missing = [
        column for column in feature_columns
        if payload.get(column) is None or not str(payload.get(column)).strip()
    ]
    if missing:
        raise SimulationServiceError(
            f"Enter a value for every field; missing: {', '.join(missing)}."
        )

    row = {}
    for column in feature_columns:
        value = payload[column]
        if column in numeric_columns:
            try:
                row[column] = float(value)
            except (TypeError, ValueError, OverflowError) as error:
                raise SimulationServiceError(
                    f"{column} must contain a finite numerical value."
                ) from error
            if not np.isfinite(row[column]):
                raise SimulationServiceError(f"{column} must contain a finite numerical value.")
            if column in NON_NEGATIVE_FEATURES and row[column] < 0:
                raise SimulationServiceError(f"{column} cannot be negative.")
            if abs(row[column]) > MAX_MODEL_VALUE:
                raise SimulationServiceError(
                    f"{column} is too large to classify; enter a smaller value."
                )
        else:
            row[column] = str(value)
    return pd.DataFrame([row], columns=feature_columns), row


def run_simulation(payload):
    """Predict one manual record using only the active deployed artifact."""
    try:
        artifact, deployment = load_active_artifact()
    except DeploymentError as error:
        raise SimulationServiceError(str(error)) from error

    result = predict_with_artifact(artifact, payload)
    return {
        "prediction": result["prediction"],
        "confidence": result["confidence"],
        "attack_probability": result["attack_probability"],
        "attack_score_band": result["attack_score_band"],
        "score_band_policy": result["score_band_policy"],
        "deployed_model_name": deployment["model_name"],
        "deployment_id": deployment["deployment_id"],
        "model_id": deployment["model_id"],
        "source_run_id": deployment["run_id"],
        "latency_ms": result["latency_ms"],
        "alert_created": result["prediction"] == "Attack",
        "flow_data": result["flow_data"],
    }


def predict_with_artifact(artifact, payload):
    """Classify one manual record with an already verified artifact."""
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
        score = attack_score_fields(attack_probability(probabilities, classes))
    except Exception as error:
        raise SimulationServiceError(f"Prediction failed: {error}") from error
    latency_ms = (time.perf_counter() - start) * 1000

    return {
        "prediction": "Attack" if predicted_value == 1 else "Normal",
        "confidence": round(confidence * 100, 2),
        **score,
        "latency_ms": round(latency_ms, 3),
        "flow_data": flow_data,
    }
