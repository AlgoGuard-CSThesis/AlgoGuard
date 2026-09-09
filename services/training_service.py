import os
from datetime import datetime, timezone

import joblib
import numpy as np
from sklearn.pipeline import Pipeline

from services.evaluation_service import (
    calculate_metrics,
    identify_best_model,
    normalize_model_results,
    rank_model_results,
)
from services.model_registry import (
    MODEL_WORKFLOW_VERSION,
    build_model_candidates,
    safe_model_filename,
)
from services.preprocessing_service import build_feature_preprocessor
from services.resource_service import ProcessResourceMonitor


def _emit(event_logger, action, status, model_name=None, message=None):
    if event_logger:
        event_logger(
            module="Training",
            action=action,
            status=status,
            model_name=model_name,
            message=message or action,
        )


def _emit_progress(progress_callback, completed, total, model_name, status):
    """Keep optional UI reporting isolated from the training result."""
    if not progress_callback:
        return
    try:
        progress_callback(completed, total, model_name, status)
    except Exception:
        pass


def _positive_probabilities(pipeline, X_test):
    if not hasattr(pipeline, "predict_proba"):
        raise ValueError("The trained pipeline does not provide probabilities.")
    probabilities = np.asarray(pipeline.predict_proba(X_test))
    classes = list(pipeline.classes_)
    if 1 not in classes or probabilities.ndim != 2:
        raise ValueError("The Attack probability column is unavailable.")
    return probabilities[:, classes.index(1)]


def _artifact_payload(
    pipeline,
    prepared_dataset,
    run_id,
    model_id,
    model_name,
    model_type,
):
    return {
        "artifact_version": 2,
        "workflow_version": MODEL_WORKFLOW_VERSION,
        "pipeline": pipeline,
        "model_id": model_id,
        "model_name": model_name,
        "model_type": model_type,
        "source_run_id": run_id,
        "feature_columns": prepared_dataset.feature_columns,
        "numeric_columns": prepared_dataset.numeric_columns,
        "categorical_columns": prepared_dataset.categorical_columns,
        "feature_defaults": prepared_dataset.feature_defaults,
        "target_column": prepared_dataset.target_column,
        "label_mapping": prepared_dataset.label_mapping,
        "trained_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
    }


def train_and_compare_models(
    prepared_dataset,
    saved_model_folder,
    run_id,
    event_logger=None,
    progress_callback=None,
):
    """Train exactly six independent candidates and isolate model failures."""
    run_folder = os.path.join(saved_model_folder, f"run_{run_id}")
    os.makedirs(run_folder, exist_ok=True)

    minimum_training_class = int(prepared_dataset.y_train.value_counts().min())
    stacking_cv = min(5, minimum_training_class)
    training_rows = len(prepared_dataset.X_train)
    # Stacking fits each base learner on smaller cross-validation folds.
    smallest_fold_rows = training_rows - (training_rows + stacking_cv - 1) // stacking_cv
    n_neighbors = min(5, max(smallest_fold_rows, 1))
    candidates = build_model_candidates(
        stacking_cv=stacking_cv,
        n_neighbors=n_neighbors,
    )
    model_results = []
    candidate_count = len(candidates)

    for candidate_index, (model_id, specification) in enumerate(candidates.items(), start=1):
        model_name = specification["model_name"]
        model_type = specification["model_type"]
        _emit_progress(
            progress_callback,
            candidate_index - 1,
            candidate_count,
            model_name,
            "started",
        )
        action_prefix = "stacking" if model_id == "stacking" else "individual_model"
        _emit(
            event_logger,
            f"{action_prefix}_training_started",
            "Started",
            model_name,
            f"Training started for {model_name}.",
        )

        monitor = ProcessResourceMonitor().start()
        stage = "training"
        model_path = None
        try:
            pipeline = Pipeline(
                steps=[
                    (
                        "preprocessor",
                        build_feature_preprocessor(
                            prepared_dataset.numeric_columns,
                            prepared_dataset.categorical_columns,
                        ),
                    ),
                    ("classifier", specification["estimator"]),
                ]
            )
            pipeline.fit(prepared_dataset.X_train, prepared_dataset.y_train)
            predictions = pipeline.predict(prepared_dataset.X_test)
            positive_probabilities = _positive_probabilities(
                pipeline,
                prepared_dataset.X_test,
            )
            resources = monitor.stop()

            stage = "serialization"
            model_path = os.path.join(run_folder, safe_model_filename(model_id))
            artifact = _artifact_payload(
                pipeline,
                prepared_dataset,
                run_id,
                model_id,
                model_name,
                model_type,
            )
            try:
                joblib.dump(artifact, model_path)
            except Exception as error:
                raise ValueError(f"Model serialization failed: {error}") from error

            model_size_mb = os.path.getsize(model_path) / (1024 * 1024)
            stage = "metric_calculation"
            metrics = calculate_metrics(
                prepared_dataset.y_test,
                predictions,
                positive_probabilities,
                resources["cpu_time_seconds"],
                resources["peak_ram_increase_mb"],
                model_size_mb,
                resources["wall_time_seconds"],
            )
            result = {
                "model_id": model_id,
                "model_name": model_name,
                "model_type": model_type,
                "status": "completed",
                "error_message": None,
                "model_path": model_path,
                "model_file": os.path.basename(model_path),
                **metrics,
            }
            _emit(
                event_logger,
                f"{action_prefix}_training_completed",
                "Success",
                model_name,
                f"Training and metric calculation completed for {model_name}.",
            )
        except Exception as error:
            monitor.stop()
            if model_path and os.path.exists(model_path):
                os.remove(model_path)
            if stage == "metric_calculation":
                _emit(
                    event_logger,
                    "metric_calculation_failed",
                    "Failed",
                    model_name,
                    f"Metric calculation failed for {model_name}: {error}",
                )
            result = {
                "model_id": model_id,
                "model_name": model_name,
                "model_type": model_type,
                "status": "failed",
                "error_message": str(error),
                "model_path": None,
                "model_file": None,
            }
            _emit(
                event_logger,
                f"{action_prefix}_training_failed",
                "Failed",
                model_name,
                f"{model_name} failed: {error}",
            )

        model_results.append(result)
        _emit_progress(
            progress_callback,
            candidate_index,
            candidate_count,
            model_name,
            result["status"],
        )

    comparison_results = [
        result for result in model_results if result.get("model_type") == "individual"
    ]
    stacking_result = next(
        (result for result in model_results if result.get("model_id") == "stacking"),
        None,
    )
    if stacking_result is not None:
        stacking_result["normalized_metrics"] = None
        stacking_result["overall_score"] = None
        stacking_result["rank"] = None

    try:
        normalize_model_results(comparison_results)
        _emit(
            event_logger,
            "normalization_completed",
            "Success",
            message=(
                "Nine required metrics were normalized across the five individual "
                "comparison models."
            ),
        )
    except Exception as error:
        _emit(
            event_logger,
            "normalization_failed",
            "Failed",
            message=f"Metric normalization failed: {error}",
        )
        raise

    ranked = rank_model_results(comparison_results)
    best_model = identify_best_model(comparison_results)
    if best_model:
        _emit(
            event_logger,
            "individual_comparison_ranked",
            "Success",
            best_model["model_name"],
            (
                f"{best_model['model_name']} leads the individual-model comparison "
                f"with normalized score {best_model['overall_score']:.4f}."
            ),
        )

    return {
        "model_results": model_results,
        "ranked_results": ranked,
        "best_model": best_model,
        "stacking_result": stacking_result,
        "stacking_cv": stacking_cv,
    }
