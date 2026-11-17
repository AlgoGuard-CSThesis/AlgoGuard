"""Training run 13, step 3 (protocol_run13.md): the gate, on selection data only.

1. Each of the four selection recordings at most 8% labelled Attack.
2. Artifact at most 50 MiB; deployment quality gate passed.
3. The artifact carries the line chosen in step 1 and workflow version v4.
The hold-out (file 1) is scored and reported, not gated. Never reads the final test.

    python test-results/model_run13/run_gate.py --run <training run id printed by train.py>
"""
import argparse
import hashlib
import json
import subprocess
import sys

import joblib
from run13_common import (
    HERE,
    HOLDOUT,
    LIMITS,
    MAX_ARTIFACT_BYTES,
    RECORDINGS,
    ROOT,
    RUN13,
    SELECTION_RECORDINGS,
    attack_scores,
    recording_flows,
)

from services.database_service import list_model_results
from services.deployment_service import stacking_deployment_eligibility
from services.model_registry import MODEL_WORKFLOW_VERSION, STACKING_MODEL_NAME


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=int, required=True)
    args = parser.parse_args()
    choice = json.loads((HERE / "line_choice.json").read_text(encoding="utf-8"))
    if choice["chosen_line"] is None:
        raise SystemExit("Step 1 chose no line; the protocol stops before training.")
    model_path = ROOT / "saved_models" / f"run_{args.run}" / "stacking.joblib"
    artifact = joblib.load(model_path)
    record = next(model for model in list_model_results(args.run)
                  if model["model_name"] == STACKING_MODEL_NAME)
    eligible, reason = stacking_deployment_eligibility(record)
    browsing = {}
    for name in SELECTION_RECORDINGS:
        flows = recording_flows(RECORDINGS / name)
        scores = attack_scores(artifact["pipeline"], flows, artifact["feature_columns"])
        browsing[name] = {"flows": len(flows),
                          "attack_share": round(float((scores >= 0.5).mean()), 4)}
    holdout_out = HERE / "gate_holdout_run13"
    subprocess.run([sys.executable, str(ROOT / "scripts" / "evaluate_model.py"),
                    "--model", str(model_path), "--data", str(HOLDOUT),
                    "--train-csv", str(RUN13 / "training.csv"), "--out", str(holdout_out)],
                   check=True, cwd=ROOT, stdout=subprocess.DEVNULL)
    holdout = json.loads((holdout_out / "metrics.json").read_text(encoding="utf-8"))
    size = model_path.stat().st_size
    checks = {
        "browsing_each_at_most_8pct": all(item["attack_share"] <= LIMITS["browsing"]
                                          for item in browsing.values()),
        "artifact_at_most_50_mib": size <= MAX_ARTIFACT_BYTES,
        "quality_gate": eligible,
        "line_matches_step_1": artifact.get("decision_line") == choice["chosen_line"],
        "workflow_v4": artifact.get("workflow_version") == MODEL_WORKFLOW_VERSION,
    }
    report = {
        "protocol": "test-results/model_run13/protocol_run13.md", "run": args.run,
        "model_id": record["model_id"], "artifact": model_path.relative_to(ROOT).as_posix(),
        "artifact_bytes": size, "sha256": hashlib.sha256(model_path.read_bytes()).hexdigest(),
        "decision_line": artifact.get("decision_line"), "quality_gate_reason": reason,
        "selection_recordings": browsing,
        "holdout_reported_not_gated": {key: round(holdout["metrics"][key], 4) for key in
                                       ("accuracy", "precision", "recall", "f1",
                                        "false_positive_rate", "roc_auc")},
        "checks": checks, "gate_passed": all(checks.values()),
    }
    (HERE / "gate_run13.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
