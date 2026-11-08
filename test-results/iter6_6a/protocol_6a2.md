# Stage 6A.2 — evaluation protocol (written before any result was seen)

Written 2026-10-25 about 21:50 (Asia/Manila; first committed 21:53 as a42ff4b), before the final evaluation set
was scored by any model. (The first commit gave 22:15 by mistake; only this line was corrected.)

## Data roles

| Role | Data | Rows |
|---|---|---|
| Training | `research/dataset/processed/flow_window_v2/training.csv` — official UNSW-NB15 testing partition minus the hold-out | 66,393 |
| Calibration and selection | `research/dataset/processed/flow_window_v2/holdout_eval.csv` — the cycle 1 hold-out, already used to choose feature schema v2 | 15,939 |
| Final evaluation (untouched) | Official UNSW-NB15 **training** partition, downloaded 2026-10-25 from the UNSW share (SHA-256 `bec7dd5e…48fa`), minus every row whose 15 per-flow features match a row of the training file or the hold-out | 175,341 before overlap removal |

Finding recorded before scoring: `research/dataset/raw/UNSW_NB15_training-set.csv` in this repository is byte-identical
(SHA-256 `734fe664…a559`) to the official `UNSW_NB15_testing-set.csv` (82,332 rows). Every model so far was trained
on the official testing partition; the official training partition has never been used.

## Model under test

Deployment #2: training run 8 Stacking Ensemble, feature schema v2, SHA-256 `1fd819c1…9f29`.

## Measures

Accuracy, precision, recall, F1, false-positive rate, ROC-AUC, Brier score, expected calibration error (ECE, 10
equal-width bins), reliability curve (10 bins), recall per attack category, and the share of rows in each attack-score
band (Low < 0.50, Medium ≥ 0.50, High ≥ 0.70, Very high ≥ 0.90). Every figure states its sample size. The Master Test
Plan 3.3 targets are reported for reference and are not changed.

## Decision rules (fixed now)

1. The final evaluation set is scored once per candidate artifact. No feature, threshold, hyperparameter or training
   choice is made after seeing its results; any later change needs a new untouched set.
2. Calibration is assessed on the calibration set. A calibrator is fitted only if ECE there exceeds 0.05. The method
   (sigmoid or isotonic) is chosen by 5-fold cross-validated Brier score **on the calibration set only**. The calibrator
   is adopted only if it lowers that cross-validated Brier score; then it becomes a new gated artifact that must pass
   the deployment quality gate and model contract before publication.
3. The final set is then scored for the uncalibrated model and, if one was adopted, the calibrated one. Both results
   are reported; the adoption decision of rule 2 is not revisited.
4. Results below the 3.3 targets are reported as they are; the release gate stays unmet rather than re-targeted.
