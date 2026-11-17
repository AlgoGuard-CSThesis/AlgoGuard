"""Training run 13 data (protocol_run13.md): run 11's training file with PC A's benign flows x10.

Reads research/dataset/processed/run11/training.csv (never final_test.csv) and writes
research/dataset/processed/run13/training.csv (git-ignored) plus run13_data_manifest.json here.

    python test-results/model_run13/build_run13_data.py
"""
import hashlib
import json

import pandas as pd
from run13_common import FILE1_ROWS, FILE2_ROWS, HERE, LOCAL_ROWS, LOCAL_WEIGHT, ROOT, RUN11, RUN13


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main():
    source = RUN11 / "training.csv"
    run11 = pd.read_csv(source)
    categories = pd.read_csv(RUN11 / "training_categories.csv")["attack_cat"].astype(str)
    if len(run11) != FILE1_ROWS + LOCAL_ROWS + FILE2_ROWS or len(categories) != len(run11):
        raise SystemExit(f"Unexpected run 11 training file: {len(run11)} rows.")
    local = run11.iloc[FILE1_ROWS:FILE1_ROWS + LOCAL_ROWS]
    local_categories = set(categories.iloc[FILE1_ROWS:FILE1_ROWS + LOCAL_ROWS])
    if local_categories != {"Normal (local capture)"} or local["label"].any():
        raise SystemExit("Rows 66,393-68,852 are not the PC A benign flows.")
    training = pd.concat([run11] + [local] * (LOCAL_WEIGHT - 1), ignore_index=True)
    RUN13.mkdir(parents=True, exist_ok=True)
    out = RUN13 / "training.csv"
    training.to_csv(out, index=False)
    manifest = {
        "protocol": "test-results/model_run13/protocol_run13.md",
        "source": {"file": source.relative_to(ROOT).as_posix(), "rows": len(run11),
                   "sha256": sha256(source)},
        "pc_a_benign_rows": LOCAL_ROWS, "pc_a_benign_weight": LOCAL_WEIGHT,
        "training": {"file": out.relative_to(ROOT).as_posix(), "rows": len(training),
                     "attack": int(training["label"].sum()),
                     "normal": int(len(training) - training["label"].sum()),
                     "sha256": sha256(out)},
        "final_test_read": False,
    }
    text = json.dumps(manifest, indent=2) + "\n"
    (HERE / "run13_data_manifest.json").write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
