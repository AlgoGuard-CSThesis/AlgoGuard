# DEF-19 — results of removing the HTTPS shortcut

Plan: `protocol_def19.md` (committed with the change in 6cb88e6, before both captures were recorded). Model:
deployment #2, training run 8, unchanged. Scores: `def19_benign_scores.json` (`benign_attack_share.py`, tracker at
5bd4e07 for "before", at 6cb88e6 for "after").

## Benign captures (2026-10-26, outside git)

| Capture | Flows | Labelled Attack before (`ssl`) | After (HTTPS as `-`) | Target ≤ 8% |
|---|---|---|---|---|
| Generator: Python page loads, DNS, pings, 120 s | 110 | 76 (69.1%) | 11 (10.0%) | not met |
| Browser: headless Chrome page loads plus every other IPv4 packet of the PC, 120 s | 542 | 523 (96.5%) | 144 (26.6%) | not met |

What is still labelled Attack after the change:

- Generator: 3 of 102 TCP flows, all 4 pings (ICMP) and all 4 DNS exchanges.
- Browser: 74 of 403 TCP flows, 70 of 139 UDP flows (mostly Chrome's QUIC on UDP 443 and other background UDP),
  12 of 20 plain-HTTP flows.

Attack-score bands after the change: generator 99 Low, 3 Medium, 2 High, 6 Very high; browser 398 Low, 42 Medium, 58
High, 44 Very high.

## Cost on known attacks

`def19_ssl_rows.json`: the 56 `ssl` rows of the official training partition, all attacks (Exploits 39, Generic 13,
DoS 4). With their service as `ssl` the model flags 56 (recall 100%); with `-` it still flags 54 (96.4%, median
score 0.974): their other features carry the attack, not the service name.

## Decision

The change ships: it removes the dominant cause of the false alarms without touching the model. The 8% target is
still not met. The remaining alarms fall on traffic UNSW-NB15 shows only as attacks or hardly at all: UDP (76.7%
of the dataset's UDP rows are attacks), DNS (89.8%), ICMP and plain HTTP. Removing a shortcut cannot teach the
model what modern benign traffic looks like, so DEF-19 stays open.
