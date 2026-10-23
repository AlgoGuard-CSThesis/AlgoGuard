"""Why benign live traffic is flagged: score the same captured flows with several models.

Captures live flows (features only, nothing stored but the feature values and verdict shares)
for --seconds, then reports the share labelled Attack by: the cycle 1 model (run 3, 15
features), cycle 1 model B (15 features, most of the UNSW file), the new model (run 7, 18
features), and the new model with its three connection counts replaced by 1 (no history).
It also prints how the live values compare with the UNSW training data for the features
the models lean on (TTLs, connection counts).

    python test-results/def_fixes_20261023/live_shift_check.py --seconds 60 --out <file.json>

Ran 2026-10-23 10:53 (live_shift_check.json) while the flow tracker still reported sttl and
dttl; the final tracker no longer does, so a re-run needs that earlier tracker.
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import joblib  # noqa: E402
import pandas as pd  # noqa: E402

from services.traffic_source_service import (  # noqa: E402
    LiveCaptureSource,
    list_capture_interfaces,
)

MODELS = {
    "cycle 1 model A (run 3, 15 features)": ROOT / "saved_models/run_3/stacking.joblib",
    "cycle 1 model B (15 features, UNSW file)":
        ROOT / "saved_models/cycle1_B/run_6/stacking.joblib",
    "new model (run 7, 18 features)": ROOT / "saved_models/run_7/stacking.joblib",
}
COUNTS = ["ct_dst_src_ltm", "ct_dst_ltm", "ct_srv_dst"]


def attack_share(path, frame, neutral_counts=False):
    artifact = joblib.load(path)
    data = frame.copy()
    if neutral_counts:
        for column in COUNTS:
            data[column] = 1
    predictions = artifact["pipeline"].predict(data[artifact["feature_columns"]])
    return round(float((predictions == 1).mean()), 4)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=60.0)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    interface = next(i for i in list_capture_interfaces() if "wi-fi 6" in i["label"].lower())
    source = LiveCaptureSource(interface["value"])
    source.prepare()
    records = []
    end = time.time() + args.seconds
    try:
        while time.time() < end:
            event = source.next_event(timeout=0.2)
            if event is not None:
                records.append(dict(event["record"], end_reason=event["end_reason"]))
    finally:
        source.close()
    frame = pd.DataFrame(records)
    shares = {name: attack_share(path, frame) for name, path in MODELS.items()}
    shares["new model with connection counts set to 1"] = attack_share(
        MODELS["new model (run 7, 18 features)"], frame, neutral_counts=True)
    unsw = pd.read_csv(ROOT / "research/dataset/processed/flow_window_v2/training.csv")
    normal = unsw[unsw["label"] == 0]
    compare = {}
    for column in ["sttl", "dttl"] + COUNTS:
        compare[column] = {
            "live_top_values": frame[column].value_counts().head(4).to_dict(),
            "unsw_normal_top_values": normal[column].value_counts().head(4).to_dict(),
            "unsw_attack_top_values": unsw[unsw["label"] == 1][column].value_counts()
            .head(4).to_dict(),
        }
    report = {"started": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(end - args.seconds)),
              "flows": len(frame), "end_reasons": frame["end_reason"].value_counts().to_dict(),
              "attack_share_by_model": shares, "feature_values": compare}
    args.out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
