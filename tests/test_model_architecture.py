import joblib
import pandas as pd
import pytest
from sklearn.base import clone
from sklearn.ensemble import StackingClassifier
from sklearn.linear_model import LogisticRegression

from services.model_registry import (
    MODEL_IDS,
    MODEL_WORKFLOW_VERSION,
    build_model_candidates,
    safe_model_filename,
)

EXPECTED_NAMES = (
    "Random Forest",
    "Gradient Boosting",
    "AdaBoost",
    "K-Nearest Neighbors",
    "Naive Bayes",
    "Stacking Ensemble",
)


def test_registry_contains_exactly_six_models():
    assert tuple(MODEL_IDS.values()) == EXPECTED_NAMES
    assert "Soft Voting Ensemble" not in MODEL_IDS.values()


def test_removed_soft_voting_identifier_cannot_create_an_artifact_name():
    with pytest.raises(ValueError, match="Unknown AlgoGuard model identifier"):
        safe_model_filename("soft_voting")


def test_all_registered_estimators_are_sklearn_cloneable():
    candidates = build_model_candidates(stacking_cv=3)

    for specification in candidates.values():
        assert clone(specification["estimator"]) is not specification["estimator"]


def test_stacking_uses_five_models_and_logistic_regression():
    candidates = build_model_candidates(stacking_cv=3)
    stacking = candidates["stacking"]["estimator"]
    assert isinstance(stacking, StackingClassifier)
    parameters = stacking.get_params(deep=False)
    assert [name for name, _ in parameters["estimators"]] == list(MODEL_IDS.keys())[:5]
    assert isinstance(parameters["final_estimator"], LogisticRegression)
    assert parameters["stack_method"] == "predict_proba"
    assert parameters["passthrough"] is False


def test_all_five_individual_models_train_successfully(trained_bundle):
    individual_results = trained_bundle["result"]["model_results"][:5]
    assert len(individual_results) == 5
    assert all(result["status"] == "completed" for result in individual_results)


def test_successful_run_produces_six_results(trained_bundle):
    from services.database_service import list_model_results

    results = trained_bundle["result"]["model_results"]
    assert len(results) == 6
    assert len(list_model_results(trained_bundle["run_id"])) == 6
    assert all(result["status"] == "completed" for result in results)


def test_only_individual_models_are_ranked(trained_bundle):
    result = trained_bundle["result"]
    assert len(result["ranked_results"]) == 5
    assert all(model["model_type"] == "individual" for model in result["ranked_results"])
    assert result["best_model"]["model_type"] == "individual"
    assert result["stacking_result"]["model_name"] == "Stacking Ensemble"
    assert result["stacking_result"]["overall_score"] is None
    assert result["stacking_result"].get("rank") is None


def test_stacking_artifact_is_current_and_fitted(trained_bundle):
    stacking_result = trained_bundle["result"]["stacking_result"]
    artifact = joblib.load(stacking_result["model_path"])
    classifier = artifact["pipeline"].named_steps["classifier"]
    parameters = classifier.get_params(deep=False)
    fitted_estimators = getattr(classifier, "estimators_", ())
    fitted_final_estimator = getattr(classifier, "final_estimator_", None)

    assert artifact["workflow_version"] == MODEL_WORKFLOW_VERSION
    assert len(fitted_estimators) == 5
    assert isinstance(fitted_final_estimator, LogisticRegression)
    assert parameters["stack_method"] == "predict_proba"


def test_training_reports_start_and_completion_for_each_model(trained_bundle):
    events = trained_bundle["progress_events"]
    started = [event for event in events if event[3] == "started"]
    completed = [event for event in events if event[3] == "completed"]

    assert len(started) == 6
    assert len(completed) == 6
    assert [event[0] for event in completed] == list(range(1, 7))
    assert all(event[1] == 6 for event in events)


@pytest.mark.parametrize("rows_per_class", [3, 4, 5])
def test_small_valid_datasets_train_all_six_models(tmp_path, rows_per_class):
    from services.preprocessing_service import prepare_dataset
    from services.training_service import train_and_compare_models

    csv_path = tmp_path / "small.csv"
    pd.DataFrame(
        {
            "value": list(range(rows_per_class)) + list(range(100, 100 + rows_per_class)),
            "label": ["Normal"] * rows_per_class + ["Attack"] * rows_per_class,
        }
    ).to_csv(csv_path, index=False)
    prepared = prepare_dataset(csv_path)

    result = train_and_compare_models(prepared, str(tmp_path / "models"), 1)

    assert len(result["model_results"]) == 6
    assert all(row["status"] == "completed" for row in result["model_results"]), result
    pipeline = joblib.load(result["stacking_result"]["model_path"])["pipeline"]
    assert len(pipeline.predict(prepared.X_test)) == len(prepared.y_test)
