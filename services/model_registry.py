from collections import OrderedDict
from typing import Literal, TypedDict

from sklearn.base import BaseEstimator
from sklearn.ensemble import (
    AdaBoostClassifier,
    GradientBoostingClassifier,
    RandomForestClassifier,
    StackingClassifier,
)
from sklearn.linear_model import LogisticRegression
from sklearn.naive_bayes import GaussianNB
from sklearn.neighbors import KNeighborsClassifier

RANDOM_STATE = 42
MODEL_WORKFLOW_VERSION = "stacking-five-v3"


class ModelSpecification(TypedDict):
    model_name: str
    model_type: Literal["individual", "ensemble"]
    estimator: BaseEstimator


MODEL_IDS: OrderedDict[str, str] = OrderedDict(
    [
        ("random_forest", "Random Forest"),
        ("gradient_boosting", "Gradient Boosting"),
        ("adaboost", "AdaBoost"),
        ("knn", "K-Nearest Neighbors"),
        ("naive_bayes", "Naive Bayes"),
        ("stacking", "Stacking Ensemble"),
    ]
)

INDIVIDUAL_MODEL_IDS: tuple[str, ...] = tuple(list(MODEL_IDS.keys())[:5])
STACKING_MODEL_ID = "stacking"
STACKING_MODEL_NAME = MODEL_IDS[STACKING_MODEL_ID]


def build_individual_estimators(n_neighbors: int = 5) -> OrderedDict[str, BaseEstimator]:
    """Create fresh, unfitted instances for the five individual learners."""
    estimators: OrderedDict[str, BaseEstimator] = OrderedDict()
    estimators["random_forest"] = RandomForestClassifier(
        n_estimators=100,
        class_weight="balanced",
        random_state=RANDOM_STATE,
        n_jobs=1,
    )
    estimators["gradient_boosting"] = GradientBoostingClassifier(random_state=RANDOM_STATE)
    estimators["adaboost"] = AdaBoostClassifier(
        n_estimators=100,
        random_state=RANDOM_STATE,
    )
    estimators["knn"] = KNeighborsClassifier(n_neighbors=max(int(n_neighbors), 1))
    estimators["naive_bayes"] = GaussianNB()
    return estimators


def _ensemble_estimators(n_neighbors: int = 5) -> list[tuple[str, BaseEstimator]]:
    """Return separate estimator instances for one ensemble candidate."""
    estimators = build_individual_estimators(n_neighbors=n_neighbors)
    return [(model_id, estimator) for model_id, estimator in estimators.items()]


def build_model_candidates(
    stacking_cv: int = 5,
    n_neighbors: int = 5,
) -> OrderedDict[str, ModelSpecification]:
    """Build exactly five standalone candidates and one stacking ensemble."""
    candidates: OrderedDict[str, ModelSpecification] = OrderedDict()

    for model_id, estimator in build_individual_estimators(n_neighbors=n_neighbors).items():
        candidates[model_id] = {
            "model_name": MODEL_IDS[model_id],
            "model_type": "individual",
            "estimator": estimator,
        }

    candidates["stacking"] = {
        "model_name": MODEL_IDS["stacking"],
        "model_type": "ensemble",
        "estimator": StackingClassifier(
            estimators=_ensemble_estimators(n_neighbors=n_neighbors),
            final_estimator=LogisticRegression(
                max_iter=1000,
                class_weight="balanced",
                random_state=RANDOM_STATE,
            ),
            stack_method="predict_proba",
            cv=max(int(stacking_cv), 2),
            passthrough=False,
            n_jobs=None,
        ),
    }

    return candidates


def safe_model_filename(model_id: str) -> str:
    if model_id not in MODEL_IDS:
        raise ValueError(f"Unknown AlgoGuard model identifier: {model_id}")
    return f"{model_id}.joblib"
