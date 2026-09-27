import math

import numpy as np
from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)

METRIC_DIRECTIONS = {
    "accuracy": "higher",
    "precision": "higher",
    "recall": "higher",
    "f1_score": "higher",
    "roc_auc": "higher",
    "false_positive_rate": "lower",
    "cpu_usage": "lower",
    "ram_usage": "lower",
    "model_size": "lower",
}

RANKING_METRICS = tuple(METRIC_DIRECTIONS.keys())


def false_positive_rate(y_true, y_pred):
    """Calculate FP / (FP + TN) for Attack as the positive class."""
    tn, fp, _, _ = confusion_matrix(y_true, y_pred, labels=[0, 1]).ravel()
    denominator = fp + tn
    return float(fp / denominator) if denominator else 0.0


def calculate_metrics(
    y_true,
    y_pred,
    positive_probabilities,
    cpu_time_seconds,
    peak_ram_increase_mb,
    model_size_mb,
    processing_time_seconds,
):
    """Calculate the nine ranking metrics using one shared test split."""
    if positive_probabilities is None:
        raise ValueError("Positive-class probabilities are required for ROC-AUC.")

    try:
        roc_auc = roc_auc_score(y_true, positive_probabilities)
    except ValueError as error:
        raise ValueError(f"ROC-AUC could not be calculated: {error}") from error

    # sklearn accepts numeric zero_division; inference from its untyped "warn"
    # default incorrectly restricts this parameter to str.
    return {
        "accuracy": round(accuracy_score(y_true, y_pred) * 100, 6),
        "precision": round(
            precision_score(
                y_true, y_pred, pos_label=1,
                zero_division=0,  # pyright: ignore[reportArgumentType]
            ) * 100,
            6,
        ),
        "recall": round(
            recall_score(
                y_true, y_pred, pos_label=1,
                zero_division=0,  # pyright: ignore[reportArgumentType]
            ) * 100,
            6,
        ),
        "f1_score": round(
            f1_score(
                y_true, y_pred, pos_label=1,
                zero_division=0,  # pyright: ignore[reportArgumentType]
            ) * 100,
            6,
        ),
        "roc_auc": round(float(roc_auc) * 100, 6),
        "false_positive_rate": round(
            false_positive_rate(y_true, y_pred) * 100,
            6,
        ),
        # cpu_usage is process CPU time in seconds for fit plus test prediction.
        "cpu_usage": round(float(cpu_time_seconds), 6),
        # ram_usage is peak process RSS increase in MB over the same interval.
        "ram_usage": round(float(peak_ram_increase_mb), 6),
        "model_size": round(float(model_size_mb), 6),
        "processing_time": round(float(processing_time_seconds), 6),
        "predicted_normal_count": int(np.sum(np.asarray(y_pred) == 0)),
        "predicted_anomaly_count": int(np.sum(np.asarray(y_pred) == 1)),
    }


def _valid_number(value):
    return isinstance(value, (int, float, np.number)) and math.isfinite(float(value))


def normalize_model_results(model_results):
    """Min-max normalize exactly nine metrics across valid candidates."""
    valid_results = []
    for result in model_results:
        if result.get("status") != "completed":
            result["normalized_metrics"] = None
            result["overall_score"] = None
            continue

        missing = [metric for metric in RANKING_METRICS if not _valid_number(result.get(metric))]
        if missing:
            result["status"] = "incomplete"
            result["error_message"] = "Missing or invalid required metrics: " + ", ".join(missing)
            result["normalized_metrics"] = None
            result["overall_score"] = None
            continue
        valid_results.append(result)

    if not valid_results:
        return model_results

    bounds = {
        metric: (
            min(float(result[metric]) for result in valid_results),
            max(float(result[metric]) for result in valid_results),
        )
        for metric in RANKING_METRICS
    }

    for result in valid_results:
        normalized = {}
        for metric, direction in METRIC_DIRECTIONS.items():
            minimum, maximum = bounds[metric]
            if math.isclose(maximum, minimum, rel_tol=1e-12, abs_tol=1e-12):
                score = 1.0
            elif direction == "higher":
                score = (float(result[metric]) - minimum) / (maximum - minimum)
            else:
                score = (maximum - float(result[metric])) / (maximum - minimum)
            normalized[metric] = round(score, 9)

        result["normalized_metrics"] = normalized
        result["overall_score"] = round(
            sum(normalized[metric] for metric in RANKING_METRICS) / 9,
            9,
        )

    return model_results


def rank_model_results(model_results):
    """Rank valid results with the required deterministic tie-break sequence."""
    eligible = [
        result
        for result in model_results
        if result.get("status") == "completed" and _valid_number(result.get("overall_score"))
    ]

    ranked = sorted(
        eligible,
        key=lambda result: (
            -float(result["overall_score"]),
            -float(result["f1_score"]),
            -float(result["recall"]),
            -float(result["roc_auc"]),
            float(result["false_positive_rate"]),
            float(result["ram_usage"]),
            float(result["model_size"]),
            result["model_name"].casefold(),
        ),
    )

    for rank, result in enumerate(ranked, start=1):
        result["rank"] = rank
    for result in model_results:
        if result not in ranked:
            result["rank"] = None
    return ranked


def identify_best_model(model_results):
    """Return the highest valid normalized score without hard-coded preference."""
    ranked = rank_model_results(model_results)
    return ranked[0] if ranked else None
