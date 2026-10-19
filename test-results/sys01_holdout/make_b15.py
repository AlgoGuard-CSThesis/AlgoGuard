"""SYS-01 model B (valid comparison): the same 66,393 hold-out training rows as model_b_train.csv,
restricted to the 15 AlgoGuard flow features + label - the schema model A was trained on.

Why: train.py uses every column except the last as a feature. The raw UNSW-NB15 file carries
`attack_cat` (the attack type, which encodes the label) and `id`, so the first model B (local run 5)
trained on the answer and scored 100% (DEF-08). Dropping those columns is a change to the test
input only; no product code is changed.

Writes research/dataset/processed/sys01_holdout/model_b_train_15.csv (git-ignored) and
test-results/sys01_holdout/model_b_train_15_manifest.json.
"""
import hashlib
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(sys.argv[1])
sys.path.insert(0, str(ROOT / "scripts"))
from evaluate_model import FEATURES  # noqa: E402

src = ROOT / "research/dataset/processed/sys01_holdout/model_b_train.csv"
dst = src.with_name("model_b_train_15.csv")
frame = pd.read_csv(src, encoding="utf-8-sig")
frame[FEATURES + ["label"]].to_csv(dst, index=False)
manifest = {
    "source": str(src.relative_to(ROOT)),
    "source_sha256": hashlib.sha256(src.read_bytes()).hexdigest(),
    "rows": len(frame), "columns": FEATURES + ["label"],
    "dropped_columns": [c for c in frame.columns if c not in FEATURES + ["label"]],
    "output": str(dst.relative_to(ROOT)),
    "output_sha256": hashlib.sha256(dst.read_bytes()).hexdigest(),
}
(ROOT / "test-results/sys01_holdout/model_b_train_15_manifest.json").write_text(
    json.dumps(manifest, indent=2), encoding="utf-8")
print(json.dumps({k: v for k, v in manifest.items() if k != "dropped_columns"}, indent=2))
print("dropped:", len(manifest["dropped_columns"]), "columns incl. id and attack_cat:",
      {"id", "attack_cat"} <= set(manifest["dropped_columns"]))
