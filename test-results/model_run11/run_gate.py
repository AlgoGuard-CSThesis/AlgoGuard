"""Run 11 gate (protocol_run11.md, section Gate): selection data only; the final test is not read.

1. Hold-out: run 11's F1 and recall at most 0.01 below run 10's on the same rows.
2. Each of the four benign captures not used for training: at most 8% labelled Attack.
3. Artifact at most 50 MiB and the deployment quality gate passed.

    python test-results/model_run11/run_gate.py
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from services.database_service import list_model_results  # noqa: E402
from services.deployment_service import stacking_deployment_eligibility  # noqa: E402
from services.model_registry import STACKING_MODEL_NAME  # noqa: E402

HERE = Path(__file__).resolve().parent
PROCESSED = ROOT / "research" / "dataset" / "processed"
# The first attempt (training run 11) was interrupted during Stacking; the same command, data and
# seeds ran again as training run 12, which is the candidate.
CANDIDATE_RUN = 12
MODELS = {"run_10": ROOT / "saved_models" / "run_10" / "stacking.joblib",
          "candidate": ROOT / "saved_models" / f"run_{CANDIDATE_RUN}" / "stacking.joblib"}
CAPTURES = ["generator.pcap", "browser.pcap", "final_browser.pcap", "final_unseen.pcap"]
MAX_BYTES = 50 * 1024 * 1024
PYTHON = sys.executable


def holdout(name, model):
    out = HERE / f"gate_holdout_{name}"
    subprocess.run([PYTHON, str(ROOT / "scripts" / "evaluate_model.py"), "--model", str(model),
                    "--data", str(PROCESSED / "flow_window_v2" / "holdout_eval.csv"),
                    "--train-csv", str(PROCESSED / "run11" / "training.csv"),
                    "--out", str(out)], check=True, cwd=ROOT, stdout=subprocess.DEVNULL)
    return json.loads((out / "metrics.json").read_text())


def benign(model, capture):
    result = subprocess.run([PYTHON, str(ROOT / "test-results" / "iter6_6a" /
                                         "benign_attack_share.py"),
                             "--pcap", str(PROCESSED / "def19" / capture), "--model", str(model)],
                            check=True, cwd=ROOT, capture_output=True, text=True)
    return json.loads(result.stdout[result.stdout.index("{"):])


def main():
    report = {"holdout": {}, "benign": {}, "artifact": {}, "rules": {}}
    for name, model in MODELS.items():
        metrics = holdout(name, model)
        report["holdout"][name] = {
            "rows": metrics["metrics"]["rows"],
            "removed_as_seen": metrics["context"]["rows_removed_seen_in_training"],
            **{k: round(metrics["metrics"][k], 4) for k in
               ("accuracy", "precision", "recall", "f1", "false_positive_rate", "roc_auc")}}
    for name, model in MODELS.items():
        report["benign"][name] = {}
        for capture in CAPTURES:
            share = benign(model, capture)
            report["benign"][name][capture] = {k: share[k] for k in
                                               ("flows", "labelled_attack", "attack_share",
                                                "bands")}
    stacking = next(m for m in list_model_results(CANDIDATE_RUN) if m.get("model_name") ==
                    STACKING_MODEL_NAME)
    eligible, reason = stacking_deployment_eligibility(stacking)
    size = MODELS["candidate"].stat().st_size
    report["artifact"] = {"bytes": size, "mib": round(size / 1024 / 1024, 2),
                          "quality_gate": reason,
                          "in_app_test_split": {k: stacking.get(k) for k in
                                                ("accuracy", "f1_score", "recall", "roc_auc",
                                                 "fpr")}}

    h10, h11 = report["holdout"]["run_10"], report["holdout"]["candidate"]
    report["rules"] = {
        "1_holdout": {"same_rows": h10["rows"] == h11["rows"],
                      "f1_floor": round(h10["f1"] - 0.01, 4),
                      "recall_floor": round(h10["recall"] - 0.01, 4),
                      "passed": h10["rows"] == h11["rows"] and h11["f1"] >= h10["f1"] - 0.01
                      and h11["recall"] >= h10["recall"] - 0.01},
        "2_benign": {"passed": all(v["attack_share"] <= 0.08
                                   for v in report["benign"]["candidate"].values())},
        "3_artifact": {"passed": eligible and size <= MAX_BYTES},
    }
    report["gate_passed"] = all(rule["passed"] for rule in report["rules"].values())
    (HERE / "gate_run11.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
