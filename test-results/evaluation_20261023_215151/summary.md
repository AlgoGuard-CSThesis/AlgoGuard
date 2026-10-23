# Model evaluation — 2026-10-23T21:52:07

- Commit: `47b3c97` · Python 3.14.6 · scikit-learn 1.9.0
- Model: `stacking.joblib` (Stacking Ensemble), trained 2026-10-23 04:56:12
- Model SHA-256: `9ec46bd16c1f970023a35b3feef419b23afaeb24dfc10873e3df60b80120d3e5`
- Data: `holdout_eval.csv` — 15939 rows loaded, 0 removed as seen in training, **15939 evaluated** (6174 attack, 9765 normal)

## Results against the test plan targets (section 3.3)

| Metric | Target | Measured | Result |
|---|---|---|---|
| accuracy | >= 0.9 | 0.9079 | Met |
| f1 | >= 0.9 | 0.8736 | Not met |
| recall | >= 0.9 | 0.8218 | Not met |
| false_positive_rate | <= 0.08 | 0.0377 | Met |
| roc_auc | >= 0.95 | 0.9713 | Met |

Precision 0.9324 · specificity 0.9623 · prediction 0.525 ms per row

## Confusion matrix

| | Predicted Normal | Predicted Attack |
|---|---|---|
| Actual Normal | 9397 | 368 |
| Actual Attack | 1100 | 5074 |

## By attack category (SYS-03)

| Category | Rows | Measure | Rate |
|---|---|---|---|
| Fuzzers | 1520 | recall (flagged as Attack) | 0.4947 |
| Shellcode | 113 | recall (flagged as Attack) | 0.5929 |
| Worms | 14 | recall (flagged as Attack) | 0.8571 |
| Generic | 252 | recall (flagged as Attack) | 0.9008 |
| DoS | 647 | recall (flagged as Attack) | 0.9165 |
| Exploits | 2800 | recall (flagged as Attack) | 0.9318 |
| Normal | 9765 | specificity (labelled Normal) | 0.9623 |
| Backdoor | 130 | recall (flagged as Attack) | 0.9769 |
| Reconnaissance | 567 | recall (flagged as Attack) | 0.9824 |
| Analysis | 131 | recall (flagged as Attack) | 0.9924 |
