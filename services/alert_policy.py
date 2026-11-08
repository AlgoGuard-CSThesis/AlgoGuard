"""Attack scores, alert severity and review states shared by the SQLite and cloud applications."""

import math

# Review workflow: a new alert is Open until someone acknowledges or resolves it.
ALERT_STATUSES = ("Open", "Acknowledged", "Resolved")

# Attack-score bands: the model's probability that a flow is an attack (class 1), not a measure
# of impact. The thresholds belong to the policy version: changing one needs a new version, and
# the cloud database accepts only the versions its migrations define
# (supabase/migrations/20261025010000_attack_score_bands.sql).
ATTACK_SCORE_BAND_POLICY = "attack-score-bands-v1"
ATTACK_SCORE_BANDS = (("Very high", 0.90), ("High", 0.70), ("Medium", 0.50), ("Low", 0.0))
ATTACK_CLASS = 1


def attack_probability(probabilities, classes):
    """The probability of the Attack class, found by class value rather than column position.

    ``None`` when the model has no Attack class, so no score is invented.
    """
    classes = list(classes)
    if ATTACK_CLASS not in classes:
        return None
    return float(probabilities[classes.index(ATTACK_CLASS)])


def attack_score_band(probability):
    """Name the band of an attack probability in [0, 1]; ``None`` when there is no score."""
    if probability is None:
        return None
    value = float(probability)
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueError("An attack probability must be between 0 and 1.")
    return next(name for name, floor in ATTACK_SCORE_BANDS if value >= floor)


def attack_score_fields(probability):
    """The three stored score fields of one prediction, all ``None`` when it has no score.

    The band is taken from the stored (rounded) probability, so the two always agree.
    """
    if probability is None:
        return {"attack_probability": None, "attack_score_band": None, "score_band_policy": None}
    value = round(float(probability), 6)
    return {
        "attack_probability": value,
        "attack_score_band": attack_score_band(value),
        "score_band_policy": ATTACK_SCORE_BAND_POLICY,
    }

# Minimum model confidence (percent) for each severity of an Attack verdict.
# A binary verdict is always at least 50% confident, so Low covers 50-75%.
HIGH_SEVERITY_CONFIDENCE = 90.0
MEDIUM_SEVERITY_CONFIDENCE = 75.0


def severity_for_confidence(confidence):
    """Map an Attack verdict's confidence percentage to High, Medium, or Low.

    A missing or unreadable confidence is treated as High so an alert is never
    understated.
    """
    try:
        value = float(confidence)
    except (TypeError, ValueError):
        return "High"
    if not math.isfinite(value) or value >= HIGH_SEVERITY_CONFIDENCE:
        return "High"
    if value >= MEDIUM_SEVERITY_CONFIDENCE:
        return "Medium"
    return "Low"
