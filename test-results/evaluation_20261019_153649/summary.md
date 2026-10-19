# Model evaluation — 2026-10-19T15:37:03

- Commit: `dafe5e5` · Python 3.14.6 · scikit-learn 1.9.0
- Model: `stacking.joblib` (Stacking Ensemble), trained 2026-10-08 05:38:46
- Model SHA-256: `9c9f0fb18524eeda528180355d4d2f7318ea4b351530394d9460474d7415847c`
- Data: `UNSW_NB15_training-set.csv` — 82332 rows loaded, 29089 removed as seen in training, **53243 evaluated** (20674 attack, 32569 normal)

## Results against the test plan targets (section 3.3)

| Metric | Target | Measured | Result |
|---|---|---|---|
| accuracy | >= 0.9 | 0.8147 | Not met |
| f1 | >= 0.9 | 0.7949 | Not met |
| recall | >= 0.9 | 0.9244 | Met |
| false_positive_rate | <= 0.08 | 0.2549 | Not met |
| roc_auc | >= 0.95 | 0.9371 | Not met |

Precision 0.6972 · specificity 0.7451 · prediction 0.084 ms per row

## Confusion matrix

| | Predicted Normal | Predicted Attack |
|---|---|---|
| Actual Normal | 24268 | 8301 |
| Actual Attack | 1563 | 19111 |

## By attack category (SYS-03)

| Category | Rows | Measure | Rate |
|---|---|---|---|
| Normal | 32569 | specificity (labelled Normal) | 0.7451 |
| Fuzzers | 5064 | recall (flagged as Attack) | 0.7662 |
| Shellcode | 359 | recall (flagged as Attack) | 0.8997 |
| Generic | 802 | recall (flagged as Attack) | 0.9127 |
| Worms | 43 | recall (flagged as Attack) | 0.9535 |
| DoS | 2214 | recall (flagged as Attack) | 0.9693 |
| Backdoor | 405 | recall (flagged as Attack) | 0.9778 |
| Exploits | 9328 | recall (flagged as Attack) | 0.98 |
| Reconnaissance | 2043 | recall (flagged as Attack) | 0.9971 |
| Analysis | 416 | recall (flagged as Attack) | 0.9976 |

## Model comparison on the same rows (SYS-02)

| Model | Accuracy | Recall | F1 | FPR | ROC-AUC | ms/row |
|---|---|---|---|---|---|---|
| Random Forest | 0.8147 | 0.9297 | 0.7957 | 0.2583 | 0.9306 | 0.0096 |
| Stacking Ensemble | 0.8147 | 0.9244 | 0.7949 | 0.2549 | 0.9371 | 0.0836 |
| Gradient Boosting | 0.7815 | 0.9777 | 0.7765 | 0.343 | 0.9292 | 0.0037 |
| K-Nearest Neighbors | 0.7768 | 0.9246 | 0.7629 | 0.317 | 0.8962 | 0.0086 |
| AdaBoost | 0.7387 | 0.9797 | 0.7444 | 0.4142 | 0.8907 | 0.0315 |
| Naive Bayes | 0.6837 | 0.1855 | 0.313 | 0.0001 | 0.6108 | 0.0036 |
