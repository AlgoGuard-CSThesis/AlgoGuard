"""Training run 13, step 1 (protocol_run13.md): choose the decision line on selection data only.

3-fold StratifiedGroupKFold over the 76,089 file-2 rows of the training file (groups: 13-feature
key; strata: attack category; seed 13). Each fold trains the v4 Stacking Ensemble as train.py
builds it, on the file-1 rows, the PC A benign flows x10 and the other two folds. It then scores
the held fold, the hold-out and the four selection recordings. Never reads the final test.

    python test-results/model_run13/choose_line.py
"""
import json
import time

import numpy as np
import pandas as pd
from run13_common import (
    FILE1_ROWS,
    FILE2_ROWS,
    FLOW_FEATURE_COLUMNS,
    HERE,
    HOLDOUT,
    LIMITS,
    LINES,
    LOCAL_ROWS,
    RECORDINGS,
    RUN11,
    RUN13,
    SELECTION_RECORDINGS,
    attack_scores,
    key_groups,
    rates,
    recording_flows,
)
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import StratifiedGroupKFold
from sklearn.pipeline import Pipeline

from services.model_registry import MODEL_WORKFLOW_VERSION, build_model_candidates
from services.preprocessing_service import build_feature_preprocessor

SEED, FOLDS = 13, 3
COLUMNS = list(FLOW_FEATURE_COLUMNS)


def stacking_pipeline(frame):
    numeric = frame[COLUMNS].select_dtypes(include=np.number).columns.tolist()
    categorical = [column for column in COLUMNS if column not in numeric]
    stack = build_model_candidates(stacking_cv=5, n_neighbors=5)["stacking"]["estimator"]
    return Pipeline([("preprocessor", build_feature_preprocessor(numeric, categorical)),
                     ("classifier", stack)])


def margin(raw, browsing_max):
    return min(raw["accuracy"] - LIMITS["accuracy"], raw["f1"] - LIMITS["f1"],
               raw["recall"] - LIMITS["recall"], LIMITS["fpr"] - raw["fpr"],
               LIMITS["browsing"] - browsing_max)


def main():
    started = time.time()
    training = pd.read_csv(RUN13 / "training.csv").replace([np.inf, -np.inf], np.nan)
    categories = pd.read_csv(RUN11 / "training_categories.csv")["attack_cat"].astype(str)
    start, end = FILE1_ROWS + LOCAL_ROWS, FILE1_ROWS + LOCAL_ROWS + FILE2_ROWS
    file2 = training.iloc[start:end].reset_index(drop=True)
    file2_categories = categories.iloc[start:end].str.strip().reset_index(drop=True)
    others = pd.concat([training.iloc[:start], training.iloc[end:]], ignore_index=True)
    holdout = pd.read_csv(HOLDOUT).replace([np.inf, -np.inf], np.nan)
    recordings = {name: recording_flows(RECORDINGS / name) for name in SELECTION_RECORDINGS}

    out_of_fold = np.full(len(file2), np.nan)
    holdout_scores = np.zeros(len(holdout))
    recording_scores = {name: np.zeros(len(frame)) for name, frame in recordings.items()}
    splitter = StratifiedGroupKFold(n_splits=FOLDS, shuffle=True, random_state=SEED)
    splits = splitter.split(file2, file2_categories, key_groups(file2))
    for fold, (train_index, test_index) in enumerate(splits, start=1):
        fold_training = pd.concat([others, file2.iloc[train_index]], ignore_index=True)
        pipeline = stacking_pipeline(fold_training)
        pipeline.fit(fold_training[COLUMNS], fold_training["label"].astype(int))
        out_of_fold[test_index] = attack_scores(pipeline, file2.iloc[test_index], COLUMNS)
        holdout_scores += attack_scores(pipeline, holdout, COLUMNS) / FOLDS
        for name, frame in recordings.items():
            recording_scores[name] += attack_scores(pipeline, frame, COLUMNS) / FOLDS
        print(f"fold {fold}/{FOLDS} done after {time.time() - started:.0f} s", flush=True)

    y_file2 = file2["label"].astype(int).to_numpy()
    y_holdout = holdout["label"].astype(int).to_numpy()
    fuzzers = file2_categories.to_numpy() == "Fuzzers"
    sweep = []
    for line in LINES:
        raw = rates(y_file2, out_of_fold, line)
        browsing = {name: float((scores >= line).mean())
                    for name, scores in recording_scores.items()}
        row_margin = margin(raw, max(browsing.values()))
        sweep.append({
            "line": line, **{key: round(value, 4) for key, value in raw.items()},
            "fuzzers_recall": round(float((out_of_fold[fuzzers] >= line).mean()), 4),
            "browsing": {name: round(value, 4) for name, value in browsing.items()},
            "browsing_max": round(max(browsing.values()), 4),
            "holdout": {key: round(value, 4)
                        for key, value in rates(y_holdout, holdout_scores, line).items()},
            "margin": round(row_margin, 4), "qualifies": row_margin >= 0,
        })
    qualifying = [row for row in sweep if row["qualifies"]]
    chosen = max(qualifying, key=lambda row: (row["margin"], row["line"])) if qualifying else None
    report = {
        "protocol": "test-results/model_run13/protocol_run13.md",
        "workflow_version": MODEL_WORKFLOW_VERSION, "seed": SEED, "folds": FOLDS,
        "file2_rows": len(file2),
        "file2_auc_out_of_fold": round(float(roc_auc_score(y_file2, out_of_fold)), 4),
        "sweep": sweep, "chosen_line": chosen["line"] if chosen else None,
        "stop": chosen is None, "minutes": round((time.time() - started) / 60, 1),
    }
    (HERE / "line_choice.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    for row in sweep:
        print(f"{row['line']:.2f} acc {row['accuracy']:.4f} f1 {row['f1']:.4f} "
              f"rec {row['recall']:.4f} fpr {row['fpr']:.4f} browse {row['browsing_max']:.4f} "
              f"ho-fpr {row['holdout']['fpr']:.4f} margin {row['margin']:+.4f}")
    print("chosen line:", report["chosen_line"])


if __name__ == "__main__":
    main()
