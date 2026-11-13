# Model evaluation — 2026-11-11T01:22:01

- Commit: `b1e647c` · Python 3.14.6 · scikit-learn 1.9.0
- Model: `stacking.joblib` (Stacking Ensemble), trained 2026-10-26 15:04:30
- Model SHA-256: `6756c632efe99edcf50c27ed5daf87dcfd18573fdbb59492d5aad4c225e69604`
- Data: `UNSW_NB15_training-set.csv` — 175341 rows loaded, 66641 removed as seen in training, **108700 evaluated** (57798 attack, 50902 normal)

## Results against the test plan targets (section 3.3)

| Metric | Target | Measured | Result |
|---|---|---|---|
| accuracy | >= 0.9 | 0.8615 | Not met |
| f1 | >= 0.9 | 0.8537 | Not met |
| recall | >= 0.9 | 0.76 | Not met |
| false_positive_rate | <= 0.08 | 0.0232 | Met |
| roc_auc | >= 0.95 | 0.9695 | Met |

Precision 0.9738 · specificity 0.9768 · prediction 0.161 ms per row

## Confusion matrix

| | Predicted Normal | Predicted Attack |
|---|---|---|
| Actual Normal | 49720 | 1182 |
| Actual Attack | 13869 | 43929 |

## By attack category (SYS-03)

| Category | Rows | Measure | Rate |
|---|---|---|---|
| Fuzzers | 14398 | recall (flagged as Attack) | 0.2282 |
| Analysis | 1136 | recall (flagged as Attack) | 0.5449 |
| Shellcode | 923 | recall (flagged as Attack) | 0.792 |
| Generic | 771 | recall (flagged as Attack) | 0.8975 |
| Worms | 123 | recall (flagged as Attack) | 0.935 |
| Reconnaissance | 6035 | recall (flagged as Attack) | 0.9442 |
| Exploits | 26427 | recall (flagged as Attack) | 0.9508 |
| DoS | 7093 | recall (flagged as Attack) | 0.9576 |
| Backdoor | 892 | recall (flagged as Attack) | 0.9765 |
| Normal | 50902 | specificity (labelled Normal) | 0.9768 |
