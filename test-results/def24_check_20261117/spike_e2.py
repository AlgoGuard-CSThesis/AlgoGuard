"""Evidence for DEF-24 (2026-11-17), moved from the session scratchpad; see summary.md.

THROWAWAY spike (DEF-24), part 2: when a model trains on BOTH UNSW-NB15 files (as run 12 did),
do extra measurements stop the false alarms that sank run 12?

Never reads research/dataset/processed/run11/final_test.csv.

Training per fold: F1train (66,393) + local benign flows from train_generator/train_browser pcaps
(re-derived with an extended tracker so every feature exists) + 2 of 3 grouped folds of S2.
Scored on: the held S2 fold (within-file, pooled), the hold-out HO (file 1), and the four benign
selection captures (generator, browser, final_browser, final_unseen).
The extended tracker is a monkeypatch of services/flow_tracker_service (not saved anywhere).
"""
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.model_selection import StratifiedGroupKFold

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scapy.layers import inet, l2  # noqa: E402,F401
from scapy.utils import PcapReader  # noqa: E402
from spike_e1 import ROOT, V2, keys, load, metrics, model  # noqa: E402

from services import flow_tracker_service as fts  # noqa: E402

OUT = Path(__file__).resolve().parent
PCAPS = ROOT / "research/dataset/processed/def19"
TRAIN_CAPS = ["train_generator.pcap", "train_browser.pcap"]
SEL_CAPS = ["generator.pcap", "browser.pcap", "final_browser.pcap", "final_unseen.pcap"]
EXTRA = ["smean", "dmean", "synack", "ackdat", "tcprtt"]
SETS = {
    "v2": V2,
    "v2+size_mean": V2 + ["smean", "dmean"],
    "v2+handshake": V2 + ["tcprtt", "synack", "ackdat"],
    "v2+size+handshake": V2 + ["smean", "dmean", "tcprtt", "synack", "ackdat"],
}

_orig_add = fts.FlowTracker._add_to_flow
_orig_cf = fts.compute_features


def _add(flow, info, is_forward):
    _orig_add(flow, info, is_forward)
    if info.proto != "tcp":
        return
    f = info.tcp_flags or ""
    if "S" in f and "A" not in f and is_forward and getattr(flow, "syn_ts", None) is None:
        flow.syn_ts = info.ts
    elif "S" in f and "A" in f and not is_forward and getattr(flow, "synack_ts", None) is None:
        flow.synack_ts = info.ts
    elif ("A" in f and "S" not in f and is_forward and getattr(flow, "synack_ts", None) is not None
          and getattr(flow, "ack_ts", None) is None):
        flow.ack_ts = info.ts


def _cf(flow):
    d = _orig_cf(flow)
    d["smean"] = int(flow.sbytes / flow.spkts) if flow.spkts else 0
    d["dmean"] = int(flow.dbytes / flow.dpkts) if flow.dpkts else 0
    syn, sa, ack = (getattr(flow, n, None) for n in ("syn_ts", "synack_ts", "ack_ts"))
    synack = (sa - syn) if syn is not None and sa is not None else 0.0
    ackdat = (ack - sa) if sa is not None and ack is not None else 0.0
    d["synack"], d["ackdat"], d["tcprtt"] = synack, ackdat, synack + ackdat
    return d


fts.FlowTracker._add_to_flow = staticmethod(_add)
fts.compute_features = _cf


def pcap_flows(name):
    cache = OUT / f"flows_{name}.csv"
    if cache.exists():
        return pd.read_csv(cache)
    tracker, flows = fts.FlowTracker(), []
    with PcapReader(str(PCAPS / name)) as reader:
        for packet in reader:
            info = fts.packet_info_from_scapy(packet)
            if info is not None:
                flows.extend(tracker.add_packet(info))
    flows.extend(tracker.flush())
    frame = pd.DataFrame([f["features"] for f in flows])
    frame.to_csv(cache, index=False)
    return frame


def main():
    t0 = time.time()
    f1, ho, s2 = load()
    for frame in (f1, ho, s2):
        frame["service"] = frame["service"].astype(str)
    # Is smean/dmean just bytes/packets in the dataset?
    derived = {}
    for col, b, p in (("smean", "sbytes", "spkts"), ("dmean", "dbytes", "dpkts")):
        both = pd.concat([f1, s2])
        calc = np.where(both[p] > 0, both[b] // both[p].clip(lower=1), 0)
        derived[col] = round(float((np.abs(both[col] - calc) <= 1).mean()), 4)
    print("smean/dmean == bytes//packets (within 1):", derived, flush=True)

    caps = {}
    for name in TRAIN_CAPS + SEL_CAPS:
        t = time.time()
        caps[name] = pcap_flows(name)
        caps[name]["service"] = caps[name]["service"].astype(str)
        print(f"{name}: {len(caps[name])} flows ({time.time()-t:.0f}s)", flush=True)
    local = pd.concat([caps[n] for n in TRAIN_CAPS], ignore_index=True).assign(
        label=0, attack_cat="Normal (local)")

    # Where do live TCP handshake timings sit against the dataset's normal rows?
    live_tcp = pd.concat([caps[n] for n in SEL_CAPS])
    live_tcp = live_tcp[(live_tcp.proto == "tcp") & (live_tcp.synack > 0)]
    ds_norm = pd.concat([f1, s2])
    ds_norm = ds_norm[(ds_norm.proto == "tcp") & (ds_norm.label == 0) & (ds_norm.synack > 0)]
    ds_att = pd.concat([f1, s2])
    ds_att = ds_att[(ds_att.proto == "tcp") & (ds_att.label == 1) & (ds_att.synack > 0)]
    q = [0.1, 0.5, 0.9]
    timing = {c: {"live_benign": [round(float(v), 5) for v in live_tcp[c].quantile(q)],
                  "dataset_normal": [round(float(v), 5) for v in ds_norm[c].quantile(q)],
                  "dataset_attack": [round(float(v), 5) for v in ds_att[c].quantile(q)]}
              for c in ("synack", "ackdat", "tcprtt")}
    print("timing quantiles 10/50/90%:", json.dumps(timing), flush=True)

    splitter = StratifiedGroupKFold(n_splits=3, shuffle=True, random_state=7)
    folds = list(splitter.split(s2, s2["attack_cat"], keys(s2)))
    results = {}
    for name, feats in SETS.items():
        t = time.time()
        # Anchor: no file-2 rows (model 60's situation).
        anchor = model(feats).fit(pd.concat([f1[feats], local[feats]]),
                                  pd.concat([f1["label"], local["label"]]))
        anchor_caps = {c: round(float((anchor.predict_proba(caps[c][feats])[:, 1] >= 0.5)
                                      .mean()), 4) for c in SEL_CAPS}
        pooled = np.zeros(len(s2))
        ho_m, cap_share, cap_share70 = [], {c: [] for c in SEL_CAPS}, {c: [] for c in SEL_CAPS}
        for tr, te in folds:
            train = pd.concat([f1[feats + ["label"]], local[feats + ["label"]],
                               s2.iloc[tr][feats + ["label"]]], ignore_index=True)
            m = model(feats).fit(train[feats], train["label"])
            pooled[te] = m.predict_proba(s2.iloc[te][feats])[:, 1]
            ho_m.append(metrics(ho["label"].to_numpy(), m.predict_proba(ho[feats])[:, 1],
                                ho["attack_cat"].to_numpy()))
            for c in SEL_CAPS:
                p = m.predict_proba(caps[c][feats])[:, 1]
                cap_share[c].append(float((p >= 0.5).mean()))
                cap_share70[c].append(float((p >= 0.7).mean()))
        y, cats = s2["label"].to_numpy(), s2["attack_cat"].to_numpy()
        results[name] = {
            "anchor_no_file2_caps_attack_share": anchor_caps,
            "S2_withinfile_0.50": metrics(y, pooled, cats),
            "S2_withinfile_0.70": metrics(y, (pooled >= 0.7).astype(float), cats),
            "HO_mean": {k: round(float(np.mean([d[k] for d in ho_m])), 4) for k in ho_m[0]},
            "caps_attack_share_0.50": {c: round(float(np.mean(v)), 4)
                                       for c, v in cap_share.items()},
            "caps_attack_share_0.70": {c: round(float(np.mean(v)), 4)
                                       for c, v in cap_share70.items()},
        }
        r = results[name]
        print(f"\n== {name} ({time.time()-t:.0f}s)\n  S2@0.50 {r['S2_withinfile_0.50']}\n"
              f"  S2@0.70 {r['S2_withinfile_0.70']}\n  HO {r['HO_mean']}\n"
              f"  caps@0.50 {r['caps_attack_share_0.50']}\n"
              f"  caps@0.70 {r['caps_attack_share_0.70']}\n"
              f"  anchor caps {r['anchor_no_file2_caps_attack_share']}", flush=True)

    (OUT / "e2_results.json").write_text(json.dumps(
        {"smean_dmean_derived_share": derived, "timing_quantiles": timing, "e2": results},
        indent=2), encoding="utf-8")
    print(f"done {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
