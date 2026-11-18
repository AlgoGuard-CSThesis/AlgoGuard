# Model evaluation — 2026-11-18T03:03:47

- Commit: `644c4e7` · Python 3.14.6 · scikit-learn 1.9.0
- Model: `stacking.joblib` (Stacking Ensemble), trained 2026-11-17 19:02:31
- Model SHA-256: `2a91716ee3c72f0134eb19068aff2c5a7170544c2791f9ae9e171ab47ab5baf3`
- Data: `holdout_eval.csv` — 15939 rows loaded, 3 removed as seen in training, **15936 evaluated** (6173 attack, 9763 normal)

## Results against the test plan targets (section 3.3)

| Metric | Target | Measured | Result |
|---|---|---|---|
| accuracy | >= 0.9 | 0.9006 | Met |
| f1 | >= 0.9 | 0.8845 | Not met |
| recall | >= 0.9 | 0.9823 | Met |
| false_positive_rate | <= 0.08 | 0.1511 | Not met |
| roc_auc | >= 0.95 | 0.9878 | Met |

Precision 0.8044 · specificity 0.8489 · prediction 0.561 ms per row

## Confusion matrix

| | Predicted Normal | Predicted Attack |
|---|---|---|
| Actual Normal | 8288 | 1475 |
| Actual Attack | 109 | 6064 |

## By attack category (SYS-03)

| Category | Rows | Measure | Rate |
|---|---|---|---|
| Normal | 9763 | specificity (labelled Normal) | 0.8489 |
| Fuzzers | 1520 | recall (flagged as Attack) | 0.9487 |
| Shellcode | 112 | recall (flagged as Attack) | 0.9821 |
| Exploits | 2800 | recall (flagged as Attack) | 0.9907 |
| Generic | 252 | recall (flagged as Attack) | 0.9921 |
| DoS | 647 | recall (flagged as Attack) | 0.9985 |
| Analysis | 131 | recall (flagged as Attack) | 1.0 |
| Backdoor | 130 | recall (flagged as Attack) | 1.0 |
| Reconnaissance | 567 | recall (flagged as Attack) | 1.0 |
| Worms | 14 | recall (flagged as Attack) | 1.0 |
