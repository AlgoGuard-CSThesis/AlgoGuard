# DEF-19 part 2 — training with local benign traffic: results

Plan: `protocol_def19_part2.md` (committed in 34f29ee before any training capture). One candidate, C1 = training run
10; no other candidate was trained.

## Training data (`def19_training_data.json`)

The 66,393 run 8 training rows plus 2,460 benign flows from two 10-minute captures on PC A, labelled Normal:
410 from the Python page generator (15,941 packets) and 2,050 from headless Chrome with the PC's background
traffic (85,921 packets; 1,562 TCP, 488 UDP). Attack share of the combined 68,853 rows: 56.9% (run 8: 59.0%).

## Gate on the selection data — passed

| Check | Run 8 | C1 (run 10) | Gate |
|---|---|---|---|
| Hold-out F1 (15,939 rows, `def19_c1_holdout/`) | 0.9367 | 0.9355 | ≥ 0.9267 ✓ |
| Hold-out recall | 0.918 | 0.9159 | ≥ 0.908 ✓ |
| Part-1 generator capture labelled Attack (110 flows) | 10.0% | 0.0% | ≤ 8% ✓ |
| Part-1 browser capture labelled Attack (542 flows) | 26.6% | 3.3% | ≤ 8% ✓ |

## Final evaluation — passed

Benign captures recorded after C1 was trained (`def19_c1_final_captures.json`; run 8 with HTTPS reported as `-`):

| Capture (120 s, headless Chrome plus background traffic) | Flows | Run 8 | C1 | Target ≤ 8% |
|---|---|---|---|---|
| Same sites as training | 502 | 147 (29.3%) | 16 (3.2%) | met |
| Sites no training capture used | 687 | 319 (46.4%) | 45 (6.6%) | met |

UNSW-NB15 untouched final set, 108,739 rows (`def19_c1_final/`; the second candidate scored on it, after run 8):

| Measure | Run 8 (6A.2) | C1 | Ship condition |
|---|---|---|---|
| Accuracy | 0.8602 | 0.8616 | — |
| F1 | 0.8524 | 0.8538 | ≥ 0.8424 ✓ |
| Recall | 0.7589 | 0.7602 | ≥ 0.7489 ✓ |
| False-positive rate | 0.0248 | 0.0232 | — |
| ROC-AUC | 0.9685 | 0.9695 | — |
| Fuzzers recall | 0.2192 | 0.2282 | — |

## Decision

C1 met the gate and both final conditions, so it replaces run 8: deployed locally on 2026-10-26 with
`python train.py --deploy-run 10` (deployment 5, local model 60; it passed the quality gate). The cloud pilot keeps
run 8 until a maintainer publishes model 60.

Benign live traffic on this PC is now labelled Attack 3–7% of the time, within the 8% target, and the model does
as well as run 8 on unseen dataset rows. Limits: the benign evidence comes from one PC and one network, and its
background traffic appears in both training and evaluation captures. Other computers may see more false alarms,
and attacks on live traffic remain untested. DEF-19 is re-tested formally in test cycle 2.
