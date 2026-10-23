"""Would dropping the TTL features help live traffic without losing the dataset targets?

Trains the project's Random Forest configuration (services/model_registry.py, a fast stand-in
for the Stacking Ensemble) on the schema v2 training file with and without sttl/dttl, scores
the hold-out at the default threshold, captures --seconds of live flows on the Wi-Fi adapter
while traffic_gen.py runs, and reports the share of those benign flows labelled Attack.

    python test-results/def_fixes_20261023/ttl_experiment.py --seconds 60 --out <file.json>

Ran 2026-10-23 10:56 (ttl_experiment.json) while the flow tracker still reported sttl and
dttl, on the 18-column training file of that time; the result led to schema v2 dropping both.
A re-run needs that earlier tracker and training file.
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402
from sklearn.metrics import confusion_matrix, roc_auc_score  # noqa: E402
from sklearn.pipeline import Pipeline  # noqa: E402

from services.model_registry import build_individual_estimators  # noqa: E402
from services.preprocessing_service import build_feature_preprocessor  # noqa: E402
from services.traffic_source_service import (  # noqa: E402
    LiveCaptureSource,
    list_capture_interfaces,
)

DATA = ROOT / "research" / "dataset" / "processed" / "flow_window_v2"
CAT = ["proto", "service", "state"]
# The 18-feature candidate schema as it stood when this ran.
FLOW_FEATURE_COLUMNS = ["dur", "proto", "service", "state", "spkts", "dpkts", "sbytes", "dbytes",
                        "rate", "sttl", "dttl", "sload", "dload", "sinpkt", "dinpkt",
                        "ct_dst_src_ltm", "ct_dst_ltm", "ct_srv_dst"]


def capture(seconds):
    interface = next(i for i in list_capture_interfaces() if "wi-fi 6" in i["label"].lower())
    source = LiveCaptureSource(interface["value"])
    source.prepare()
    records, end = [], time.time() + seconds
    try:
        while time.time() < end:
            event = source.next_event(timeout=0.2)
            if event is not None:
                records.append(event["record"])
    finally:
        source.close()
    return pd.DataFrame(records)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=60.0)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    train = pd.read_csv(DATA / "training.csv")
    holdout = pd.read_csv(DATA / "holdout_eval.csv")
    for frame in (train, holdout):
        for column in CAT:
            frame[column] = frame[column].astype(str)
    live = capture(args.seconds)
    for column in CAT:
        live[column] = live[column].astype(str)
    y_train, y_holdout = train["label"].astype(int), holdout["label"].astype(int)

    results = {"live_flows": len(live)}
    for name, columns in {
        "schema v2 (18 features)": list(FLOW_FEATURE_COLUMNS),
        "schema v2 without sttl, dttl (16)": [c for c in FLOW_FEATURE_COLUMNS
                                              if c not in ("sttl", "dttl")],
    }.items():
        numeric = [c for c in columns if c not in CAT]
        model = Pipeline([("preprocessor", build_feature_preprocessor(numeric, CAT)),
                          ("classifier", build_individual_estimators()["random_forest"])])
        model.fit(train[columns], y_train)
        probabilities = model.predict_proba(holdout[columns])[:, 1]
        tn, fp, fn, tp = confusion_matrix(y_holdout, probabilities >= 0.5).ravel()
        precision, recall = tp / (tp + fp), tp / (tp + fn)
        results[name] = {
            "holdout_accuracy": round((tp + tn) / len(y_holdout), 4),
            "holdout_f1": round(2 * precision * recall / (precision + recall), 4),
            "holdout_recall": round(recall, 4),
            "holdout_fpr": round(fp / (fp + tn), 4),
            "holdout_roc_auc": round(roc_auc_score(y_holdout, probabilities), 4),
            "live_attack_share": round(float((model.predict(live[columns]) == 1).mean()), 4),
        }
        print(name, results[name], flush=True)
    args.out.write_text(json.dumps(results, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
