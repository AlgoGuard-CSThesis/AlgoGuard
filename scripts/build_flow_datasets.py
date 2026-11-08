"""Build the feature schema v2 data: training file, hold-out and the bundled replay samples.

Feature schema v2 (unsw-nb15-flow-window-v2) is 13 per-flow UNSW-NB15 features (the cycle 1 set
without sttl and dttl) plus three connection counts: ct_dst_src_ltm, ct_dst_ltm and ct_srv_dst.
The source is the committed research/dataset/raw/UNSW_NB15_training-set.csv.

1. Hold-out: the rows whose UNSW id is listed in research/dataset/holdout_ids.csv. They are
   the test cycle 1 hold-out (test-results/sys01_holdout/holdout_manifest.json): 30% of the
   15-feature key groups the cycle 1 model never saw, split with seed 42. No training row
   shares its 15 per-flow features with a hold-out row.
2. Training file: every other row, with the 16 features and the label.
3. Bundled samples datasets/algoguard_big.csv (5,000 rows), algoguard_bigger.csv (10,000) and
   algoguard_biggest.csv (20,000): seeded samples of the training file with the same sizes and
   Attack/Normal counts as before; each file contains the smaller ones.

Training and hold-out files go to research/dataset/processed/flow_window_v2/ (git-ignored);
the manifest with counts and SHA-256 sums goes to the folder given as --evidence.

Usage (from the repository folder, inside the venv):
    python scripts/build_flow_datasets.py --evidence test-results/<folder>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from services.flow_tracker_service import FLOW_FEATURE_COLUMNS  # noqa: E402

SEED = 42
SOURCE = ROOT / "research" / "dataset" / "raw" / "UNSW_NB15_training-set.csv"
HOLDOUT_IDS = ROOT / "research" / "dataset" / "holdout_ids.csv"
OUT = ROOT / "research" / "dataset" / "processed" / "flow_window_v2"
DATASETS = ROOT / "datasets"
# (file, attack rows, normal rows) added on top of the previous, smaller sample.
SAMPLE_BLOCKS = [
    ("algoguard_big.csv", 3_403, 1_597),
    ("algoguard_bigger.csv", 3_403, 1_597),
    ("algoguard_biggest.csv", 6_806, 3_194),
]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--evidence", type=Path, required=True,
                        help="Folder for the manifest (e.g. test-results/<run>).")
    args = parser.parse_args()

    source = pd.read_csv(SOURCE, encoding="utf-8-sig")
    holdout_ids = set(pd.read_csv(HOLDOUT_IDS)["id"].astype(int))
    in_holdout = source["id"].astype(int).isin(holdout_ids)
    if int(in_holdout.sum()) != len(holdout_ids):
        raise SystemExit("Some hold-out ids are not in the source file.")

    columns = list(FLOW_FEATURE_COLUMNS) + ["label"]
    holdout = source.loc[in_holdout]
    training = source.loc[~in_holdout, columns]

    OUT.mkdir(parents=True, exist_ok=True)
    holdout.to_csv(OUT / "holdout_eval.csv", index=False)
    training.to_csv(OUT / "training.csv", index=False)

    # Replay samples keep the readable labels the bundled files have always used.
    labelled = training.assign(label=training["label"].map({0: "Normal", 1: "Attack"}))
    rng = np.random.default_rng(SEED)
    pools = {
        label: labelled[labelled["label"] == label].index.to_numpy()[
            rng.permutation(int((labelled["label"] == label).sum()))]
        for label in ("Attack", "Normal")
    }
    taken = {"Attack": 0, "Normal": 0}
    sample = pd.DataFrame(columns=columns)
    samples = {}
    for name, attacks, normals in SAMPLE_BLOCKS:
        picks = []
        for label, count in (("Attack", attacks), ("Normal", normals)):
            picks.extend(pools[label][taken[label]:taken[label] + count])
            taken[label] += count
        block = labelled.loc[picks]
        block = block.iloc[rng.permutation(len(block))]
        sample = block if sample.empty else pd.concat([sample, block])
        sample.to_csv(DATASETS / name, index=False)
        samples[name] = {
            "rows": len(sample),
            "attack": int((sample["label"] == "Attack").sum()),
            "normal": int((sample["label"] == "Normal").sum()),
            "sha256": sha256(DATASETS / name),
        }

    y_holdout = holdout["label"].astype(int)
    manifest = {
        "feature_schema": "unsw-nb15-flow-window-v2",
        "features": list(FLOW_FEATURE_COLUMNS),
        "source": str(SOURCE.relative_to(ROOT)), "source_sha256": sha256(SOURCE),
        "source_rows": len(source), "seed": SEED,
        "holdout_ids": str(HOLDOUT_IDS.relative_to(ROOT)),
        "holdout_ids_sha256": sha256(HOLDOUT_IDS),
        "holdout_rows": len(holdout), "holdout_attack": int(y_holdout.sum()),
        "holdout_normal": int(len(y_holdout) - y_holdout.sum()),
        "holdout_attack_cat":
            holdout["attack_cat"].astype(str).str.strip().value_counts().to_dict(),
        "holdout_sha256": sha256(OUT / "holdout_eval.csv"),
        "training_rows": len(training),
        "training_attack": int(training["label"].sum()),
        "training_normal": int(len(training) - training["label"].sum()),
        "training_sha256": sha256(OUT / "training.csv"),
        "samples": samples,
        "data_files": [str((OUT / n).relative_to(ROOT)) for n in ("holdout_eval.csv",
                                                                 "training.csv")],
    }
    args.evidence.mkdir(parents=True, exist_ok=True)
    (args.evidence / "flow_datasets_manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
