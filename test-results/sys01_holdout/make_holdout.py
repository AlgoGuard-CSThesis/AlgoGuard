"""SYS-01 model B alternative: a hold-out from UNSW_NB15_training-set.csv that neither A nor B sees.

1. Start from the independent evaluation set (training-set rows whose 15 features do not match
   any row in datasets/*.csv - the rows model A never saw).
2. Group those rows by their 15-feature key, so identical feature rows stay on one side, and
   put 30% of the groups (seed 42, stratified by label) into the hold-out H.
3. Model B's training CSV = the whole training-set file minus every row whose key is in H.
   (This keeps the rows A was trained on available to B, as in the original plan.)
4. A and B are then evaluated on H with evaluate_model.py, removing rows seen by either model;
   the expected "removed as seen in training" count is 0, which proves H is unseen by both.

Data files (git-ignored): research/dataset/processed/sys01_holdout/holdout_eval.csv, model_b_train.csv
Evidence (committed):     test-results/sys01_holdout/holdout_manifest.json (seed, counts, SHA-256)
"""
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(sys.argv[1])
sys.path.insert(0, str(ROOT / "scripts"))
from evaluate_model import FEATURES, to_binary  # noqa: E402


def row_keys(frame):
    """Same normalisation as evaluate_model.row_keys, but a stable digest (reproducible split)."""
    norm = pd.DataFrame(index=frame.index)
    for col in FEATURES:
        column = frame[col]
        if pd.api.types.is_numeric_dtype(column):
            norm[col] = column.astype(float).round(9)
        else:
            norm[col] = column.astype(str).str.strip()
    return pd.Series([int(hashlib.sha1(repr(row).encode()).hexdigest()[:15], 16)
                      for row in norm.itertuples(index=False, name=None)], index=frame.index)

SEED = 42
HOLDOUT_SHARE = 0.30
OUT = ROOT / "research" / "dataset" / "processed" / "sys01_holdout"
EVIDENCE = ROOT / "test-results" / "sys01_holdout"
OUT.mkdir(parents=True, exist_ok=True)
EVIDENCE.mkdir(parents=True, exist_ok=True)

source_path = ROOT / "research/dataset/raw/UNSW_NB15_training-set.csv"
source = pd.read_csv(source_path, encoding="utf-8-sig")
source = source.replace([np.inf, -np.inf], np.nan)
keys = row_keys(source)

seen_by_a = set()
for path in sorted((ROOT / "datasets").glob("*.csv")):
    seen_by_a.update(int(k) for k in row_keys(pd.read_csv(path, encoding="utf-8-sig")))
unseen_mask = ~keys.isin(seen_by_a)

# One label per key group (majority label; ties count as attack). Stratify groups by that label.
groups = pd.DataFrame({"key": keys[unseen_mask], "y": to_binary(source.loc[unseen_mask, "label"])})
group_label = groups.groupby("key")["y"].mean().ge(0.5).astype(int)
rng = np.random.default_rng(SEED)
holdout_keys = set()
for label in (0, 1):
    members = group_label.index[group_label == label].to_numpy()
    members = members[rng.permutation(len(members))]
    holdout_keys.update(int(k) for k in members[: int(round(len(members) * HOLDOUT_SHARE))])

in_holdout = keys.isin(holdout_keys)
holdout = source[in_holdout]
model_b_train = source[~in_holdout]
holdout.to_csv(OUT / "holdout_eval.csv", index=False)
model_b_train.to_csv(OUT / "model_b_train.csv", index=False)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


y_h = to_binary(holdout["label"])
manifest = {
    "source": str(source_path.relative_to(ROOT)), "source_sha256": sha(source_path),
    "seed": SEED, "holdout_share_of_unseen_key_groups": HOLDOUT_SHARE,
    "source_rows": len(source), "rows_unseen_by_model_a": int(unseen_mask.sum()),
    "unseen_key_groups": int(len(group_label)), "holdout_key_groups": len(holdout_keys),
    "holdout_rows": len(holdout), "holdout_attack": int(y_h.sum()),
    "holdout_normal": int(len(y_h) - y_h.sum()),
    "holdout_attack_cat": holdout["attack_cat"].astype(str).str.strip().value_counts().to_dict(),
    "model_b_train_rows": len(model_b_train),
    "holdout_sha256": sha(OUT / "holdout_eval.csv"),
    "model_b_train_sha256": sha(OUT / "model_b_train.csv"),
    "key_overlap_holdout_vs_b_train": int(row_keys(holdout).isin(set(int(k) for k in row_keys(model_b_train))).sum()),
}
manifest["data_files"] = [str((OUT / n).relative_to(ROOT)) for n in ("holdout_eval.csv",
                                                                     "model_b_train.csv")]
(EVIDENCE / "holdout_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
print(json.dumps(manifest, indent=2))
