"""DEF-24 decision line study: can a different decision line meet the targets? Nothing deployed.

Scores model 60 (training run 10) and the run 12 candidate on the selection data of
protocol_run11.md (the hold-out and the four benign captures) at decision lines from 0.01 to
0.95. A line passes selection when hold-out recall >= 0.90, hold-out false-positive rate <= 0.08
and every benign capture has at most 8% of its flows labelled Attack.

Model 60 is also scored on the untouched set used by TC-03 (the official UNSW-NB15 training
partition minus rows whose per-flow features appear in run 10's training file). Picking a line
on the set it is measured on is not allowed, so those figures are an UPPER BOUND, not a result.
The locked final test of run 11 is not read.

    python test-results/cycle2_qa_20261111/def24_decision_line.py
"""
import json
import sys
import time
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from evaluate_model import seen_in_training, to_binary  # noqa: E402
from scapy.layers import inet, l2  # noqa: E402,F401  (decoders, as the app loads them)
from scapy.utils import PcapReader  # noqa: E402

from services.flow_tracker_service import FlowTracker, packet_info_from_scapy  # noqa: E402

DATA = ROOT / "research" / "dataset" / "processed"
MODELS = {"run_10": ROOT / "saved_models" / "run_10" / "stacking.joblib",
          "run_12": ROOT / "saved_models" / "run_12" / "stacking.joblib"}
HOLDOUT = DATA / "flow_window_v2" / "holdout_eval.csv"
TRAIN_RUN12 = DATA / "run11" / "training.csv"
TRAIN_RUN10 = DATA / "def19" / "training_local.csv"
UNTOUCHED = DATA / "official_unsw" / "UNSW_NB15_training-set.csv"
CAPTURES = ["generator.pcap", "browser.pcap", "final_browser.pcap", "final_unseen.pcap"]
LINES = sorted({0.01, 0.02, 0.03} | {round(t, 2) for t in np.arange(0.05, 0.96, 0.05)})
OUT = Path(__file__).with_name("def24_decision_line.json")


def log(*parts):
    print(time.strftime("%H:%M:%S"), *parts, flush=True)


def attack_probability(artifact, frame):
    pipeline, columns = artifact["pipeline"], artifact["feature_columns"]
    probabilities = np.asarray(pipeline.predict_proba(frame[columns]))
    return probabilities[:, list(pipeline.classes_).index(1)]


def measures(y, p, line):
    pred = (p >= line).astype(int)
    tp = int(((pred == 1) & (y == 1)).sum())
    fp = int(((pred == 1) & (y == 0)).sum())
    fn = int(((pred == 0) & (y == 1)).sum())
    tn = int(((pred == 0) & (y == 0)).sum())
    recall = tp / (tp + fn) if tp + fn else 0.0
    precision = tp / (tp + fp) if tp + fp else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"accuracy": round((tp + tn) / len(y), 4), "recall": round(recall, 4),
            "precision": round(precision, 4), "f1": round(f1, 4),
            "fpr": round(fp / (fp + tn), 4) if fp + tn else 0.0}


def capture_flows(path):
    tracker, flows = FlowTracker(), []
    with PcapReader(str(path)) as reader:
        for packet in reader:
            info = packet_info_from_scapy(packet)
            if info is not None:
                flows.extend(tracker.add_packet(info))
    flows.extend(tracker.flush())
    return pd.DataFrame([flow["features"] for flow in flows])


def selection(artifact, holdout, y_holdout, captures):
    p_holdout = attack_probability(artifact, holdout)
    p_captures = {name: attack_probability(artifact, frame) for name, frame in captures.items()}
    rows = []
    for line in LINES:
        row = {"line": line, "holdout": measures(y_holdout, p_holdout, line),
               "benign_share": {name: round(float((p >= line).mean()), 4)
                                for name, p in p_captures.items()}}
        row["passes_selection"] = (row["holdout"]["recall"] >= 0.90
                                   and row["holdout"]["fpr"] <= 0.08
                                   and all(v <= 0.08 for v in row["benign_share"].values()))
        rows.append(row)
    return rows


def main():
    for path in (HOLDOUT, TRAIN_RUN12, TRAIN_RUN10, UNTOUCHED, *MODELS.values()):
        if not path.exists():
            raise SystemExit(f"missing: {path}")
    artifacts = {name: joblib.load(path) for name, path in MODELS.items()}
    report = {"lines": LINES, "selection_rule": "hold-out recall >= 0.90, hold-out FPR <= 0.08, "
              "each benign capture <= 8% Attack", "models": {}}

    log("hold-out")
    holdout = pd.read_csv(HOLDOUT).replace([np.inf, -np.inf], np.nan)
    keep = ~seen_in_training(holdout, pd.read_csv(TRAIN_RUN12), "run11/training.csv")
    holdout = holdout.loc[keep].reset_index(drop=True)
    y_holdout = to_binary(holdout["label"])
    report["holdout_rows"] = int(len(holdout))

    log("benign captures")
    captures = {name: capture_flows(DATA / "def19" / name) for name in CAPTURES}
    report["capture_flows"] = {name: int(len(frame)) for name, frame in captures.items()}

    for name, artifact in artifacts.items():
        log("scoring", name)
        report["models"][name] = {"rows": selection(artifact, holdout, y_holdout, captures)}

    log("untouched set, model 60 only (upper bound)")
    untouched = pd.read_csv(UNTOUCHED, encoding="utf-8-sig").replace([np.inf, -np.inf], np.nan)
    keep = ~seen_in_training(untouched, pd.read_csv(TRAIN_RUN10), "def19/training_local.csv")
    untouched = untouched.loc[keep].reset_index(drop=True)
    y = to_binary(untouched["label"])
    fuzzers = (untouched["attack_cat"].astype(str).str.strip() == "Fuzzers").to_numpy() & (y == 1)
    report["untouched_rows"] = int(len(untouched))
    p = attack_probability(artifacts["run_10"], untouched)
    for row in report["models"]["run_10"]["rows"]:
        bound = measures(y, p, row["line"])
        bound["fuzzers_recall"] = round(float(((p >= row["line"]) & fuzzers).sum()
                                              / fuzzers.sum()), 4)
        bound["all_targets_met"] = (bound["accuracy"] >= 0.90 and bound["f1"] >= 0.90
                                    and bound["recall"] >= 0.90 and bound["fpr"] <= 0.08)
        row["untouched_upper_bound"] = bound
    missed = (p < 0.5) & (y == 1)
    report["model_60_missed_at_0_50"] = {
        "missed_attacks": int(missed.sum()),
        "scored_below_0_10": int((missed & (p < 0.10)).sum()),
        "scored_below_0_05": int((missed & (p < 0.05)).sum()),
        "scored_below_0_02": int((missed & (p < 0.02)).sum()),
    }
    OUT.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    log("written", OUT.name)


if __name__ == "__main__":
    main()
