# Stage 6A.1 confirmation — results

Plan: `protocol_6a1_confirmation.md` (committed in cffb952 before the capture). Pipeline frozen at that commit;
nothing was changed after the capture was recorded.

## Capture and reference

Recorded 2026-10-26 with `capture_fidelity.py --seconds 120`: 3,750 packets to 24 site addresses (85 page loads
succeeded, 8 failed). `wire_equivalent.py` split 860 large sends, spread 137 shared timestamps and removed 178
802.1Q tags. Argus 3.0.8.2 found 102 IPv4 flows on the wire-equivalent copy; AlgoGuard found 102. All 102 matched;
no flow was left unmatched on either side.

## Results (share of 102 matched flows that agree; supported = at least 90%)

| Feature | First capture, after fixes (`fidelity_6a1_hold_wire.json`) | Fresh capture (`fidelity_6a1_confirm_wire.json`) | Supported |
|---|---|---|---|
| proto | 100.0 | 100.0 | yes |
| service (port convention) | 100.0 | 100.0 | yes |
| state | 98.2 | 100.0 | yes |
| spkts | 98.2 | 100.0 | yes |
| dpkts | 97.2 | 100.0 | yes |
| sbytes | 98.2 | 100.0 | yes |
| dbytes | 97.2 | 100.0 | yes |
| dur | 97.2 | 100.0 | yes |
| rate | 97.2 | 100.0 | yes |
| dload | 94.4 | 97.1 | yes |
| sinpkt | 94.4 | 96.1 | yes |
| dinpkt | 94.4 | 97.1 | yes |
| ct_srv_dst | 98.2 | 100.0 | yes |
| ct_dst_src_ltm | 91.7 | 93.1 | yes |
| ct_dst_ltm | 91.7 | 93.1 | yes |
| sload | 0.0 | 0.0 | no (see below) |

`sload` fails the literal tolerance on both captures because Argus's load includes the 14-byte Ethernet header of
every packet, and source packets are mostly 40-byte acknowledgements. The protocol already allows exactly that
header for byte counts. With the same correction applied to the load (Argus's bytes taken at the IP layer, which
is how UNSW-NB15 counts them), `sload` agrees on 96.3% (first capture) and 99.0% (fresh capture) of flows. This is
reported as a diagnostic, not as a pass: under the predeclared tolerances, 15 of 16 features are supported.

## Verdict

The tracker's live features now match Argus within the predeclared tolerances for 15 of the 16 model inputs on a
capture none of the changes had seen, and the 16th matches once the link-layer bytes the protocol already
discounts for byte counts are discounted for the load as well. The remaining limits: one PC, one Wi-Fi adapter,
web traffic from one generator, and a reference that is Argus on a wire-equivalent copy, not a network tap.
