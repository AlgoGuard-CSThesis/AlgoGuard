"""Stage 6A.1: compare AlgoGuard's packet-derived features with Argus on the same capture.

Tolerances are the ones fixed in protocol_6a1.md before this comparison. AlgoGuard's flows come
from the same FlowTracker live capture and PCAP replay use; Argus's from argus_flows.csv
(argus -F argus.conf -J, racluster by 5-tuple, ra). Writes fidelity_6a1.json.

    python test-results/iter6_6a/compare_fidelity.py --pcap <file.pcap> --argus <argus_flows.csv>
"""
import argparse
import json
import statistics
import sys
from collections import deque
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scapy.layers import inet, l2  # noqa: E402,F401  (decoders, as the app loads them)
from scapy.utils import PcapReader  # noqa: E402

from services.flow_tracker_service import (  # noqa: E402
    SERVICE_PORTS,
    FlowTracker,
    packet_info_from_scapy,
)

SUPPORTED_SHARE = 0.90
ETHERNET_HEADER = 14


MTU = 1500
# Connection keys with a TCP packet bigger than the MTU: a large send captured before the network
# card segmented it (IP length 0, which Argus drops), or received segments Windows merged.
OFFLOADED = set()


def algoguard_flows(pcap):
    tracker = FlowTracker()
    flows = []
    with PcapReader(str(pcap)) as reader:
        for packet in reader:
            info = packet_info_from_scapy(packet)
            if info is not None:
                ip = packet.getlayer("IP")
                if info.proto == "tcp" and len(bytes(ip)) > MTU:
                    OFFLOADED.add(key(info.proto, info.src_ip, info.src_port, info.dst_ip,
                                      info.dst_port))
                flows.extend(tracker.add_packet(info))
    flows.extend(tracker.flush())
    return flows


def argus_flows(path):
    data = pd.read_csv(path)
    data = data[data["Proto"].isin(["tcp", "udp", "icmp"])]
    data = data[~data["SrcAddr"].astype(str).str.contains(":")]  # IPv4 only, like AlgoGuard
    # Argus writes ICMP type/code in the port columns (e.g. 0x0100); AlgoGuard uses port 0.
    for column in ("Sport", "Dport"):
        data[column] = [0 if proto == "icmp" else int(str(value), 0)
                        for proto, value in zip(data["Proto"], data[column])]
    return data.reset_index(drop=True)


def key(proto, a, ap, b, bp):
    return proto, frozenset({(str(a), int(ap)), (str(b), int(bp))})


def service_for(sport, dport):
    return SERVICE_PORTS.get(int(dport)) or SERVICE_PORTS.get(int(sport)) or "-"


def reference_counts(ref):
    """The connection counts recomputed from Argus's flow list with AlgoGuard's definition."""
    window, counts = deque(maxlen=100), {}
    for index in ref.sort_values("LastTime").index:
        row = ref.loc[index]
        src, dst = str(row["SrcAddr"]), str(row["DstAddr"])
        service = service_for(row["Sport"], row["Dport"])
        window.append((src, dst, service))
        counts[index] = {
            "ct_dst_src_ltm": sum(1 for s, d, _ in window if s == src and d == dst),
            "ct_dst_ltm": sum(1 for _, d, _ in window if d == dst),
            "ct_srv_dst": sum(1 for _, d, sv in window if d == dst and sv == service),
        }
    return counts


def relative(a, b):
    return abs(a - b) / max(abs(a), abs(b))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pcap", type=Path, required=True)
    parser.add_argument("--argus", type=Path, required=True)
    parser.add_argument("--out", type=Path,
                        default=Path(__file__).resolve().parent / "fidelity_6a1.json")
    args = parser.parse_args()
    ours = algoguard_flows(args.pcap)
    ref = argus_flows(args.argus)
    ref_counts = reference_counts(ref)
    by_key = {}
    for index, row in ref.iterrows():
        by_key.setdefault(key(row["Proto"], row["SrcAddr"], row["Sport"], row["DstAddr"],
                              row["Dport"]), []).append(index)

    checks = {name: [] for name in ("proto", "spkts", "dpkts", "sbytes", "dbytes", "dur", "rate",
                                    "sload", "dload", "sinpkt", "dinpkt", "state", "service",
                                    "ct_dst_src_ltm", "ct_dst_ltm", "ct_srv_dst")}
    offloaded_flags = []
    ip_layer_loads = {"sload": [], "dload": []}
    differences = {name: [] for name in checks}
    matched_ref, unmatched_ours, examples = set(), [], []
    for flow in ours:
        f = flow["features"]
        k = key(flow["protocol"], flow["source_ip"], flow["source_port"], flow["destination_ip"],
                flow["destination_port"])
        candidates = [i for i in by_key.get(k, []) if i not in matched_ref
                      and abs(ref.loc[i, "StartTime"] - flow["first_ts"]) <= 1.0]
        if not candidates:
            unmatched_ours.append({"proto": flow["protocol"], "end_reason": flow["end_reason"],
                                   "packets": f["spkts"] + f["dpkts"]})
            continue
        i = candidates[0]
        matched_ref.add(i)
        offloaded_flags.append(k in OFFLOADED)
        r = ref.loc[i]
        same_direction = str(r["SrcAddr"]) == flow["source_ip"] and int(r["Sport"]) == int(
            flow["source_port"])
        s, d = ("Src", "Dst") if same_direction else ("Dst", "Src")
        si, di = ("SIntPkt", "DIntPkt") if same_direction else ("DIntPkt", "SIntPkt")
        ref_values = {
            "spkts": r[f"{s}Pkts"], "dpkts": r[f"{d}Pkts"], "sbytes": r[f"{s}Bytes"],
            "dbytes": r[f"{d}Bytes"], "dur": r["Dur"], "rate": r["Rate"], "sload": r[f"{s}Load"],
            "dload": r[f"{d}Load"], "sinpkt": r[si], "dinpkt": r[di]}
        checks["proto"].append(f["proto"] == r["Proto"])
        for name in ("spkts", "dpkts"):
            checks[name].append(int(f[name]) == int(ref_values[name]))
            differences[name].append(int(f[name]) - int(ref_values[name]))
        for name, packets in (("sbytes", f["spkts"]), ("dbytes", f["dpkts"])):
            ours_b, ref_b = int(f[name]), int(ref_values[name])
            checks[name].append(ours_b == ref_b or ours_b + ETHERNET_HEADER * packets == ref_b)
            differences[name].append(ref_b - ours_b - ETHERNET_HEADER * packets)
        dur_diff = abs(float(f["dur"]) - float(ref_values["dur"]))
        longer = max(float(f["dur"]), float(ref_values["dur"]))
        checks["dur"].append(dur_diff <= 0.001 + 0.01 * longer)
        differences["dur"].append(round(float(f["dur"]) - float(ref_values["dur"]), 6))
        for name in ("rate", "sload", "dload"):
            a, b = float(f[name]), float(ref_values[name])
            agree = (a <= 1e-6 and b <= 1e-6) or (max(a, b) > 0 and relative(a, b) <= 0.05)
            checks[name].append(agree)
            if max(a, b) > 0:
                differences[name].append(round(relative(a, b), 4))
        # Diagnostic: Argus's load counts the 14-byte Ethernet header per packet, UNSW-NB15's load
        # (and AlgoGuard's) does not; compare with Argus's load taken at the IP layer.
        for name, side in (("sload", s), ("dload", d)):
            ref_b, ref_p = float(r[f"{side}Bytes"]), float(r[f"{side}Pkts"])
            ip_share = (ref_b - ETHERNET_HEADER * ref_p) / ref_b if ref_b else 0.0
            b = float(ref_values[name]) * ip_share
            a = float(f[name])
            ip_layer_loads[name].append((a <= 1e-6 and b <= 1e-6)
                                        or (max(a, b) > 0 and relative(a, b) <= 0.05))
        for name in ("sinpkt", "dinpkt"):
            a = float(f[name])
            b = 0.0 if pd.isna(ref_values[name]) else float(ref_values[name])
            agree = (a <= 0.001 and b <= 0.001) or (max(a, b) > 0 and relative(a, b) <= 0.05)
            checks[name].append(agree)
            if max(a, b) > 0:
                differences[name].append(round(relative(a, b), 4))
        checks["state"].append(f["state"] == r["State"])
        if f["state"] != r["State"]:
            differences["state"].append(f"{f['state']} vs {r['State']}")
        checks["service"].append(f["service"] == service_for(flow["source_port"],
                                                             flow["destination_port"]))
        for name in ("ct_dst_src_ltm", "ct_dst_ltm", "ct_srv_dst"):
            checks[name].append(int(f[name]) == ref_counts[i][name])
            differences[name].append(int(f[name]) - ref_counts[i][name])
        if len(examples) < 5:
            examples.append({"ours": {k2: f[k2] for k2 in ("proto", "state", "spkts", "dpkts",
                                                           "sbytes", "dbytes", "dur", "sinpkt")},
                             "argus": {"state": r["State"], **{k2: ref_values[k2] for k2 in (
                                 "spkts", "dpkts", "sbytes", "dbytes", "dur", "sinpkt")}}})

    features = {}
    for name, results in checks.items():
        share = sum(results) / len(results) if results else None
        diffs = differences[name]
        numeric = [x for x in diffs if isinstance(x, (int, float))]
        features[name] = {
            "matched": len(results), "agree": sum(results),
            "share": round(share, 4) if share is not None else None,
            "supported": share is not None and share >= SUPPORTED_SHARE,
            "median_difference": statistics.median(numeric) if numeric else None,
            "examples_of_disagreement": [x for x in diffs if not isinstance(x, (int, float))][:8],
        }
    # Diagnostic only (the official result above covers every matched flow): flows without a packet
    # bigger than the MTU, where the capture and the wire agree packet for packet.
    clean = [not flag for flag in offloaded_flags]
    without_offload = {
        name: round(sum(r for r, ok in zip(results, clean) if ok) / max(sum(clean), 1), 4)
        for name, results in checks.items() if len(results) == len(clean)}
    unmatched_ref = ref.drop(index=list(matched_ref))
    report = {
        "capture": str(args.pcap.name), "reference": str(args.argus.name),
        "algoguard_flows": len(ours),
        "argus_ipv4_flows": len(ref), "matched": len(matched_ref),
        "unmatched_algoguard": len(unmatched_ours),
        "unmatched_algoguard_by_end_reason": pd.Series(
            [u["end_reason"] for u in unmatched_ours]).value_counts().to_dict(),
        "unmatched_argus": len(unmatched_ref),
        "unmatched_argus_by_proto": unmatched_ref["Proto"].value_counts().to_dict(),
        "features": features,
        "diagnostic_flows_with_packet_over_mtu": sum(offloaded_flags),
        "diagnostic_share_agreeing_without_such_flows": without_offload,
        "diagnostic_load_share_agreeing_with_argus_bytes_at_ip_layer": {
            name: round(sum(v) / len(v), 4) if v else None for name, v in ip_layer_loads.items()},
        "examples": examples,
    }
    args.out.write_text(json.dumps(report, indent=2, default=float), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "examples"}, indent=2,
                     default=float))
    return 0


if __name__ == "__main__":
    sys.exit(main())
