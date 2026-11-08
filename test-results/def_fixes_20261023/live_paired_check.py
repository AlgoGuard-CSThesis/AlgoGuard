"""DEF-19 evidence: one live capture of benign traffic, scored by every candidate model.

Captures live flows on the Wi-Fi adapter for --seconds (run traffic_gen.py alongside). The
flow tracker no longer reports TTLs, so this script records each flow's first TTL in each
direction from the packets itself, which lets the 18-feature model (run 7, with sttl/dttl) be
scored on exactly the same flows as the final 16-feature model (run 8) and its base models.
Reports the share of flows labelled Attack per model, overall and by how the flow ended.

    python test-results/def_fixes_20261023/live_paired_check.py --seconds 90 --out <file.json>
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

from services import flow_tracker_service as tracker_module  # noqa: E402
from services.traffic_source_service import (  # noqa: E402
    LiveCaptureSource,
    list_capture_interfaces,
)

TTLS = {}  # 5-tuple as first seen -> [sttl, dttl]
_add_packet = tracker_module.FlowTracker.add_packet


def recording_add_packet(self, info):
    forward = (info.src_ip, info.src_port, info.dst_ip, info.dst_port, info.proto)
    reverse = (info.dst_ip, info.dst_port, info.src_ip, info.src_port, info.proto)
    if reverse in TTLS:
        if TTLS[reverse][1] == 0:
            TTLS[reverse][1] = info.ttl
    elif forward not in TTLS:
        TTLS[forward] = [info.ttl, 0]
    return _add_packet(self, info)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=90.0)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    tracker_module.FlowTracker.add_packet = recording_add_packet
    interface = next(i for i in list_capture_interfaces() if "wi-fi 6" in i["label"].lower())
    source = LiveCaptureSource(interface["value"])
    source.prepare()
    rows, end = [], time.time() + args.seconds
    started = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        while time.time() < end:
            event = source.next_event(timeout=0.2)
            if event is None:
                continue
            key = (event["source_ip"], event["source_port"], event["destination_ip"],
                   event["destination_port"], event["protocol"])
            sttl, dttl = TTLS.get(key, [0, 0])
            rows.append(dict(event["record"], sttl=sttl, dttl=dttl,
                             end_reason=event["end_reason"]))
    finally:
        source.close()
    frame = pd.DataFrame(rows)

    models = {"run 7 Stacking (18 features, with TTL)": ROOT / "saved_models/run_7/stacking.joblib"}
    for name in ("stacking", "random_forest", "gradient_boosting", "adaboost", "knn",
                 "naive_bayes"):
        models[f"run 8 {name} (16 features)"] = ROOT / f"saved_models/run_8/{name}.joblib"
    shares = {}
    for label, path in models.items():
        artifact = joblib.load(path)
        predicted = artifact["pipeline"].predict(frame[artifact["feature_columns"]]) == 1
        shares[label] = {
            "all": round(float(predicted.mean()), 4),
            **{reason: round(float(predicted[(frame["end_reason"] == reason).to_numpy()].mean()), 4)
               for reason in sorted(frame["end_reason"].unique())},
        }
    report = {
        "started": started, "seconds": args.seconds, "flows": len(frame),
        "end_reasons": frame["end_reason"].value_counts().to_dict(),
        "services": frame["service"].value_counts().to_dict(),
        "sttl_top": frame["sttl"].value_counts().head(4).to_dict(),
        "attack_share": shares,
    }
    args.out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
