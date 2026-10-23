# Model evaluation — 2026-10-23T14:26:25

- Commit: `67032f5` · Python 3.14.6 · scikit-learn 1.9.0
- Model: `stacking.joblib` (Stacking Ensemble), trained 2026-10-23 03:19:00
- Model SHA-256: `1fd819c1c08fbcdecbe8db3da590ef60abd770bceb1b811435d8bae570cf9f29`
- Data: `holdout_eval.csv` — 15939 rows loaded, 0 removed as seen in training, **15939 evaluated** (6174 attack, 9765 normal)

## Results against the test plan targets (section 3.3)

| Metric | Target | Measured | Result |
|---|---|---|---|
| accuracy | >= 0.9 | 0.9519 | Met |
| f1 | >= 0.9 | 0.9367 | Met |
| recall | >= 0.9 | 0.918 | Met |
| false_positive_rate | <= 0.08 | 0.0266 | Met |
| roc_auc | >= 0.95 | 0.989 | Met |

Precision 0.9561 · specificity 0.9734 · prediction 0.979 ms per row

## Confusion matrix

| | Predicted Normal | Predicted Attack |
|---|---|---|
| Actual Normal | 9505 | 260 |
| Actual Attack | 506 | 5668 |

## By attack category (SYS-03)

| Category | Rows | Measure | Rate |
|---|---|---|---|
| Fuzzers | 1520 | recall (flagged as Attack) | 0.7895 |
| Shellcode | 113 | recall (flagged as Attack) | 0.885 |
| Generic | 252 | recall (flagged as Attack) | 0.9246 |
| Worms | 14 | recall (flagged as Attack) | 0.9286 |
| DoS | 647 | recall (flagged as Attack) | 0.9536 |
| Exploits | 2800 | recall (flagged as Attack) | 0.9618 |
| Normal | 9765 | specificity (labelled Normal) | 0.9734 |
| Reconnaissance | 567 | recall (flagged as Attack) | 0.9735 |
| Backdoor | 130 | recall (flagged as Attack) | 0.9923 |
| Analysis | 131 | recall (flagged as Attack) | 1.0 |

## Model comparison on the same rows (SYS-02)

| Model | Accuracy | Recall | F1 | FPR | ROC-AUC | ms/row |
|---|---|---|---|---|---|---|
| Random Forest | 0.9534 | 0.9185 | 0.9386 | 0.0245 | 0.9887 | 0.0321 |
| Stacking Ensemble | 0.9519 | 0.918 | 0.9367 | 0.0266 | 0.989 | 0.9786 |
| K-Nearest Neighbors | 0.9254 | 0.8874 | 0.9021 | 0.0506 | 0.9648 | 0.6364 |
| Gradient Boosting | 0.9097 | 0.8905 | 0.8843 | 0.0781 | 0.97 | 0.0124 |
| AdaBoost | 0.8226 | 0.7272 | 0.7606 | 0.1171 | 0.9117 | 0.0965 |
| Naive Bayes | 0.6829 | 0.1817 | 0.3075 | 0.0002 | 0.6029 | 0.0094 |
