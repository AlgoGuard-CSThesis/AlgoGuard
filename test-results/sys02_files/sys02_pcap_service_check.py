"""SYS-02 service-level check: TC-04 sample PCAP and the three bad files through the app's own
PCAP reader (services.traffic_source_service.PcapReplaySource), with each sample flow classified by
the deployed model A through predict_with_artifact - the same calls the Live Monitor makes.
No web route, no storage, no upload. Synthetic capture files only.
"""
import json
import sys
from pathlib import Path

import joblib

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from services.simulation_service import predict_with_artifact  # noqa: E402
from services.traffic_source_service import PcapReplaySource, TrafficSourceError  # noqa: E402

artifact = joblib.load(ROOT / "saved_models/run_3/stacking.joblib")
results = []
for name in ("cycle1-tc04-sample.pcap", "cycle1-bad-empty.pcap", "cycle1-bad-text.pcap",
             "cycle1-bad-truncated.pcap"):
    source = PcapReplaySource(str(ROOT / "captures" / name), order="sequential")
    entry = {"file": name}
    try:
        source.prepare()
        verdicts = []
        while True:
            try:
                event = source.next_event()
            except StopIteration:
                break
            if event is None:
                continue
            outcome = predict_with_artifact(artifact, event["record"])
            verdicts.append({"end_reason": event["end_reason"], "protocol": event["protocol"],
                             "prediction": outcome["prediction"],
                             "confidence": outcome["confidence"]})
        entry.update(outcome_type="read", packets=source.stats().get("packets"),
                     flows=len(verdicts),
                     attack=sum(v["prediction"] == "Attack" for v in verdicts),
                     normal=sum(v["prediction"] == "Normal" for v in verdicts),
                     end_reasons=sorted({v["end_reason"] for v in verdicts}))
    except TrafficSourceError as error:
        entry.update(outcome_type="refused (TrafficSourceError)", message=str(error))
    except Exception as error:  # anything else would be a crash in the app
        entry.update(outcome_type=f"UNHANDLED {type(error).__name__}", message=str(error))
    finally:
        source.close()
    results.append(entry)
print(json.dumps(results, indent=2))
