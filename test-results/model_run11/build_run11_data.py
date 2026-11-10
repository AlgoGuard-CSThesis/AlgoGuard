"""Training run 11 data (protocol_run11.md): run 10's rows plus most of the official training set.

Pool P = rows of the official UNSW-NB15 training partition whose 13 per-flow features (feature
schema v2 without the connection counts) match no row of the 82,332-row file every earlier model
used. P is split by key group with StratifiedGroupKFold(10, shuffle, seed 42) on attack_cat: folds
0-2 are the final test, folds 3-9 join run 10's training rows. Data files go to
research/dataset/processed/run11/ (git-ignored); the manifest with counts and SHA-256 sums is
written next to this script.

    python test-results/model_run11/build_run11_data.py
"""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from sklearn.model_selection import StratifiedGroupKFold  # noqa: E402

from services.flow_tracker_service import (  # noqa: E402
    CONNECTION_COUNT_COLUMNS,
    FLOW_FEATURE_COLUMNS,
)

SEED = 42
KEY = [c for c in FLOW_FEATURE_COLUMNS if c not in CONNECTION_COUNT_COLUMNS]
PROCESSED = ROOT / "research" / "dataset" / "processed"
USED = ROOT / "research" / "dataset" / "raw" / "UNSW_NB15_training-set.csv"
OFFICIAL_TRAINING = PROCESSED / "official_unsw" / "UNSW_NB15_training-set.csv"
RUN10_TRAINING = PROCESSED / "def19" / "training_local.csv"
HOLDOUT_IDS = ROOT / "research" / "dataset" / "holdout_ids.csv"
OUT = PROCESSED / "run11"
FINAL_FOLDS = (0, 1, 2)


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def keys(frame):
    norm = pd.DataFrame(index=frame.index)
    for col in KEY:
        column = frame[col]
        if pd.api.types.is_numeric_dtype(column):
            norm[col] = column.astype(float).round(9)
        else:
            norm[col] = column.astype(str).str.strip()
    return pd.Series([hash(row) for row in norm.itertuples(index=False, name=None)],
                     index=frame.index)


def categories(frame):
    return {k: int(v) for k, v in frame["attack_cat"].astype(str).str.strip()
            .value_counts().items()}


def main():
    used = pd.read_csv(USED, encoding="utf-8-sig")
    official = pd.read_csv(OFFICIAL_TRAINING, encoding="utf-8-sig")
    run10 = pd.read_csv(RUN10_TRAINING)
    columns = list(run10.columns)
    if columns != list(FLOW_FEATURE_COLUMNS) + ["label"]:
        raise SystemExit(f"Unexpected run 10 training columns: {columns}")

    pool = official[~keys(official).isin(set(keys(used)))].reset_index(drop=True)
    groups = keys(pool)
    strata = pool["attack_cat"].astype(str).str.strip()
    splitter = StratifiedGroupKFold(n_splits=10, shuffle=True, random_state=SEED)
    fold = np.full(len(pool), -1)
    for number, (_, test_index) in enumerate(splitter.split(pool, strata, groups)):
        fold[test_index] = number
    is_final = np.isin(fold, FINAL_FOLDS)
    final, added = pool[is_final], pool[~is_final]

    # Attack categories of the training rows, for the pre-check's recall per category only.
    holdout_ids = set(pd.read_csv(HOLDOUT_IDS)["id"].astype(int))
    base = used[~used["id"].astype(int).isin(holdout_ids)].reset_index(drop=True)
    unsw_rows = len(base)
    same = base[list(FLOW_FEATURE_COLUMNS)].reset_index(drop=True).equals(
        run10.iloc[:unsw_rows][list(FLOW_FEATURE_COLUMNS)].reset_index(drop=True))
    if not same:
        raise SystemExit("run 10's first rows are not the UNSW-NB15 training rows in order.")
    training = pd.concat([run10, added[columns]], ignore_index=True)
    training_cats = pd.concat([
        base["attack_cat"].astype(str).str.strip(),
        pd.Series(["Normal (local capture)"] * (len(run10) - unsw_rows)),
        added["attack_cat"].astype(str).str.strip()], ignore_index=True)

    final_keys, training_keys = set(keys(final)), set(keys(training))
    shared_groups = len(set(groups[is_final]) & set(groups[~is_final]))
    if shared_groups or final_keys & training_keys:
        raise SystemExit("A final test row shares its per-flow features with a training row.")

    OUT.mkdir(parents=True, exist_ok=True)
    training.to_csv(OUT / "training.csv", index=False)
    final.to_csv(OUT / "final_test.csv", index=False)
    pd.DataFrame({"attack_cat": training_cats}).to_csv(OUT / "training_categories.csv",
                                                       index=False)

    manifest = {
        "protocol": "test-results/model_run11/protocol_run11.md",
        "key": KEY, "seed": SEED, "folds": 10, "final_folds": list(FINAL_FOLDS),
        "sources": {
            "used_by_earlier_models": {"file": str(USED.relative_to(ROOT)), "rows": len(used),
                                       "sha256": sha256(USED)},
            "official_training_partition": {"file": str(OFFICIAL_TRAINING.relative_to(ROOT)),
                                            "rows": len(official),
                                            "sha256": sha256(OFFICIAL_TRAINING)},
            "run10_training": {"file": str(RUN10_TRAINING.relative_to(ROOT)), "rows": len(run10),
                               "sha256": sha256(RUN10_TRAINING)},
        },
        "pool": {"rows": len(pool), "key_groups": int(groups.nunique()),
                 "by_category": categories(pool)},
        "final_test": {"file": str((OUT / "final_test.csv").relative_to(ROOT)),
                       "rows": len(final), "attack": int(final["label"].sum()),
                       "normal": int(len(final) - final["label"].sum()),
                       "by_category": categories(final),
                       "sha256": sha256(OUT / "final_test.csv")},
        "added_to_training": {"rows": len(added), "by_category": categories(added)},
        "training": {"file": str((OUT / "training.csv").relative_to(ROOT)),
                     "rows": len(training), "attack": int(training["label"].sum()),
                     "normal": int(len(training) - training["label"].sum()),
                     "attack_share": round(float(training["label"].mean()), 4),
                     "sha256": sha256(OUT / "training.csv")},
        "checks": {"key_groups_on_both_sides": shared_groups,
                   "final_rows_matching_a_training_row": len(final_keys & training_keys)},
    }
    path = Path(__file__).resolve().parent / "run11_data_manifest.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
