"""Shared paths and helpers for training run 13 (protocol_run13.md). Never reads the final test."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scapy.layers import inet, l2  # noqa: E402,F401  (decoders, as the app loads them)
from scapy.utils import PcapReader  # noqa: E402

from services.flow_tracker_service import (  # noqa: E402
    CONNECTION_COUNT_COLUMNS,
    FLOW_FEATURE_COLUMNS,
    FlowTracker,
    packet_info_from_scapy,
)

HERE = Path(__file__).resolve().parent
PROCESSED = ROOT / "research" / "dataset" / "processed"
RUN11 = PROCESSED / "run11"
RUN13 = PROCESSED / "run13"
HOLDOUT = PROCESSED / "flow_window_v2" / "holdout_eval.csv"
RECORDINGS = PROCESSED / "def19"
SELECTION_RECORDINGS = ("generator.pcap", "browser.pcap", "final_browser.pcap", "final_unseen.pcap")
# Run 11's training file: 66,393 file-1 rows, then 2,460 PC A benign flows, then 76,089 file-2 rows.
FILE1_ROWS, LOCAL_ROWS, FILE2_ROWS = 66_393, 2_460, 76_089
LOCAL_WEIGHT = 10
KEY = [c for c in FLOW_FEATURE_COLUMNS if c not in CONNECTION_COUNT_COLUMNS]
LINES = tuple(round(0.20 + 0.05 * step, 2) for step in range(7))  # 0.20, 0.25, ... 0.50
LIMITS = {"accuracy": 0.90, "f1": 0.90, "recall": 0.90, "fpr": 0.08, "browsing": 0.08}
MAX_ARTIFACT_BYTES = 50 * 1024 * 1024


def key_groups(frame):
    """One group id per 13-feature key, the same in every process (unlike Python's hash())."""
    norm = pd.DataFrame(index=frame.index)
    for column in KEY:
        values = frame[column]
        if pd.api.types.is_numeric_dtype(values):
            norm[column] = values.astype(float).round(9)
        else:
            norm[column] = values.astype(str).str.strip()
    return pd.util.hash_pandas_object(norm, index=False).to_numpy()


def recording_flows(path):
    """The 16 features of every flow in a recording, derived by the app's own flow tracker."""
    tracker, flows = FlowTracker(), []
    with PcapReader(str(path)) as reader:
        for packet in reader:
            info = packet_info_from_scapy(packet)
            if info is not None:
                flows.extend(tracker.add_packet(info))
    flows.extend(tracker.flush())
    return pd.DataFrame([flow["features"] for flow in flows], columns=list(FLOW_FEATURE_COLUMNS))


def rates(y, scores, line):
    """Accuracy, F1, recall and false-positive rate when scores at or above ``line`` are attacks."""
    y = np.asarray(y).astype(int)
    pred = np.asarray(scores) >= line
    tp = int((pred & (y == 1)).sum())
    fp = int((pred & (y == 0)).sum())
    fn = int((~pred & (y == 1)).sum())
    tn = int((~pred & (y == 0)).sum())
    recall = tp / max(tp + fn, 1)
    precision = tp / max(tp + fp, 1)
    return {"accuracy": (tp + tn) / max(len(y), 1),
            "f1": 2 * precision * recall / max(precision + recall, 1e-12),
            "recall": recall, "fpr": fp / max(fp + tn, 1)}


def attack_scores(pipeline, frame, columns):
    """The Attack column of ``predict_proba`` (found by class value, not position)."""
    probabilities = np.asarray(pipeline.predict_proba(frame[list(columns)]))
    return probabilities[:, list(pipeline.classes_).index(1)]
