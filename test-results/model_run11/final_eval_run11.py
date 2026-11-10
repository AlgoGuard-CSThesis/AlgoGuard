"""Run 11 final evaluation (protocol_run11.md): the locked final test, scored once per model.

Run only after run_gate.py reports gate_passed. Scores the candidate (training run 12) and run 10
on the same rows with scripts/evaluate_model.py, then adds the Brier score, expected calibration
error (10 bins) and attack-score band shares on those rows.

    python test-results/model_run11/final_eval_run11.py
"""
import json
import subprocess
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import brier_score_loss

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "test-results" / "iter6_6a"))
from calibration_6a2 import reliability  # noqa: E402

DATA = ROOT / "research" / "dataset" / "processed" / "run11"
MODELS = {"candidate": ROOT / "saved_models" / "run_12" / "stacking.joblib",
          "run_10": ROOT / "saved_models" / "run_10" / "stacking.joblib"}
BANDS = (("Very high", 0.90), ("High", 0.70), ("Medium", 0.50), ("Low", 0.0))


def main():
    gate = json.loads((HERE / "gate_run11.json").read_text())
    if not gate["gate_passed"]:
        raise SystemExit("The gate did not pass; the final test stays unread.")
    final = pd.read_csv(DATA / "final_test.csv").replace([np.inf, -np.inf], np.nan)
    y = final["label"].astype(int).to_numpy()
    report = {"rows": len(final), "attack": int(y.sum()), "normal": int(len(y) - y.sum()),
              "models": {}}
    for name, model in MODELS.items():
        out = HERE / f"final_{name}"
        subprocess.run([sys.executable, str(ROOT / "scripts" / "evaluate_model.py"),
                        "--model", str(model), "--data", str(DATA / "final_test.csv"),
                        "--train-csv", str(DATA / "training.csv"), "--out", str(out)],
                       check=True, cwd=ROOT, stdout=subprocess.DEVNULL)
        metrics = json.loads((out / "metrics.json").read_text())
        if metrics["context"]["rows_removed_seen_in_training"]:
            raise SystemExit("Final test rows matched training rows; the split is broken.")
        artifact = joblib.load(model)
        pipeline = artifact["pipeline"]
        p = np.asarray(pipeline.predict_proba(final[artifact["feature_columns"]]))[
            :, list(pipeline.classes_).index(1)]
        bins, ece = reliability(y, p)
        report["models"][name] = {
            **{k: round(metrics["metrics"][k], 4) for k in
               ("accuracy", "precision", "recall", "f1", "false_positive_rate", "roc_auc")},
            "targets": metrics["targets"],
            "by_category": pd.read_csv(out / "per_category.csv").to_dict("records"),
            "brier": round(float(brier_score_loss(y, p)), 5), "ece": round(float(ece), 4),
            "bands": {band: round(float(((p >= floor) & (p < ceiling)).mean()), 4)
                      for (band, floor), ceiling in zip(BANDS, (1.01, 0.90, 0.70, 0.50))},
        }
    (HERE / "final_run11.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
