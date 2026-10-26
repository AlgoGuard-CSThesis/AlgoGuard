"""Stage 6A.1: latency figures, each with its load and sample size (protocol_6a1.md, Latency).

1. Inference: one ``classify_rows`` call (the monitor's single ``predict_proba``) for batches of
   1, 8 and 64 rows of datasets/algoguard_big.csv, ``--repeats`` calls per size, no other load.
2. Event to verdict: live capture through the monitor's own LiveCaptureSource, collect_batch and
   classify_rows while traffic_gen.py loads web pages, for ``--seconds``. Flows ended by FIN or
   RST: lag from the last packet to the verdict. Flows ended by the idle timeout: lag from the
   moment the flow could expire (last packet + idle timeout) to the verdict; the timeout itself
   is reported beside it. Packet and flow rates during the run are the load.

`--scorer process` scores live flows in the separate inference process the monitor uses since the
DEF-17 fix (services/inference_worker.py) instead of in this process.

Upload (synchronisation) delay is not re-measured here: the figures on the final cycle-1 build
are in test-results/def_fixes_20261023/upload_delay_c775d13.json. No addresses are recorded.

    python test-results/iter6_6a/latency_6a1.py --model saved_models/run_8/stacking.joblib
        --seconds 120
"""
import argparse
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import joblib  # noqa: E402
import pandas as pd  # noqa: E402

from services import live_monitor_service as monitor  # noqa: E402
from services.flow_tracker_service import IDLE_TIMEOUT_SECONDS  # noqa: E402
from services.inference_worker import LiveScorer  # noqa: E402
from services.traffic_source_service import (  # noqa: E402
    LiveCaptureSource,
    list_capture_interfaces,
)

BATCH_SIZES = (1, 8, 64)


def percentile(values, share):
    if not values:
        return None
    ordered = sorted(values)
    index = max(int(round(share * len(ordered) + 0.5)) - 1, 0)  # nearest-rank
    return round(ordered[min(index, len(ordered) - 1)], 1)


def summary(values):
    return {"n": len(values), "p50": percentile(values, 0.5), "p95": percentile(values, 0.95),
            "max": round(max(values), 1) if values else None,
            "mean": round(statistics.mean(values), 1) if values else None}


def inference(pipeline, columns, numeric, defaults, repeats):
    data = pd.read_csv(ROOT / "datasets" / "algoguard_big.csv").head(max(BATCH_SIZES))
    rows = [monitor._build_flow_row(record, columns, numeric, defaults)
            for record in data.to_dict("records")]
    results = {}
    for size in BATCH_SIZES:
        times = [monitor.classify_rows(pipeline, rows[:size], columns)[1] for _ in range(repeats)]
        results[f"batch_{size}"] = {**summary(times),
                                    "per_flow_p50_ms": round(percentile(times, 0.5) / size, 2)}
    return results


def live(pipeline, columns, numeric, defaults, seconds, scorer_kind="in-process"):
    interface = next(item for item in list_capture_interfaces()
                     if "wi-fi" in item["label"].lower())
    scorer = (LiveScorer(pipeline, columns, monitor.score_rows)
              if scorer_kind == "process" else None)
    if scorer is not None:
        scorer.score([monitor._build_flow_row(dict(defaults), columns, numeric, defaults)])
    source = LiveCaptureSource(interface["value"])
    source.prepare()
    generator = subprocess.Popen(
        [sys.executable, str(ROOT / "test-results/def_fixes_20261023/traffic_gen.py"),
         "--seconds", str(int(seconds))], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    flows, batches = [], []
    started = time.time()
    try:
        while time.time() - started < seconds:
            event = source.next_event(timeout=0.2)
            if event is None:
                continue
            batch = monitor.collect_batch(source, event, False)[0]
            rows = [monitor._build_flow_row(item["record"], columns, numeric, defaults)
                    for item in batch]
            if scorer is not None:
                _verdicts, model_ms = scorer.score(rows)
            else:
                _verdicts, model_ms = monitor.classify_rows(pipeline, rows, columns)
            now = time.time()
            batches.append({"size": len(batch), "model_ms": model_ms})
            for item in batch:
                lag = (now - item["flow_last_ts"]) * 1000
                if item["end_reason"] == "idle_timeout":
                    lag -= IDLE_TIMEOUT_SECONDS * 1000
                flows.append({"end_reason": item["end_reason"], "lag_ms": max(lag, 0.0)})
        stats = source.stats()
    finally:
        source.close()
        generator.wait()
        if scorer is not None:
            scorer.close()
    elapsed = time.time() - started

    def lags(*reasons):
        return [f["lag_ms"] for f in flows if f["end_reason"] in reasons]

    return {
        "interface": interface["label"].split(" (")[0],
        "scorer": scorer_kind if scorer is None or scorer.fallback_reason is None
        else f"in-process (fallback: {scorer.fallback_reason})",
        "seconds": round(elapsed, 1),
        "load": {"packets_per_s": round(stats["packets"] / elapsed, 1),
                 "flows_per_s": round(len(flows) / elapsed, 2),
                 "dropped_packets": stats["dropped"],
                 "batch_size_mean": round(statistics.mean(b["size"] for b in batches), 2)
                 if batches else None,
                 "batch_size_max": max((b["size"] for b in batches), default=0)},
        "end_reasons": {reason: len(lags(reason))
                        for reason in sorted({f["end_reason"] for f in flows})},
        "lag_teardown_ms (fin/rst, last packet to verdict)": summary(lags("fin", "rst")),
        "lag_idle_ms (expiry to verdict; the idle timeout comes first)": summary(
            lags("idle_timeout")),
        "idle_timeout_s": IDLE_TIMEOUT_SECONDS,
        "model_call_ms_live": summary([b["model_ms"] for b in batches]),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--seconds", type=float, default=120.0)
    parser.add_argument("--repeats", type=int, default=200)
    parser.add_argument("--scorer", choices=("in-process", "process"), default="in-process")
    parser.add_argument("--out", type=Path,
                        default=Path(__file__).resolve().parent / "latency_6a1.json")
    args = parser.parse_args()

    monitor.prefer_low_latency()
    artifact = joblib.load(args.model)
    pipeline, columns = artifact["pipeline"], artifact["feature_columns"]
    numeric = set(artifact.get("numeric_columns") or [])
    defaults = artifact.get("feature_defaults") or {}
    warmup = monitor._build_flow_row(dict(defaults), columns, numeric, defaults)
    monitor.classify_rows(pipeline, [warmup], columns)

    report = {
        "model": args.model.as_posix(),
        "started": time.strftime("%Y-%m-%d %H:%M:%S"),
        "inference_ms (no other load)": inference(pipeline, columns, numeric, defaults,
                                                  args.repeats),
        "event_to_verdict": live(pipeline, columns, numeric, defaults, args.seconds, args.scorer),
        "upload_delay": "test-results/def_fixes_20261023/upload_delay_c775d13.json",
    }
    args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
