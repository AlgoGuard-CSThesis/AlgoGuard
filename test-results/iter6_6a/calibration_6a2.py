"""Stage 6A.2 step 1: calibration of deployment #2 on the calibration set only (protocol_6a2.md).

Computes the Brier score, the expected calibration error (10 equal-width bins) and a reliability
table on the calibration set (the cycle 1 hold-out). If ECE > 0.05, compares sigmoid and isotonic
calibrators by 5-fold cross-validated Brier score on that set and adopts the better one only if it
beats the uncalibrated model. The final evaluation set is not read here.

    python test-results/iter6_6a/calibration_6a2.py
"""
import json
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss
from sklearn.model_selection import StratifiedKFold

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
MODEL = ROOT / "saved_models/run_8/stacking.joblib"
CALIBRATION_SET = ROOT / "research/dataset/processed/flow_window_v2/holdout_eval.csv"
ECE_LIMIT = 0.05
SEED = 42


def reliability(y, p, bins=10):
    edges = np.linspace(0, 1, bins + 1)
    rows, ece = [], 0.0
    for low, high in zip(edges[:-1], edges[1:]):
        mask = (p >= low) & ((p < high) if high < 1 else (p <= high))
        if mask.any():
            mean_p, rate = float(p[mask].mean()), float(y[mask].mean())
            ece += mask.sum() / len(y) * abs(mean_p - rate)
            rows.append({"bin": f"{low:.1f}-{high:.1f}", "rows": int(mask.sum()),
                         "mean_attack_probability": round(mean_p, 4),
                         "observed_attack_rate": round(rate, 4)})
    return rows, ece


def calibrators():
    return {"sigmoid": lambda: LogisticRegression(C=1e6),
            "isotonic": lambda: IsotonicRegression(out_of_bounds="clip")}


def fit_predict(kind, factory, p_train, y_train, p_test):
    model = factory()
    if kind == "sigmoid":
        model.fit(p_train.reshape(-1, 1), y_train)
        return model.predict_proba(p_test.reshape(-1, 1))[:, 1]
    model.fit(p_train, y_train)
    return model.predict(p_test)


def main():
    artifact = joblib.load(MODEL)
    pipeline, columns = artifact["pipeline"], artifact["feature_columns"]
    data = pd.read_csv(CALIBRATION_SET)
    y = data["label"].astype(int).to_numpy()
    p = pipeline.predict_proba(data[columns])[:, list(pipeline.classes_).index(1)]
    table, ece = reliability(y, p)
    report = {"model": str(MODEL.relative_to(ROOT)), "calibration_set_rows": len(y),
              "attacks": int(y.sum()), "brier": round(float(brier_score_loss(y, p)), 5),
              "ece": round(float(ece), 4), "ece_limit": ECE_LIMIT, "reliability": table}
    if ece > ECE_LIMIT:
        folds = StratifiedKFold(n_splits=5, shuffle=True, random_state=SEED)
        scores = {"uncalibrated": [], "sigmoid": [], "isotonic": []}
        for train_index, test_index in folds.split(p, y):
            scores["uncalibrated"].append(brier_score_loss(y[test_index], p[test_index]))
            for kind, factory in calibrators().items():
                q = fit_predict(kind, factory, p[train_index], y[train_index], p[test_index])
                scores[kind].append(brier_score_loss(y[test_index], q))
        mean = {k: round(float(np.mean(v)), 5) for k, v in scores.items()}
        best = min(("sigmoid", "isotonic"), key=lambda k: mean[k])
        report["cross_validated_brier"] = mean
        report["adopted_calibrator"] = best if mean[best] < mean["uncalibrated"] else None
    else:
        report["adopted_calibrator"] = None
        report["note"] = "ECE within the limit; no calibrator fitted (protocol rule 2)."
    (OUT / "calibration_6a2.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
