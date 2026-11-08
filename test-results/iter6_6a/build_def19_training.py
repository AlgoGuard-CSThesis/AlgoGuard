"""DEF-19 part 2: the run 8 training rows plus benign flows from local captures, labelled Normal.

Flows come from the same tracker live capture uses (``FlowTracker`` on each capture). The combined
CSV is written outside git; a summary of what was added is written next to this script.

    python test-results/iter6_6a/build_def19_training.py --capture <a.pcap> --capture <b.pcap>
        --out research/dataset/processed/def19/training_local.csv
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402
from scapy.layers import inet, l2  # noqa: E402,F401  (decoders, as the app loads them)
from scapy.utils import PcapReader  # noqa: E402

from services.flow_tracker_service import (  # noqa: E402
    FLOW_FEATURE_COLUMNS,
    FlowTracker,
    packet_info_from_scapy,
)

BASE = ROOT / "research" / "dataset" / "processed" / "flow_window_v2" / "training.csv"


def capture_flows(path):
    tracker, flows = FlowTracker(), []
    with PcapReader(str(path)) as reader:
        for packet in reader:
            info = packet_info_from_scapy(packet)
            if info is not None:
                flows.extend(tracker.add_packet(info))
    flows.extend(tracker.flush())
    return [flow["features"] for flow in flows]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture", type=Path, action="append", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    base = pd.read_csv(BASE)
    summary = {"base_rows": len(base), "base_attack_share": round(float(base["label"].mean()), 4),
               "captures": {}}
    added = []
    for path in args.capture:
        rows = capture_flows(path)
        summary["captures"][path.name] = {
            "flows": len(rows),
            "by_proto": dict(Counter(row["proto"] for row in rows).most_common()),
            "by_service": dict(Counter(row["service"] for row in rows).most_common()),
            "by_state": dict(Counter(row["state"] for row in rows).most_common()),
        }
        added.extend(rows)
    local = pd.DataFrame(added, columns=list(FLOW_FEATURE_COLUMNS)).assign(label=0)
    combined = pd.concat([base, local[base.columns]], ignore_index=True)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(args.out, index=False)
    summary.update({"local_rows": len(local), "combined_rows": len(combined),
                    "combined_attack_share": round(float(combined["label"].mean()), 4)})
    report = Path(__file__).resolve().parent / "def19_training_data.json"
    report.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
