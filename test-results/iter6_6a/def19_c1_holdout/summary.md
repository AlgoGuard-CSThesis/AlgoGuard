# Model evaluation — 2026-10-26T23:05:19

- Commit: `34f29ee` · Python 3.14.6 · scikit-learn 1.9.0
- Model: `stacking.joblib` (Stacking Ensemble), trained 2026-10-26 15:04:30
- Model SHA-256: `6756c632efe99edcf50c27ed5daf87dcfd18573fdbb59492d5aad4c225e69604`
- Data: `holdout_eval.csv` — 15939 rows loaded, 0 removed as seen in training, **15939 evaluated** (6174 attack, 9765 normal)

## Results against the test plan targets (section 3.3)

| Metric | Target | Measured | Result |
|---|---|---|---|
| accuracy | >= 0.9 | 0.9511 | Met |
| f1 | >= 0.9 | 0.9355 | Met |
| recall | >= 0.9 | 0.9159 | Met |
| false_positive_rate | <= 0.08 | 0.0267 | Met |
| roc_auc | >= 0.95 | 0.9889 | Met |

Precision 0.9559 · specificity 0.9733 · prediction 0.819 ms per row

## Confusion matrix

| | Predicted Normal | Predicted Attack |
|---|---|---|
| Actual Normal | 9504 | 261 |
| Actual Attack | 519 | 5655 |

## By attack category (SYS-03)

| Category | Rows | Measure | Rate |
|---|---|---|---|
| Fuzzers | 1520 | recall (flagged as Attack) | 0.7908 |
| Shellcode | 113 | recall (flagged as Attack) | 0.8584 |
| Generic | 252 | recall (flagged as Attack) | 0.9167 |
| Worms | 14 | recall (flagged as Attack) | 0.9286 |
| DoS | 647 | recall (flagged as Attack) | 0.9567 |
| Exploits | 2800 | recall (flagged as Attack) | 0.9589 |
| Reconnaissance | 567 | recall (flagged as Attack) | 0.9665 |
| Normal | 9765 | specificity (labelled Normal) | 0.9733 |
| Backdoor | 130 | recall (flagged as Attack) | 0.9923 |
| Analysis | 131 | recall (flagged as Attack) | 1.0 |
