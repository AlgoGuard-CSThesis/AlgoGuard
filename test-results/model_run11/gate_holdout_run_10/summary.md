# Model evaluation — 2026-11-11T01:18:23

- Commit: `f768dd0` · Python 3.14.6 · scikit-learn 1.9.0
- Model: `stacking.joblib` (Stacking Ensemble), trained 2026-10-26 15:04:30
- Model SHA-256: `6756c632efe99edcf50c27ed5daf87dcfd18573fdbb59492d5aad4c225e69604`
- Data: `holdout_eval.csv` — 15939 rows loaded, 3 removed as seen in training, **15936 evaluated** (6173 attack, 9763 normal)

## Results against the test plan targets (section 3.3)

| Metric | Target | Measured | Result |
|---|---|---|---|
| accuracy | >= 0.9 | 0.9511 | Met |
| f1 | >= 0.9 | 0.9355 | Met |
| recall | >= 0.9 | 0.9159 | Met |
| false_positive_rate | <= 0.08 | 0.0267 | Met |
| roc_auc | >= 0.95 | 0.9889 | Met |

Precision 0.9559 · specificity 0.9733 · prediction 0.253 ms per row

## Confusion matrix

| | Predicted Normal | Predicted Attack |
|---|---|---|
| Actual Normal | 9502 | 261 |
| Actual Attack | 519 | 5654 |

## By attack category (SYS-03)

| Category | Rows | Measure | Rate |
|---|---|---|---|
| Fuzzers | 1520 | recall (flagged as Attack) | 0.7908 |
| Shellcode | 112 | recall (flagged as Attack) | 0.8571 |
| Generic | 252 | recall (flagged as Attack) | 0.9167 |
| Worms | 14 | recall (flagged as Attack) | 0.9286 |
| DoS | 647 | recall (flagged as Attack) | 0.9567 |
| Exploits | 2800 | recall (flagged as Attack) | 0.9589 |
| Reconnaissance | 567 | recall (flagged as Attack) | 0.9665 |
| Normal | 9763 | specificity (labelled Normal) | 0.9733 |
| Backdoor | 130 | recall (flagged as Attack) | 0.9923 |
| Analysis | 131 | recall (flagged as Attack) | 1.0 |
