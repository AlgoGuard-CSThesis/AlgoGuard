"""Training run 13, step 5 (protocol_run13.md): the final test, once.

Runs only after gate_run13.json and fresh_run13.json both passed, and only if final_run13.json
does not exist yet. Scores run 13 and model 60 on the 32,610 reserved rows with
scripts/evaluate_model.py, adds the Brier score, the expected calibration error (10 bins) and the
score bands, and applies the ship rule: all five targets met by run 13.

    python test-results/model_run13/final_eval_run13.py --run <training run id>
"""
import argparse
import json
import subprocess
import sys

import joblib
import numpy as np
import pandas as pd
from run13_common import HERE, ROOT, RUN11, RUN13
from sklearn.metrics import brier_score_loss

sys.path.insert(0, str(ROOT / "test-results" / "iter6_6a"))
from calibration_6a2 import reliability  # noqa: E402

BANDS = (("Very high", 0.90), ("High", 0.70), ("Medium", 0.50), ("Low", 0.0))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=int, required=True)
    args = parser.parse_args()
    gate = json.loads((HERE / "gate_run13.json").read_text(encoding="utf-8"))
    fresh = json.loads((HERE / "fresh_run13.json").read_text(encoding="utf-8"))
    if not gate["gate_passed"] or not fresh["passed"]:
        raise SystemExit("The gate or the fresh recordings did not pass; "
                         "the final test stays unread.")
    if (HERE / "final_run13.json").exists():
        raise SystemExit("The final test has already been run once; it is not run again.")
    models = {"run_13": ROOT / "saved_models" / f"run_{args.run}" / "stacking.joblib",
              "model_60": ROOT / "saved_models" / "run_10" / "stacking.joblib"}
    final = pd.read_csv(RUN11 / "final_test.csv").replace([np.inf, -np.inf], np.nan)
    y = final["label"].astype(int).to_numpy()
    report = {"protocol": "test-results/model_run13/protocol_run13.md", "run": args.run,
              "rows": len(final), "attack": int(y.sum()), "normal": int(len(y) - y.sum()),
              "models": {}}
    for name, model in models.items():
        out = HERE / f"final_{name}"
        subprocess.run([sys.executable, str(ROOT / "scripts" / "evaluate_model.py"),
                        "--model", str(model), "--data", str(RUN11 / "final_test.csv"),
                        "--train-csv", str(RUN13 / "training.csv"), "--out", str(out)],
                       check=True, cwd=ROOT, stdout=subprocess.DEVNULL)
        metrics = json.loads((out / "metrics.json").read_text(encoding="utf-8"))
        if metrics["context"]["rows_removed_seen_in_training"]:
            raise SystemExit("Final test rows matched training rows; the split is broken.")
        artifact = joblib.load(model)
        pipeline = artifact["pipeline"]
        p = np.asarray(pipeline.predict_proba(final[artifact["feature_columns"]]))[
            :, list(pipeline.classes_).index(1)]
        _, ece = reliability(y, p)
        report["models"][name] = {
            **{k: round(metrics["metrics"][k], 4) for k in
               ("accuracy", "precision", "recall", "f1", "false_positive_rate", "roc_auc")},
            "targets": metrics["targets"],
            "by_category": pd.read_csv(out / "per_category.csv").to_dict("records"),
            "brier": round(float(brier_score_loss(y, p)), 5), "ece": round(float(ece), 4),
            "bands": {band: round(float(((p >= floor) & (p < ceiling)).mean()), 4)
                      for (band, floor), ceiling in zip(BANDS, (1.01, 0.90, 0.70, 0.50))},
        }
    report["ship"] = all(row["result"] == "Met" for row in report["models"]["run_13"]["targets"])
    (HERE / "final_run13.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
