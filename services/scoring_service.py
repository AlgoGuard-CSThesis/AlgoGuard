"""Batch scoring shared by the monitors and the live inference process.

Kept free of database and web imports so ``services.inference_worker`` can load it in a child
process without starting any of the application.
"""

import time

import numpy as np
import pandas as pd

from services.alert_policy import attack_probability


def score_rows(pipeline, rows, feature_columns):
    """Classify flow rows with one ``predict_proba`` call.

    Returns ``(class, confidence, attack probability)`` per row and the model time in
    milliseconds. The class with the highest probability is what ``predict`` returns, so the
    model runs once, not twice; the attack probability is the Attack class's own column.
    """
    frame = pd.DataFrame(rows, columns=feature_columns)
    start = time.perf_counter()
    probabilities = np.asarray(pipeline.predict_proba(frame))
    elapsed_ms = (time.perf_counter() - start) * 1000
    classes = list(pipeline.classes_)
    verdicts = []
    for row in probabilities:
        index = int(np.argmax(row))
        verdicts.append((int(classes[index]), float(row[index]), attack_probability(row, classes)))
    return verdicts, elapsed_ms


def classify_rows(pipeline, rows, feature_columns):
    """``score_rows`` without the attack probability: ``(class, confidence)`` per row."""
    verdicts, elapsed_ms = score_rows(pipeline, rows, feature_columns)
    return [(predicted, confidence) for predicted, confidence, _ in verdicts], elapsed_ms
