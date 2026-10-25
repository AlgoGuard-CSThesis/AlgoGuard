# Stage 6A.1 — feature-fidelity protocol (written before any comparison was made)

Written 2026-10-25 (Asia/Manila), before the first reference capture was compared.

## Method

1. Capture packets on PC A's Wi-Fi adapter while `test-results/def_fixes_20261023/traffic_gen.py` loads public home
   pages, plus ICMP pings to the same hosts. A BPF filter keeps only traffic to those hosts' addresses, DNS and ICMP, so
   no other traffic of the PC is recorded. The capture file stays out of git; derived per-flow numbers are the evidence.
2. Derive AlgoGuard features from the capture with `PcapReplaySource` (the same flow tracker live capture uses).
3. Derive reference features from the same file with Argus (`argus` + `racluster` + `ra`, Ubuntu 24.04 packages in
   `test-results/iter6_6a/argus/Dockerfile`), the tool UNSW-NB15's flow features came from. Bro/Zeek, the source of
   UNSW-NB15's `service` column, is not part of this comparison: `service` is checked against the port convention only.
4. Match flows by protocol, endpoints and ports in either direction, and start times within 1 s. Unmatched flows on
   either side are counted and listed, not dropped silently.
5. Recompute the three connection counts from the reference flow list with the same definition (last 100 finished
   flows by end time, including the flow itself) and compare with AlgoGuard's values.

## Tolerances (fixed now)

| Feature | A matched flow agrees when |
|---|---|
| proto | identical |
| spkts, dpkts | equal |
| sbytes, dbytes | equal, or equal after adding 14 bytes (Ethernet header) per packet to AlgoGuard's IP-length count |
| dur | difference ≤ 1 ms + 1% |
| rate, sload, dload | relative difference ≤ 5% (or both ≤ 1e-6) |
| sinpkt, dinpkt | relative difference ≤ 5% (or both ≤ 0.001 ms) |
| state | AlgoGuard's state equals Argus's state for the flow |
| service | equals the port convention AlgoGuard documents (no Bro/Zeek reference) |
| ct_dst_src_ltm, ct_dst_ltm, ct_srv_dst | equal to the recomputed reference value |

A feature is **supported** when at least 90% of matched flows agree. Features below 90% are reported as unsupported
with the observed differences; they are not re-toleranced after the comparison. Flow-boundary differences (timeouts,
teardown handling) are reported separately.

## Latency (reported from existing and new measurements)

Inference time per model call, event-to-verdict lag for flows ended by FIN/RST and by idle timeout, and upload
(synchronisation) delay, each with its load and sample size, kept as separate numbers.
