"""TC-11 / DEF-17 re-test: live event-to-verdict lag through the monitor's own code path.

Captures from a live interface with the same LiveCaptureSource, collect_batch and
classify_rows the Live Monitor uses (services/live_monitor_service.py), with a model artifact
loaded from disk, for --seconds. For every flow it records why the flow ended and the lag from
its last packet to its verdict. TC-11 counts flows ended by FIN or RST (idle and active
timeouts are excluded, as the Master Test Plan says). --single classifies one flow at a time
with predict() then predict_proba(), as the monitor did before the fix, for comparison.

Run from the repository folder, inside the venv (Npcap must allow capture):
    python test-results/def_fixes_20261023/live_lag_harness.py --model <stacking.joblib>
        --interface-match "Wi-Fi" --seconds 90 --out <file.json> [--single]
"""
import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import joblib  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from services import live_monitor_service as monitor  # noqa: E402
from services.traffic_source_service import (  # noqa: E402
    LiveCaptureSource,
    list_capture_interfaces,
)


def percentile(values, share):
    if not values:
        return None
    ordered = sorted(values)
    index = max(int(round(share * len(ordered) + 0.5)) - 1, 0)  # nearest-rank
    return round(ordered[min(index, len(ordered) - 1)], 1)


def classify_single(pipeline, rows, columns):
    """The pre-fix path: predict() and predict_proba() for each flow separately."""
    verdicts, total = [], 0.0
    for row in rows:
        frame = pd.DataFrame([row], columns=columns)
        start = time.perf_counter()
        predicted = int(pipeline.predict(frame)[0])
        probabilities = np.asarray(pipeline.predict_proba(frame))[0]
        total += (time.perf_counter() - start) * 1000
        verdicts.append((predicted, float(probabilities[list(pipeline.classes_).index(predicted)])))
    return verdicts, total


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--interface-match", default="Wi-Fi")
    parser.add_argument("--seconds", type=float, default=90.0)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--single", action="store_true")
    args = parser.parse_args()

    artifact = joblib.load(args.model)
    pipeline, columns = artifact["pipeline"], artifact["feature_columns"]
    numeric = set(artifact.get("numeric_columns") or [])
    defaults = artifact.get("feature_defaults") or {}
    interface = next(item for item in list_capture_interfaces()
                     if args.interface_match.lower() in item["label"].lower())

    warmup = monitor._build_flow_row(dict(defaults), columns, numeric, defaults)
    monitor.classify_rows(pipeline, [warmup], columns)

    source = LiveCaptureSource(interface["value"])
    source.prepare()
    flows, batches = [], []
    started = time.time()
    try:
        while time.time() - started < args.seconds:
            event = source.next_event(timeout=0.2)
            if event is None:
                continue
            batch = [event] if args.single else monitor.collect_batch(source, event, False)[0]
            rows = [monitor._build_flow_row(item["record"], columns, numeric, defaults)
                    for item in batch]
            if args.single:
                verdicts, model_ms = classify_single(pipeline, rows, columns)
            else:
                verdicts, model_ms = monitor.classify_rows(pipeline, rows, columns)
            now = time.time()
            batches.append({"size": len(batch), "model_ms": round(model_ms, 1)})
            for item, (label, _confidence) in zip(batch, verdicts):
                flows.append({
                    "end_reason": item["end_reason"],
                    "lag_ms": round(max((now - item["flow_last_ts"]) * 1000, 0.0), 1),
                    "prediction": "Attack" if label == 1 else "Normal",
                    "service": item["record"].get("service"),
                })
        stats = source.stats()
    finally:
        source.close()

    teardown = [f["lag_ms"] for f in flows if f["end_reason"] in ("fin", "rst")]
    reasons = {}
    for flow in flows:
        reasons[flow["end_reason"]] = reasons.get(flow["end_reason"], 0) + 1
    report = {
        "mode": "single (pre-fix path)" if args.single else "batched (fixed path)",
        "model": str(args.model), "feature_columns": len(columns),
        "interface": interface["label"].split(" (")[0],
        "started": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(started)),
        "seconds": args.seconds,
        "capture_counters": stats,
        "flows": len(flows), "end_reasons": reasons,
        "lag_teardown_ms (fin/rst, excludes timeouts)": {
            "n": len(teardown), "p50": percentile(teardown, 0.5),
            "p95": percentile(teardown, 0.95), "max": max(teardown) if teardown else None,
            "mean": round(statistics.mean(teardown), 1) if teardown else None,
        },
        "model_call_ms": {"n": len(batches),
                          "p50": percentile([b["model_ms"] for b in batches], 0.5),
                          "p95": percentile([b["model_ms"] for b in batches], 0.95)},
        "batch_size": {"max": max((b["size"] for b in batches), default=0),
                       "mean": round(statistics.mean(b["size"] for b in batches), 2)
                       if batches else None},
        "attack_share": round(sum(f["prediction"] == "Attack" for f in flows) / len(flows), 4)
        if flows else None,
        "flows_detail": flows,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "flows_detail"}, indent=2))


if __name__ == "__main__":
    main()
