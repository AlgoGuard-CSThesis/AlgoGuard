"""Run 11 pre-check (protocol_run11.md): grouped 5-fold Random Forest on the run 11 training rows.

Recorded only; nothing is chosen from it. Rows sharing their 13 per-flow features stay in one fold,
so the recall per attack category is not inflated by duplicates. The final test is not read.

    python test-results/model_run11/precheck_run11.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from sklearn.ensemble import RandomForestClassifier  # noqa: E402
from sklearn.model_selection import StratifiedGroupKFold  # noqa: E402
from sklearn.pipeline import Pipeline  # noqa: E402

from services.flow_tracker_service import (  # noqa: E402
    CONNECTION_COUNT_COLUMNS,
    FLOW_FEATURE_COLUMNS,
)
from services.model_registry import RANDOM_STATE  # noqa: E402
from services.preprocessing_service import build_feature_preprocessor  # noqa: E402

DATA = ROOT / "research" / "dataset" / "processed" / "run11"
KEY = [c for c in FLOW_FEATURE_COLUMNS if c not in CONNECTION_COUNT_COLUMNS]


def main():
    training = pd.read_csv(DATA / "training.csv")
    cats = pd.read_csv(DATA / "training_categories.csv")["attack_cat"].astype(str)
    X, y = training[list(FLOW_FEATURE_COLUMNS)], training["label"].astype(int)
    groups = pd.util.hash_pandas_object(training[KEY], index=False)
    numeric = X.select_dtypes(include=np.number).columns.tolist()
    categorical = [c for c in X.columns if c not in numeric]
    pred = np.zeros(len(y), dtype=int)
    for train_index, test_index in StratifiedGroupKFold(
            n_splits=5, shuffle=True, random_state=42).split(X, cats, groups):
        model = Pipeline([("preprocessor", build_feature_preprocessor(numeric, categorical)),
                          ("classifier", RandomForestClassifier(
                              n_estimators=100, class_weight="balanced",
                              random_state=RANDOM_STATE, n_jobs=2))])
        model.fit(X.iloc[train_index], y.iloc[train_index])
        pred[test_index] = model.predict(X.iloc[test_index])
    by_cat = {}
    for cat in sorted(cats.unique()):
        mask = (cats == cat).to_numpy()
        target = 0 if cat.startswith("Normal") else 1
        rate = round(float((pred[mask] == target).mean()), 4)
        by_cat[cat] = {"rows": int(mask.sum()), "rate": rate,
                       "measure": "specificity" if target == 0 else "recall"}
    attack = y.to_numpy() == 1
    result = {"model": "Random Forest (run 11 settings), grouped 5-fold CV on training rows only",
              "rows": len(y), "recall": round(float(pred[attack].mean()), 4),
              "false_positive_rate": round(float(pred[~attack].mean()), 4),
              "by_category": by_cat}
    out = Path(__file__).resolve().parent / "precheck_run11.json"
    out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
