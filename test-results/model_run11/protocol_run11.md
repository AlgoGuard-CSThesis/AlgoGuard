# Training run 11 — protocol (written before any run 11 data was built or model trained)

Written 2026-11-10 18:20 (Asia/Manila) and committed before `build_run11_data.py` was run. Same rules as
Stage 6A.2 (`test-results/iter6_6a/protocol_6a2.md`): every choice below is fixed now, the final test rows
are scored once per model, and results that miss a target are reported as they are.

## Why

Model 60 (training run 10) on the untouched UNSW-NB15 set (108,739 rows, `iter6_6a/def19_c1_final/`):
accuracy 0.8616, F1 0.8538, recall 0.7602 against the 90% targets. Of its 13,869 missed attacks, 11,113
(80.1%) are Fuzzers, caught 22.8% of the time there and 79.1% on the hold-out. Every model so far was trained
only on the official UNSW-NB15 **testing** partition (82,332 rows, 6,062 Fuzzers); the official **training**
partition (175,341 rows, 18,184 Fuzzers) has never been used for training. A threshold change cannot recover
these attacks: for run 8 on the same set, about 9,500 of its 13,942 missed attacks scored below 0.10
(`iter6_6a/final_eval_supplement.json`, reliability bin 0.0–0.1).

## Key used to decide "the same flow"

The 13 per-flow features of feature schema v2 (`FLOW_FEATURE_COLUMNS` without the three connection counts).
Run 10 and run 11 do not use `sttl` or `dttl`, so two rows that differ only there are the same input apart
from the connection counts. This is stricter than the 15-feature key used in 6A.2.

## Data roles

| Role | Data |
|---|---|
| Pool P | Rows of the official training partition (`research/dataset/processed/official_unsw/UNSW_NB15_training-set.csv`, SHA-256 `bec7dd5e…48fa`) whose 13-feature key matches no row of the 82,332-row file: 108,699 rows in 87,661 key groups (counted 2026-11-10, before any split). |
| Split of P | `StratifiedGroupKFold(n_splits=10, shuffle=True, random_state=42)`, groups = 13-feature key, strata = `attack_cat`. Folds 0, 1 and 2 are the **final test** (about 30%); folds 3–9 are added to training. No key group is on both sides. |
| Training | `research/dataset/processed/def19/training_local.csv` (run 10's 68,853 rows: 66,393 UNSW-NB15 rows plus 2,460 local benign flows) plus the training part of P, 16 features and `label`. |
| Selection (gate) | The hold-out `flow_window_v2/holdout_eval.csv` (15,939 rows) and four benign captures not used for training: `def19/generator.pcap`, `def19/browser.pcap`, `def19/final_browser.pcap`, `def19/final_unseen.pcap`. |
| Final test | The test part of P. Not scored by any model until the gate has passed. |

Files go to `research/dataset/processed/run11/` (git-ignored); counts and SHA-256 sums go to
`test-results/model_run11/run11_data_manifest.json`.

## Candidate

One: `python train.py research\dataset\processed\run11\training.csv`, unchanged code, models,
hyperparameters and quality gate. No tuning and no second candidate.

## Pre-check (recorded, not used for any choice)

5-fold `StratifiedGroupKFold` Random Forest on the run 11 training rows only, recall per attack category. It
only shows early whether Fuzzers become separable; it does not touch the final test.

## Gate (selection data)

1. Hold-out: run 11's F1 and recall at most 0.01 below run 10's on the same rows (`scripts/evaluate_model.py`
   with the run 11 training file as `--train-csv`, run 10 scored on the same kept rows).
2. Each of the four benign captures: at most 8% of flows labelled Attack (`iter6_6a/benign_attack_share.py`).
3. The artifact is at most 50 MiB (hosted storage limit) and passes the deployment quality gate.

If run 11 fails the gate it is not evaluated further, it is not deployed, and model 60 stays.

## Final evaluation (once, only after the gate)

1. Final test: run 11 and run 10 on the same rows: accuracy, precision, recall, F1, false-positive rate,
   ROC-AUC, recall per attack category, Brier score and expected calibration error (10 bins).
2. Two benign captures recorded after run 11 was trained (`iter6_6a/capture_browsing.py --seconds 120`, once
   with the generator sites and once with `--sites unseen`): at most 8% labelled Attack each, both models.

## Ship rule

Run 11 replaces run 10 only if it passed the gate, both new benign captures are at most 8% Attack, and on the
final test its F1 and recall are at least run 10's. Whether the 90% targets are met is reported either way and
does not decide shipping.

## Limits stated in advance

- The final test comes from the same file as part of run 11's training data (a same-dataset hold-out); for
  run 10 the same rows are a cross-file test. Run 11's result is not comparable with run 10's 6A.2 figure, and
  no untouched UNSW-NB15 data remains after this run.
- Benign live-traffic evidence is still one PC on one network.
