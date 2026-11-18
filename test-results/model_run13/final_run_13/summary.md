# Model evaluation — 2026-11-18T16:19:48

- Commit: `644c4e7` · Python 3.14.6 · scikit-learn 1.9.0
- Model: `stacking.joblib` (Stacking Ensemble), trained 2026-11-17 19:02:31
- Model SHA-256: `2a91716ee3c72f0134eb19068aff2c5a7170544c2791f9ae9e171ab47ab5baf3`
- Data: `final_test.csv` — 32610 rows loaded, 0 removed as seen in training, **32610 evaluated** (17339 attack, 15271 normal)

## Results against the test plan targets (section 3.3)

| Metric | Target | Measured | Result |
|---|---|---|---|
| accuracy | >= 0.9 | 0.9293 | Met |
| f1 | >= 0.9 | 0.9332 | Met |
| recall | >= 0.9 | 0.9282 | Met |
| false_positive_rate | <= 0.08 | 0.0694 | Met |
| roc_auc | >= 0.95 | 0.9862 | Met |

Precision 0.9382 · specificity 0.9306 · prediction 0.437 ms per row

## Confusion matrix

| | Predicted Normal | Predicted Attack |
|---|---|---|
| Actual Normal | 14211 | 1060 |
| Actual Attack | 1245 | 16094 |

## By attack category (SYS-03)

| Category | Rows | Measure | Rate |
|---|---|---|---|
| Analysis | 341 | recall (flagged as Attack) | 0.6422 |
| Fuzzers | 4319 | recall (flagged as Attack) | 0.7611 |
| Normal | 15271 | specificity (labelled Normal) | 0.9306 |
| Shellcode | 278 | recall (flagged as Attack) | 0.9748 |
| Generic | 231 | recall (flagged as Attack) | 0.987 |
| Exploits | 7926 | recall (flagged as Attack) | 0.9915 |
| DoS | 2129 | recall (flagged as Attack) | 0.9953 |
| Reconnaissance | 1811 | recall (flagged as Attack) | 0.9978 |
| Backdoor | 268 | recall (flagged as Attack) | 1.0 |
| Worms | 36 | recall (flagged as Attack) | 1.0 |
