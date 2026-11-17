"""The decision line set inside the Stacking Ensemble (services/decision_line.py, DEF-24)."""

import copy

import joblib
import numpy as np
import pandas as pd
import pytest
from scipy.special import expit, logit
from sklearn.datasets import make_classification
from sklearn.ensemble import StackingClassifier
from sklearn.linear_model import LogisticRegression

from services.decision_line import apply_decision_line, validate_decision_line
from services.model_registry import build_model_candidates


@pytest.fixture(scope="module")
def fitted_stack():
    features, labels = make_classification(n_samples=240, n_features=6, random_state=3)
    stack = build_model_candidates(stacking_cv=3, n_neighbors=5)["stacking"]["estimator"]
    assert isinstance(stack, StackingClassifier)
    stack.fit(features, labels)
    return stack, features


def test_the_line_becomes_a_score_of_one_half(fitted_stack):
    stack, features = fitted_stack
    before = stack.predict_proba(features)[:, 1]
    shifted = copy.deepcopy(stack)
    assert apply_decision_line(shifted, 0.3) == 0.3
    after = shifted.predict_proba(features)[:, 1]
    clipped = np.clip(before, 1e-12, 1 - 1e-12)
    np.testing.assert_allclose(after, expit(logit(clipped) - logit(0.3)), atol=1e-9)
    assert np.all(np.diff(after[np.argsort(before)]) >= -1e-12)  # order unchanged
    np.testing.assert_array_equal(shifted.predict(features), (before > 0.3).astype(int))


def test_the_default_line_changes_nothing(fitted_stack):
    stack, features = fitted_stack
    unchanged = copy.deepcopy(stack)
    apply_decision_line(unchanged, 0.5)
    np.testing.assert_array_equal(unchanged.predict_proba(features), stack.predict_proba(features))


@pytest.mark.parametrize("value", [0, 1, -0.2, 1.5, float("nan"), "abc"])
def test_lines_outside_zero_and_one_are_refused(value):
    with pytest.raises(ValueError, match="between 0 and 1"):
        validate_decision_line(value)


def test_only_a_fitted_stacking_ensemble_takes_a_line():
    with pytest.raises(TypeError, match="Stacking Ensemble only"):
        apply_decision_line(LogisticRegression(), 0.3)
    unfitted = build_model_candidates(stacking_cv=3)["stacking"]["estimator"]
    with pytest.raises(TypeError, match="fitted"):
        apply_decision_line(unfitted, 0.3)


def test_training_stores_the_line_in_the_stacking_artifact(tmp_path):
    from services.preprocessing_service import prepare_dataset
    from services.training_service import train_and_compare_models

    rows = 40
    csv_path = tmp_path / "small.csv"
    pd.DataFrame({
        "value": list(range(rows)) + list(range(100, 100 + rows)),
        "label": ["Normal"] * rows + ["Attack"] * rows,
    }).to_csv(csv_path, index=False)
    events = []
    result = train_and_compare_models(
        prepare_dataset(csv_path), str(tmp_path / "models"), 1,
        event_logger=lambda **event: events.append(event), decision_line=0.3)

    assert joblib.load(result["stacking_result"]["model_path"])["decision_line"] == 0.3
    forest = next(r for r in result["model_results"] if r["model_id"] == "random_forest")
    assert "decision_line" not in joblib.load(forest["model_path"])
    assert any(event["action"] == "decision_line_applied" for event in events)
