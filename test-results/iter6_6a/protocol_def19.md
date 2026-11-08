# DEF-19 — remove the HTTPS shortcut: evaluation protocol (written before the evaluation captures)

Written 2026-10-26, before either evaluation capture below was recorded.

## Finding that motivates the change

In UNSW-NB15 the service value `ssl` occurs only in attack rows: 20 of 20 in the training file, 56 of 56 in the
official training partition (Exploits 39, Generic 13, DoS 4). The tracker reported every port-443 connection as
`ssl`, so the deployed model reads ordinary HTTPS as attack traffic. Exploration on the first 6A.1 capture (already
seen, not used below): with `ssl` the model labels 72.2% of its 115 benign flows Attack; with the HTTPS flows
reported as `-` it labels 10.4%.

## Change under test

`services/flow_tracker_service.py` stops mapping port 443 to `ssl`; HTTPS reports `-`, the dataset's value for
traffic whose service was not identified. The deployed model (deployment #2, training run 8) is unchanged: no
retraining, no new publication. This removes a learned shortcut; it does not teach the model what HTTPS looks like.

## Evaluation data (recorded after this file is committed)

1. **Generator capture:** `capture_fidelity.py --seconds 120` (Python page loads, DNS, pings).
2. **Browser capture:** `capture_browsing.py --seconds 120`: headless Chrome loads the same public home pages in a
   fresh temporary profile while every IPv4 packet on the Wi-Fi adapter is recorded, so the PC's other background
   traffic is included. All of it is treated as benign: the PC is assumed not to be under attack.

Captures stay outside git; only per-flow scores and counts are kept.

## Measures and target

For each capture: the number of flows and the share labelled Attack (attack probability ≥ 0.50), the attack-score
bands, and the Attack share by protocol and service, all with the tracker before and after the change. Target: at
most 8% labelled Attack, the Master Test Plan 3.3 false-positive target for unseen data. The result is reported
whether or not it is met.

Cost of the change on known attacks: the 56 `ssl` rows of the official training partition (all attacks, already
used once in 6A.2) are scored with their service as `ssl` and as `-`, and both recalls are reported.

DEF-19 is re-tested formally in test cycle 2; this run decides whether the change ships.
