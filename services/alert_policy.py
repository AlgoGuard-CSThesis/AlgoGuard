"""Alert severity and review states shared by the SQLite and cloud applications."""

import math

# Review workflow: a new alert is Open until someone acknowledges or resolves it.
ALERT_STATUSES = ("Open", "Acknowledged", "Resolved")

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
