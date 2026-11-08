"""Stage 6A.1 / DEF-19: share of benign captured flows the deployed model labels Attack.

Derives flows from a capture of generated benign web traffic with a flow tracker, scores them with
the model artifact's own pipeline, and reports the Attack share and the attack-score bands
(Low < 0.50, Medium >= 0.50, High >= 0.70, Very high >= 0.90). ``--tracker`` loads another
version of services/flow_tracker_service.py, to score the same packets with the tracker before
a change (for example ``git show 7498b48:services/flow_tracker_service.py > old_tracker.py``).

    python test-results/iter6_6a/benign_attack_share.py --pcap <file.pcap>
        --model saved_models/run_8/stacking.joblib [--tracker old_tracker.py] [--label name]
"""
import argparse
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import joblib  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from scapy.layers import inet, l2  # noqa: E402,F401  (decoders, as the app loads them)
from scapy.utils import PcapReader  # noqa: E402

from services import flow_tracker_service  # noqa: E402

BANDS = (("very_high", 0.90), ("high", 0.70), ("medium", 0.50), ("low", 0.0))


def load_tracker(path):
    if path is None:
        return flow_tracker_service
    spec = importlib.util.spec_from_file_location("alternate_tracker", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pcap", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--tracker", type=Path)
    parser.add_argument("--label", default="current tracker")
    args = parser.parse_args()

    tracker_module = load_tracker(args.tracker)
    tracker = tracker_module.FlowTracker()
    flows = []
    with PcapReader(str(args.pcap)) as reader:
        for packet in reader:
            info = tracker_module.packet_info_from_scapy(packet)
            if info is not None:
                flows.extend(tracker.add_packet(info))
    flows.extend(tracker.flush())

    artifact = joblib.load(args.model)
    pipeline, columns = artifact["pipeline"], artifact["feature_columns"]
    frame = pd.DataFrame([flow["features"] for flow in flows], columns=columns)
    attack = np.asarray(pipeline.predict_proba(frame))[:, list(pipeline.classes_).index(1)]
    bands = {name: 0 for name, _ in BANDS}
    for score in attack:
        bands[next(name for name, floor in BANDS if score >= floor)] += 1
    by_proto, by_service = {}, {}
    for flow, score in zip(flows, attack):
        for table, value in ((by_proto, flow["features"]["proto"]),
                             (by_service, flow["features"]["service"])):
            entry = table.setdefault(value, [0, 0])
            entry[0] += 1
            entry[1] += int(score >= 0.5)
    print(json.dumps({
        "label": args.label, "capture": args.pcap.name, "model": args.model.as_posix(),
        "flows": len(flows),
        "labelled_attack": int((attack >= 0.5).sum()),
        "attack_share": round(float((attack >= 0.5).mean()), 4),
        "attack_score_median": round(float(np.median(attack)), 4),
        "bands": bands,
        "attack_by_proto": {proto: {"flows": n, "attack": a} for proto, (n, a) in by_proto.items()},
        "attack_by_service": {name: {"flows": n, "attack": a}
                              for name, (n, a) in by_service.items()},
    }, indent=2))


if __name__ == "__main__":
    main()
