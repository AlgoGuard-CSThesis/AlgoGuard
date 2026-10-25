"""Stage 6A.2 step 2 supplement: Brier, ECE, reliability and score bands on the final set.

Uses exactly the rows scripts/evaluate_model.py keeps (same seen-row removal against the whole
82,332-row file used for training and calibration) and the same model, so it adds measures to
that one final scoring rather than a new look at the data.

    python test-results/iter6_6a/final_eval_supplement.py
"""
import json
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import brier_score_loss

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from calibration_6a2 import reliability  # noqa: E402
from evaluate_model import FEATURES, row_keys, to_binary  # noqa: E402

FINAL = ROOT / "research/dataset/processed/official_unsw/UNSW_NB15_training-set.csv"
USED = ROOT / "research/dataset/raw/UNSW_NB15_training-set.csv"
MODEL = ROOT / "saved_models/run_8/stacking.joblib"
BANDS = [("Low", 0.0), ("Medium", 0.50), ("High", 0.70), ("Very high", 0.90)]


def band(p):
    name = "Low"
    for label, floor in BANDS:
        if p >= floor:
            name = label
    return name


def main():
    data = pd.read_csv(FINAL, encoding="utf-8-sig").replace([np.inf, -np.inf], np.nan)
    used = pd.read_csv(USED, encoding="utf-8-sig")
    seen = row_keys(data).isin(set(int(k) for k in row_keys(used[FEATURES])))
    data = data[~seen].reset_index(drop=True)
    y = to_binary(data["label"])
    artifact = joblib.load(MODEL)
    pipeline = artifact["pipeline"]
    p = pipeline.predict_proba(data[artifact["feature_columns"]])[:, list(pipeline.classes_).index(1)]
    table, ece = reliability(y, p)
    bands = pd.Series([band(v) for v in p])
    by_band = []
    for label, _ in BANDS:
        mask = (bands == label).to_numpy()
        by_band.append({"band": label, "rows": int(mask.sum()),
                        "share": round(float(mask.mean()), 4),
                        "observed_attack_rate": round(float(y[mask].mean()), 4) if mask.any() else None})
    report = {"final_rows_loaded": int(len(seen)), "removed_as_seen": int(seen.sum()),
              "final_rows": int(len(y)), "attacks": int(y.sum()), "normal": int(len(y) - y.sum()),
              "brier": round(float(brier_score_loss(y, p)), 5), "ece": round(float(ece), 4),
              "reliability": table, "bands": by_band}
    out = Path(__file__).resolve().parent / "final_eval_supplement.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "reliability"}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
