"""Training run 13, step 4 (protocol_run13.md): the two fresh browsing recordings.

The Lead Architect records them on PC A after the final model exists, in an administrator
terminal, from the repository folder:

    venv\\Scripts\\python.exe test-results\\iter6_6a\\capture_browsing.py --seconds 120
        --out research\\dataset\\processed\\run13\\fresh_generator.pcap
    venv\\Scripts\\python.exe test-results\\iter6_6a\\capture_browsing.py --seconds 120
        --sites unseen --out research\\dataset\\processed\\run13\\fresh_unseen.pcap

Then: python test-results/model_run13/score_fresh.py --run <training run id>
Each recording needs at least 50 flows (fewer: record it again; it is not counted) and at most
8% labelled Attack by run 13. Model 60 (training run 10) is scored for comparison.
"""
import argparse
import json

import joblib
from run13_common import HERE, LIMITS, ROOT, RUN13, attack_scores, recording_flows

FRESH = ("fresh_generator.pcap", "fresh_unseen.pcap")
MIN_FLOWS = 50


def attack_share(artifact, flows):
    scores = attack_scores(artifact["pipeline"], flows, artifact["feature_columns"])
    return round(float((scores >= 0.5).mean()), 4)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=int, required=True)
    args = parser.parse_args()
    gate = json.loads((HERE / "gate_run13.json").read_text(encoding="utf-8"))
    if not gate["gate_passed"]:
        raise SystemExit("The gate did not pass; the protocol stops before step 4.")
    models = {"run_13": ROOT / "saved_models" / f"run_{args.run}" / "stacking.joblib",
              "model_60": ROOT / "saved_models" / "run_10" / "stacking.joblib"}
    artifacts = {name: joblib.load(path) for name, path in models.items()}
    recordings = {}
    for name in FRESH:
        flows = recording_flows(RUN13 / name)
        if len(flows) < MIN_FLOWS:
            raise SystemExit(f"{name} has {len(flows)} flows; record it again "
                             f"(needs {MIN_FLOWS}).")
        recordings[name] = {"flows": len(flows), **{
            model: attack_share(artifact, flows) for model, artifact in artifacts.items()}}
    passed = all(item["run_13"] <= LIMITS["browsing"] for item in recordings.values())
    report = {"protocol": "test-results/model_run13/protocol_run13.md", "run": args.run,
              "recordings": recordings, "passed": passed}
    (HERE / "fresh_run13.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
