# Stage 6A.1 — feature fidelity and latency: results

Protocol: `protocol_6a1.md` (committed in 7498b48 before any comparison). Tolerances were not changed after the
comparison; every number below uses them. Run 2026-10-25 on PC A (Windows 11, Intel Wi-Fi 6 AX203, Npcap), model
deployment #2 (training run 8, Stacking Ensemble, schema v2).

## Reference capture

`capture_fidelity.py`: 120 s of `traffic_gen.py` page loads plus pings, BPF-filtered to the test sites, DNS and
ICMP; 4,223 packets (4,096 IPv4). The capture stays outside git (`research/dataset/processed/fidelity/`).

Reference: Argus 3.0.8.2 and ra 3.0.8.2 (Ubuntu 24.04 packages, `argus/Dockerfile`), run as
`argus -F argus.conf -J -r <pcap> -w <file>`, `racluster -m saddr daddr sport dport proto`, `ra -n -u -c ,`.
Argus's output file must not exist beforehand (`-w` appends). IPv6 flows are excluded (AlgoGuard reads IPv4 only).
Argus reports 108 IPv4 flows; all 108 matched an AlgoGuard flow.

## What the comparison found, and the fixes made

Each fix was checked against UNSW-NB15 itself (`UNSW_NB15_training-set.csv`, 175,341 rows), because the model
learned the dataset's definitions, not Argus's defaults.

| Finding | Evidence | Change in `services/flow_tracker_service.py` |
|---|---|---|
| Load leaves out the first packet | dataset `sload = sbytes·8·(spkts−1)/spkts/dur` in 99.999% of 172,360 rows with dur > 0 | `_load_bps` uses that definition (one-packet direction: 0) |
| State RST wins over FIN | Argus reports RST for a reset after a finish | `_state_for` checks RST first |
| Pings are ECO | all 12 ECO rows in the dataset are ICMP; ICMP is never CON/INT there | ICMP echo flows report ECO |
| Teardown resets made one-packet stray flows | 9 stray RST flows before the fix | a trailing RST after a closed flow is swallowed like the trailing ACK |
| Windows captures on the far side of segmentation offload | 1,106 TCP packets larger than the 1,500-byte MTU: 94 outgoing large sends (IP length 0, which Argus drops) and merged received segments | such a packet counts as the MTU-sized segments a network tap sees (back-to-back gaps) |
| Byte counts are IP-layer | 15.2% of the dataset's TCP rows average < 54 bytes/packet (impossible with an Ethernet header), none < 40 | bytes stay IP-layer; Argus on this capture adds 14 bytes/packet, which the protocol's byte tolerance allows |

Already matching the dataset and unchanged: `rate = (packets−1)/dur` (100% of rows), `sinpkt`/`dinpkt` = mean gap per
direction in ms (median ratio 1.0).

## Results (share of 108 matched flows that agree; supported = at least 90%)

"Recorded" compares with Argus on the capture as recorded. "Wire" compares with Argus on `wire_equivalent.py`'s
copy, in which each packet larger than the MTU is split into MTU-sized segments 1 µs apart. The wire copy is what a
tap would record, but its segment sizes follow the same MSS assumption as the tracker, so `spkts`/`sbytes` agreement on
the 94 flows that contain such a packet is partly by construction.

| Feature | Before fixes | After (recorded) | After (wire) | Supported (wire) |
|---|---|---|---|---|
| proto | 100.0 | 100.0 | 100.0 | yes |
| service (port convention) | 100.0 | 100.0 | 100.0 | yes |
| state | 13.9 | 96.3 | 96.3 | yes |
| spkts | 13.9 | 12.0 | 96.3 | yes |
| sbytes | 12.0 | 12.0 | 96.3 | yes |
| sinpkt | 41.7 | 17.6 | 92.6 | yes |
| rate | 85.2 | 12.0 | 90.7 | yes |
| ct_srv_dst | 88.0 | 93.5 | 93.5 | yes |
| dur | 88.9 | 88.9 | 88.9 | no |
| dload | 43.5 | 84.3 | 87.0 | no |
| ct_dst_src_ltm | 81.5 | 87.0 | 87.0 | no |
| ct_dst_ltm | 81.5 | 87.0 | 87.0 | no |
| dpkts | 81.5 | 13.9 | 81.5 | no |
| dinpkt | 68.5 | 9.3 | 71.3 | no |
| dbytes | 69.4 | 13.0 | 69.4 | no |
| sload | 18.5 | 0.0 | 8.3 | no |

Diagnostic only (not a protocol result): Argus's load includes the 14-byte Ethernet header per packet and the
dataset's does not. With Argus's bytes taken at the IP layer, load agreement on the wire copy is `sload` 89.8% and
`dload` 88.9%.

Files: `fidelity_6a1_before_fixes.json`, `fidelity_6a1.json` (recorded), `fidelity_6a1_wire.json` (wire),
`compare_fidelity.py`, `wire_equivalent.py`.

## Why the unsupported features disagree (flow boundaries, reported separately)

Of 121 AlgoGuard flows matched by 5-tuple and start time against the wire reference (several AlgoGuard flows can
match one Argus flow here), 75 have identical packet counts and IP-layer byte counts. The rest:

- **Teardown (20 flows):** AlgoGuard ends a flow at the first RST, or when both FINs are seen, so the verdict comes
  quickly; Argus keeps counting packets the server sends after that point. This shortens `dur` and lowers
  `dpkts`/`dbytes` and the loads. Waiting for those packets would delay every verdict.
- **Idle timeout (14 flows plus 16 unmatched AlgoGuard flows):** AlgoGuard splits a TCP connection silent for 15 s;
  Argus kept some connections as one flow of up to 90 s. A longer timeout would delay idle-ended verdicts by as much.
  These splits also add flows to the 100-flow window, which is why the connection counts miss 90%.
- **Frame trailers (12 flows):** two servers' frames carry 4 bytes after the IP packet; Argus counts them, the
  dataset's IP-layer bytes do not. Packet counts agree on these flows.

## Effect on DEF-19 (benign traffic labelled Attack)

`benign_attack_share.py`, same capture, run 8 model (`benign_attack_share_6a1.json`):

| Tracker | Flows | Labelled Attack | Median attack score | Very high (≥ 0.90) |
|---|---|---|---|---|
| 7498b48 (before fixes) | 134 | 99 (73.9%) | 0.774 | 49 |
| after fixes | 126 | 92 (73.0%) | 0.693 | 34 |

Closer features lower the scores but not the Attack share. The remaining gap is between UNSW-NB15's 2015 lab traffic
and today's HTTPS browsing, not feature extraction. DEF-19 stays open for the cycle-2 re-test, and live accuracy is
not claimed.

## Latency (`latency_6a1.py` → `latency_6a1.json`; `latency_6a1_cycle1_harness.json`)

| Measure | Load | n | p50 | p95 | max |
|---|---|---|---|---|---|
| Inference, 1 flow per call | none (offline) | 200 calls | 75.3 ms | 81.9 ms | 210.6 ms |
| Inference, 8 flows per call | none | 200 calls | 77.5 ms | 83.6 ms | 88.0 ms |
| Inference, 64 flows per call | none | 200 calls | 109.2 ms (1.7 ms/flow) | 120.0 ms | 251.7 ms |
| Model call during live capture | 50 packets/s, 2.7 flows/s | 118 calls | 79.4 ms | 441.9 ms | 583.5 ms |
| Last packet → verdict, FIN/RST flows | 50 packets/s, 2.7 flows/s, 132 s | 102 flows | 477.2 ms | 776.0 ms | 823.6 ms |
| Expiry → verdict, idle-timeout flows (after the 15 s timeout) | same run | 256 flows | 598.0 ms | 1,140.4 ms | 1,567.1 ms |
| Last packet → verdict, FIN/RST (cycle-1 harness, same code) | 261 packets/s, 2.9 flows/s, 90 s | 76 flows | 273.3 ms | 606.1 ms | 816.8 ms |
| Upload delay, event → server receipt (c775d13, `upload_delay_c775d13.json`) | 1.75 and 2.05 events/s | 208 + 180 events | 0.80 s / 0.91 s | 1.43 s / 1.55 s | 1.58 s / 1.64 s |

Both live runs miss the TC-11 budget (p95 ≤ 500 ms), which the app met on 2026-10-24 (p95 332.1 ms). Under load the
model call itself stretches from about 80 ms to about 440 ms at p95. Docker Desktop, with the Argus image and the local
Supabase stack, was running on the same PC during both runs. The tracker changes are not the cause:
replaying this capture costs 12–13 µs per packet before and after them. Lag under sustained load is carried to
Stage 6C, and TC-11 is re-run in cycle 2. Idle-ended flows wait for the 1 s expiry sweep (about 0.5 s on average)
on top of the 15 s timeout.

## Gate

Supported against the wire reference: proto, service, state, spkts, sbytes, sinpkt, rate and ct_srv_dst.
Unsupported: dur, dpkts, dbytes, sload, dload, dinpkt, ct_dst_src_ltm and ct_dst_ltm. The research release gate for
live-capture feature fidelity is therefore **unmet** and is reported as unmet, not as a success. The evidence covers
one PC, one Wi-Fi adapter and one 120 s capture of web traffic; no second PC or network tap was available.
