"""The decision line of a Stacking Ensemble, carried inside the model (DEF-24, training run 13).

AlgoGuard calls a flow an attack when the Attack probability is the higher of the two, that is at
0.50. To use another line X without changing the app, the Logistic Regression that combines the
five members has its intercept lowered by logit(X). The new score, sigmoid(z - logit(X)), is
exactly 0.50 where the old probability was X. The order of the scores, and so the ROC-AUC, does
not change.
"""

import math

from sklearn.ensemble import StackingClassifier
from sklearn.linear_model import LogisticRegression

DEFAULT_DECISION_LINE = 0.5


def validate_decision_line(line: float | str) -> float:
    """Return ``line`` as a float strictly between 0 and 1, or raise ``ValueError``."""
    try:
        value = float(line)
    except (TypeError, ValueError):
        value = math.nan
    if not math.isfinite(value) or not 0.0 < value < 1.0:
        raise ValueError("The decision line must be between 0 and 1, for example 0.30.")
    return value


def apply_decision_line(stacking, line):
    """Set ``line`` inside a fitted Stacking Ensemble and return it as a float."""
    value = validate_decision_line(line)
    if not isinstance(stacking, StackingClassifier):
        raise TypeError("The decision line applies to a Stacking Ensemble only.")
    combiner = getattr(stacking, "final_estimator_", None)
    if not isinstance(combiner, LogisticRegression):
        raise TypeError("The Stacking Ensemble must be fitted with a Logistic Regression combiner.")
    if value != DEFAULT_DECISION_LINE:
        combiner.intercept_ = combiner.intercept_ - math.log(value / (1.0 - value))
    return value
