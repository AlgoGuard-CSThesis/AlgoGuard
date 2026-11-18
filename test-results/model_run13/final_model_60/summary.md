# Model evaluation — 2026-11-18T16:20:27

- Commit: `644c4e7` · Python 3.14.6 · scikit-learn 1.9.0
- Model: `stacking.joblib` (Stacking Ensemble), trained 2026-10-26 15:04:30
- Model SHA-256: `6756c632efe99edcf50c27ed5daf87dcfd18573fdbb59492d5aad4c225e69604`
- Data: `final_test.csv` — 32610 rows loaded, 0 removed as seen in training, **32610 evaluated** (17339 attack, 15271 normal)

## Results against the test plan targets (section 3.3)

| Metric | Target | Measured | Result |
|---|---|---|---|
| accuracy | >= 0.9 | 0.8603 | Not met |
| f1 | >= 0.9 | 0.8527 | Not met |
| recall | >= 0.9 | 0.7604 | Not met |
| false_positive_rate | <= 0.08 | 0.0262 | Met |
| roc_auc | >= 0.95 | 0.9682 | Met |

Precision 0.9706 · specificity 0.9738 · prediction 0.318 ms per row

## Confusion matrix

| | Predicted Normal | Predicted Attack |
|---|---|---|
| Actual Normal | 14871 | 400 |
| Actual Attack | 4154 | 13185 |

## By attack category (SYS-03)

| Category | Rows | Measure | Rate |
|---|---|---|---|
| Fuzzers | 4319 | recall (flagged as Attack) | 0.2299 |
| Analysis | 341 | recall (flagged as Attack) | 0.5425 |
| Shellcode | 278 | recall (flagged as Attack) | 0.795 |
| Generic | 231 | recall (flagged as Attack) | 0.9048 |
| Worms | 36 | recall (flagged as Attack) | 0.9167 |
| Reconnaissance | 1811 | recall (flagged as Attack) | 0.942 |
| Exploits | 7926 | recall (flagged as Attack) | 0.9516 |
| DoS | 2129 | recall (flagged as Attack) | 0.9554 |
| Normal | 15271 | specificity (labelled Normal) | 0.9738 |
| Backdoor | 268 | recall (flagged as Attack) | 0.9776 |
