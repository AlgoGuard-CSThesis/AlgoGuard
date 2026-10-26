# DEF-19 part 2 — train with local benign traffic: protocol (written before any training capture)

Written 2026-10-26, after `results_def19.md` and before the training captures below were recorded.

## Why

Reporting HTTPS as `-` cut the false alarms on benign traffic to 10.0% (generator) and 26.6% (browser), still
above the 8% target. The rest falls on traffic UNSW-NB15 shows only as attacks or hardly at all (UDP, QUIC, DNS,
ICMP, plain HTTP). The model has never seen modern benign traffic, so it is given some.

## Candidate (one, no tuning)

C1 = `python train.py <file>` on the run 8 training rows (`flow_window_v2/training.csv`, 66,393 UNSW-NB15 rows)
plus every flow of two new training captures, labelled Normal, extracted with the tracker at the commit that adds
this file. Same models, hyperparameters and quality gate as run 8.

Training captures (recorded after this file is committed; outside git):

- T-generator: `capture_fidelity.py --seconds 600`
- T-browser: `capture_browsing.py --seconds 600` (generator sites, plus every other IPv4 packet of the PC)

Assumption: everything this PC sends and receives during the captures is benign.

## Gate on the selection data (decided now)

- UNSW-NB15 hold-out (`holdout_eval.csv`, 15,939 rows): F1 ≥ 0.9267 and recall ≥ 0.908, i.e. at most 0.01 below
  run 8 (F1 0.9367, recall 0.918, measured 2026-10-26 on the same rows).
- The two part-1 captures (`def19/generator.pcap`, 110 flows; `def19/browser.pcap`, 542 flows): at most 8% labelled
  Attack each.

If C1 fails the gate, it is not evaluated further, not deployed, and the result is reported.

## Final evaluation (only after the gate; captures recorded after C1 is trained)

- F-browser: `capture_browsing.py --seconds 120` (generator sites); F-unseen: `capture_browsing.py --seconds 120
  --sites unseen` (sites no training capture used). Target: at most 8% labelled Attack each.
- UNSW-NB15 untouched final set (`scripts/evaluate_model.py` as in 6A.2, overlap with every training CSV removed):
  all 6A.2 measures. This is the second candidate scored on that set; the first was run 8. Ship condition: F1 and
  recall at most 0.01 below run 8's (0.8524, 0.7589).

C1 replaces run 8 only if it meets the gate and both final conditions. Whatever the outcome, every number is
reported. The evaluation covers one PC and one network: the captures share the PC's background traffic, so the
benign results may be optimistic for other computers.
