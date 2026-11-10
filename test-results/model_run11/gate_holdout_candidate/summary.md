# Model evaluation — 2026-11-11T01:18:37

- Commit: `f768dd0` · Python 3.14.6 · scikit-learn 1.9.0
- Model: `stacking.joblib` (Stacking Ensemble), trained 2026-11-10 17:16:28
- Model SHA-256: `2afa2e8bb642cd0e4543e58f1414c0781ec6766e9aced1027c2fa25d97f64845`
- Data: `holdout_eval.csv` — 15939 rows loaded, 3 removed as seen in training, **15936 evaluated** (6173 attack, 9763 normal)

## Results against the test plan targets (section 3.3)

| Metric | Target | Measured | Result |
|---|---|---|---|
| accuracy | >= 0.9 | 0.9267 | Met |
| f1 | >= 0.9 | 0.9103 | Met |
| recall | >= 0.9 | 0.9601 | Met |
| false_positive_rate | <= 0.08 | 0.0944 | Not met |
| roc_auc | >= 0.95 | 0.984 | Met |

Precision 0.8654 · specificity 0.9056 · prediction 0.355 ms per row

## Confusion matrix

| | Predicted Normal | Predicted Attack |
|---|---|---|
| Actual Normal | 8841 | 922 |
| Actual Attack | 246 | 5927 |

## By attack category (SYS-03)

| Category | Rows | Measure | Rate |
|---|---|---|---|
| Fuzzers | 1520 | recall (flagged as Attack) | 0.8757 |
| Normal | 9763 | specificity (labelled Normal) | 0.9056 |
| Worms | 14 | recall (flagged as Attack) | 0.9286 |
| Shellcode | 112 | recall (flagged as Attack) | 0.9732 |
| Exploits | 2800 | recall (flagged as Attack) | 0.9839 |
| DoS | 647 | recall (flagged as Attack) | 0.9923 |
| Analysis | 131 | recall (flagged as Attack) | 0.9924 |
| Generic | 252 | recall (flagged as Attack) | 0.996 |
| Reconnaissance | 567 | recall (flagged as Attack) | 0.9982 |
| Backdoor | 130 | recall (flagged as Attack) | 1.0 |
