# Model evaluation — 2026-10-23T10:48:53

- Commit: `47b3c97` · Python 3.14.6 · scikit-learn 1.9.0
- Model: `stacking.joblib` (Stacking Ensemble), trained 2026-10-23 02:47:33
- Model SHA-256: `05a0c9e62ce18f2bbf8efbfe48cd83c61f699582f78aed3f6ee97a7a42590ca4`
- Data: `holdout_eval.csv` — 15939 rows loaded, 0 removed as seen in training, **15939 evaluated** (6174 attack, 9765 normal)

## Results against the test plan targets (section 3.3)

| Metric | Target | Measured | Result |
|---|---|---|---|
| accuracy | >= 0.9 | 0.9556 | Met |
| f1 | >= 0.9 | 0.9415 | Met |
| recall | >= 0.9 | 0.9236 | Met |
| false_positive_rate | <= 0.08 | 0.0242 | Met |
| roc_auc | >= 0.95 | 0.9908 | Met |

Precision 0.9603 · specificity 0.9758 · prediction 0.918 ms per row

## Confusion matrix

| | Predicted Normal | Predicted Attack |
|---|---|---|
| Actual Normal | 9529 | 236 |
| Actual Attack | 472 | 5702 |

## By attack category (SYS-03)

| Category | Rows | Measure | Rate |
|---|---|---|---|
| Fuzzers | 1520 | recall (flagged as Attack) | 0.7967 |
| Shellcode | 113 | recall (flagged as Attack) | 0.8938 |
| Worms | 14 | recall (flagged as Attack) | 0.9286 |
| Generic | 252 | recall (flagged as Attack) | 0.9484 |
| DoS | 647 | recall (flagged as Attack) | 0.9614 |
| Exploits | 2800 | recall (flagged as Attack) | 0.9661 |
| Reconnaissance | 567 | recall (flagged as Attack) | 0.9718 |
| Normal | 9765 | specificity (labelled Normal) | 0.9758 |
| Backdoor | 130 | recall (flagged as Attack) | 0.9923 |
| Analysis | 131 | recall (flagged as Attack) | 1.0 |

## Model comparison on the same rows (SYS-02)

| Model | Accuracy | Recall | F1 | FPR | ROC-AUC | ms/row |
|---|---|---|---|---|---|---|
| Random Forest | 0.9558 | 0.9213 | 0.9417 | 0.0223 | 0.99 | 0.0264 |
| Stacking Ensemble | 0.9556 | 0.9236 | 0.9415 | 0.0242 | 0.9908 | 0.9181 |
| K-Nearest Neighbors | 0.9376 | 0.9033 | 0.9181 | 0.0408 | 0.9728 | 0.6315 |
| Gradient Boosting | 0.9318 | 0.9174 | 0.9124 | 0.0591 | 0.9825 | 0.0089 |
| AdaBoost | 0.8997 | 0.8515 | 0.868 | 0.0698 | 0.9647 | 0.0868 |
| Naive Bayes | 0.6829 | 0.1817 | 0.3075 | 0.0002 | 0.6029 | 0.0098 |
