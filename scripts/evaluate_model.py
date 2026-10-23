"""Evaluate a trained AlgoGuard model on data it has never seen (card SYS-01, test case TC-03).

What it does, in order:
  1. Loads the model artifact (.joblib) that train.py produced.
  2. Loads an evaluation CSV with the model's features plus `label`
     (and optionally `attack_cat`).
  3. Removes every evaluation row whose 15 per-flow features also appear in a training CSV,
     whatever its connection counts, so the score is measured only on unseen traffic.
  4. Predicts the remaining rows and writes the metrics, the confusion matrix,
     recall per attack category, and a check against the test plan targets.

Default data: research/dataset/raw/UNSW_NB15_training-set.csv minus every row that
appears in the bundled datasets/*.csv (the bundled CSVs are samples of that file).
A model trained by scripts/build_flow_datasets.py is evaluated on its hold-out instead:
    --data research/dataset/processed/flow_window_v2/holdout_eval.csv
    --train-csv research/dataset/processed/flow_window_v2/training.csv

Usage (from the repository folder, inside the venv):
    python scripts/evaluate_model.py --model saved_models/deployed_model.<id>.joblib
    python scripts/evaluate_model.py --model <file> --compare-dir saved_models/run_<id>

Results go to test-results/evaluation_<timestamp>/.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.metrics import accuracy_score, confusion_matrix, roc_auc_score

ROOT = Path(__file__).resolve().parents[1]
# The 15 per-flow features identify a row when checking what training has seen.
FEATURES = ["dur", "proto", "service", "state", "spkts", "dpkts", "sbytes", "dbytes",
            "rate", "sttl", "dttl", "sload", "dload", "sinpkt", "dinpkt"]
# Feature schema v2 drops sttl and dttl and adds three connection counts
# (services/flow_tracker_service.py); models of either schema can be evaluated.
KNOWN_FEATURES = FEATURES + ["ct_dst_src_ltm", "ct_dst_ltm", "ct_srv_dst"]
NORMAL_LABELS = {"0", "0.0", "normal", "benign", "legitimate", "clean"}
COMPARE_KEYS = ("accuracy", "precision", "recall", "f1", "false_positive_rate", "roc_auc",
                "prediction_ms_per_row")

# Exit criteria from the Master Test Plan v1.0, section 3.3 (edit only by team agreement).
TARGETS = {
    "accuracy": (">=", 0.90),
    "f1": (">=", 0.90),
    "recall": (">=", 0.90),
    "false_positive_rate": ("<=", 0.08),
    "roc_auc": (">=", 0.95),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_commit() -> str:
    try:
        result = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                                capture_output=True, text=True, check=True)
        return result.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def to_binary(labels: pd.Series) -> np.ndarray:
    text = labels.astype(str).str.strip().str.lower()
    return (~text.isin(NORMAL_LABELS)).to_numpy().astype(int)


def row_keys(frame: pd.DataFrame) -> pd.Series:
    """One hashable key per row of the 15 features, robust to float formatting."""
    norm = pd.DataFrame(index=frame.index)
    for col in FEATURES:
        column = frame[col]
        if pd.api.types.is_numeric_dtype(column):
            norm[col] = column.astype(float).round(9)
        else:
            norm[col] = column.astype(str).str.strip()
    # Python's hash is stable within one run, which is all the comparison needs.
    return pd.Series([hash(row) for row in norm.itertuples(index=False, name=None)],
                     index=frame.index)


def load_artifact(path: Path) -> dict[str, Any]:
    artifact = joblib.load(path)
    if not isinstance(artifact, dict) or "pipeline" not in artifact:
        raise SystemExit(f"{path} is not an AlgoGuard model artifact (no 'pipeline').")
    cols = artifact.get("feature_columns") or FEATURES
    if not cols or not set(cols) <= set(KNOWN_FEATURES):
        raise SystemExit(f"{path} expects different features: {cols}")
    return artifact


def ratio(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else float("nan")


def evaluate(artifact: dict[str, Any], data: pd.DataFrame,
             y: np.ndarray) -> tuple[dict[str, Any], np.ndarray]:
    pipeline = artifact["pipeline"]
    features = data[list(artifact.get("feature_columns") or FEATURES)]
    start = time.perf_counter()
    pred = np.asarray(pipeline.predict(features)).astype(int)
    elapsed = time.perf_counter() - start
    classes = list(pipeline.classes_)
    proba = np.asarray(pipeline.predict_proba(features))[:, classes.index(1)]
    tn, fp, fn, tp = (int(v) for v in confusion_matrix(y, pred, labels=[0, 1]).ravel())
    precision = ratio(tp, tp + fp)
    recall = ratio(tp, tp + fn)
    metrics: dict[str, Any] = {
        "rows": int(len(y)),
        "attacks": int(y.sum()),
        "normal": int(len(y) - y.sum()),
        "accuracy": float(accuracy_score(y, pred)),
        "precision": precision,
        "recall": recall,
        "f1": ratio(2 * precision * recall, precision + recall),
        "false_positive_rate": ratio(fp, fp + tn),
        "specificity": ratio(tn, tn + fp),
        "roc_auc": float(roc_auc_score(y, proba)) if len(set(y)) == 2 else float("nan"),
        "tp": tp, "fp": fp, "tn": tn, "fn": fn,
        "prediction_seconds_total": elapsed,
        "prediction_ms_per_row": 1000 * elapsed / max(len(y), 1),
    }
    return metrics, pred


def check_targets(metrics: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for name, (op, target) in TARGETS.items():
        value = metrics[name]
        met = value >= target if op == ">=" else value <= target
        rows.append({"metric": name, "target": f"{op} {target:g}", "measured": round(value, 4),
                     "result": "Met" if met else "Not met"})
    return rows


def per_category(data: pd.DataFrame, pred: np.ndarray) -> list[dict[str, Any]]:
    cats = data["attack_cat"].astype(str).str.strip()
    rows = []
    for cat in sorted(cats.unique()):
        mask = (cats == cat).to_numpy()
        normal = cat.lower() == "normal"
        hits = pred[mask] == (0 if normal else 1)
        rows.append({
            "category": cat,
            "rows": int(mask.sum()),
            "correct": int(hits.sum()),
            "measure": "specificity (labelled Normal)" if normal
            else "recall (flagged as Attack)",
            "rate": round(float(hits.mean()), 4),
        })
    return sorted(rows, key=lambda row: row["rate"])


def compare_models(args: argparse.Namespace, artifact: dict[str, Any], metrics: dict[str, Any],
                   data: pd.DataFrame, y: np.ndarray) -> list[dict[str, Any]]:
    rows = [{"model": artifact.get("model_name", args.model.name), "file": args.model.name,
             **{k: round(metrics[k], 4) for k in COMPARE_KEYS}}]
    main_hash = sha256(args.model)
    for path in sorted(args.compare_dir.glob("*.joblib")):
        if path.resolve() == args.model.resolve() or sha256(path) == main_hash:
            continue  # the same file as the model already evaluated
        try:
            other = load_artifact(path)
        except SystemExit as error:
            print(f"  skipped {path.name}: {error}")
            continue
        if other.get("model_name") and other.get("model_name") == artifact.get("model_name"):
            continue  # same model type as the one under test (the run's own Stacking copy)
        other_metrics, _ = evaluate(other, data, y)
        rows.append({"model": other.get("model_name", path.stem), "file": path.name,
                     **{k: round(other_metrics[k], 4) for k in COMPARE_KEYS}})
    return sorted(rows, key=lambda row: row["f1"], reverse=True)


def summary_lines(context: dict[str, Any], metrics: dict[str, Any], targets: list[dict[str, Any]],
                  categories: list[dict[str, Any]] | None,
                  comparison: list[dict[str, Any]] | None) -> list[str]:
    lines = [
        f"# Model evaluation — {context['evaluated_at']}", "",
        f"- Commit: `{context['git_commit']}` · Python {context['python']} · "
        f"scikit-learn {context['scikit_learn']}",
        f"- Model: `{Path(context['model_file']).name}` ({context['model_name']}), "
        f"trained {context['model_trained_at']}",
        f"- Model SHA-256: `{context['model_sha256']}`",
        f"- Data: `{Path(context['data_file']).name}` — {context['rows_loaded']} rows loaded, "
        f"{context['rows_removed_seen_in_training']} removed as seen in training, "
        f"**{metrics['rows']} evaluated** ({metrics['attacks']} attack, "
        f"{metrics['normal']} normal)",
        "", "## Results against the test plan targets (section 3.3)", "",
        "| Metric | Target | Measured | Result |", "|---|---|---|---|",
    ]
    lines += [f"| {r['metric']} | {r['target']} | {r['measured']} | {r['result']} |"
              for r in targets]
    lines += [
        "", f"Precision {metrics['precision']:.4f} · specificity {metrics['specificity']:.4f} · "
        f"prediction {metrics['prediction_ms_per_row']:.3f} ms per row", "",
        "## Confusion matrix", "", "| | Predicted Normal | Predicted Attack |", "|---|---|---|",
        f"| Actual Normal | {metrics['tn']} | {metrics['fp']} |",
        f"| Actual Attack | {metrics['fn']} | {metrics['tp']} |",
    ]
    if categories is not None:
        lines += ["", "## By attack category (SYS-03)", "",
                  "| Category | Rows | Measure | Rate |", "|---|---|---|---|"]
        lines += [f"| {r['category']} | {r['rows']} | {r['measure']} | {r['rate']} |"
                  for r in categories]
    if comparison is not None:
        lines += ["", "## Model comparison on the same rows (SYS-02)", "",
                  "| Model | Accuracy | Recall | F1 | FPR | ROC-AUC | ms/row |",
                  "|---|---|---|---|---|---|---|"]
        lines += [f"| {r['model']} | {r['accuracy']} | {r['recall']} | {r['f1']} | "
                  f"{r['false_positive_rate']} | {r['roc_auc']} | {r['prediction_ms_per_row']} |"
                  for r in comparison]
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", required=True, type=Path,
                        help="Model artifact (.joblib) to evaluate.")
    parser.add_argument("--data", type=Path,
                        default=ROOT / "research/dataset/raw/UNSW_NB15_training-set.csv",
                        help="Evaluation CSV with the model's features and a label column.")
    parser.add_argument("--train-csv", type=Path, nargs="*",
                        default=sorted((ROOT / "datasets").glob("*.csv")),
                        help="Training CSVs; their rows are removed from the evaluation data.")
    parser.add_argument("--label-column", default="label")
    parser.add_argument("--compare-dir", type=Path,
                        help="Folder of other artifacts (e.g. saved_models/run_<id>) to score.")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = args.out or ROOT / "test-results" / f"evaluation_{stamp}"
    out.mkdir(parents=True, exist_ok=True)

    print(f"Loading evaluation data: {args.data}")
    data = pd.read_csv(args.data, encoding="utf-8-sig")
    missing = [c for c in FEATURES + [args.label_column] if c not in data.columns]
    if missing:
        raise SystemExit(f"Evaluation CSV is missing columns: {missing}")
    data = data.replace([np.inf, -np.inf], np.nan)
    rows_loaded = len(data)

    # Remove anything the model could have seen during training.
    train_keys: set[int] = set()
    for path in args.train_csv:
        frame = pd.read_csv(path, encoding="utf-8-sig")
        if all(c in frame.columns for c in FEATURES):
            train_keys.update(int(k) for k in row_keys(frame))
            print(f"  training rows from {path.name}: {len(frame)}")
    seen = row_keys(data).isin(train_keys)
    data = data[~seen].reset_index(drop=True)
    print(f"Evaluation rows: {rows_loaded} loaded, {int(seen.sum())} removed as seen in "
          f"training, {len(data)} kept")
    if data.empty:
        raise SystemExit("No unseen rows left to evaluate.")

    y = to_binary(data[args.label_column])
    artifact = load_artifact(args.model)
    missing = [c for c in artifact.get("feature_columns") or FEATURES if c not in data.columns]
    if missing:
        raise SystemExit(f"Evaluation CSV lacks features the model needs: {missing}")
    print(f"Evaluating {args.model.name} ({artifact.get('model_name', 'unknown model')}) ...")
    metrics, pred = evaluate(artifact, data, y)

    categories = per_category(data, pred) if "attack_cat" in data.columns else None
    if categories is not None:
        pd.DataFrame(categories).to_csv(out / "per_category.csv", index=False)
    comparison = compare_models(args, artifact, metrics, data, y) if args.compare_dir else None
    if comparison is not None:
        pd.DataFrame(comparison).to_csv(out / "model_comparison.csv", index=False)

    targets = check_targets(metrics)
    context = {
        "evaluated_at": datetime.now().isoformat(timespec="seconds"),
        "git_commit": git_commit(),
        "python": platform.python_version(),
        "scikit_learn": sklearn.__version__,
        "model_file": str(args.model), "model_sha256": sha256(args.model),
        "model_name": artifact.get("model_name"), "model_trained_at": artifact.get("trained_at"),
        "data_file": str(args.data), "data_sha256": sha256(args.data),
        "training_csvs": [str(p) for p in args.train_csv],
        "rows_loaded": rows_loaded, "rows_removed_seen_in_training": int(seen.sum()),
    }
    report = {"context": context, "metrics": metrics, "targets": targets}
    (out / "metrics.json").write_text(json.dumps(report, indent=2, default=float))
    pd.DataFrame([[metrics["tn"], metrics["fp"]], [metrics["fn"], metrics["tp"]]],
                 index=["actual Normal", "actual Attack"],
                 columns=["predicted Normal", "predicted Attack"]
                 ).to_csv(out / "confusion_matrix.csv")

    lines = summary_lines(context, metrics, targets, categories, comparison)
    (out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n" + "\n".join(lines))
    print(f"\nSaved to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
