# Model evaluation — 2026-10-19T15:59:00

- Commit: `dafe5e5` · Python 3.14.6 · scikit-learn 1.9.0
- Model: `stacking.joblib` (Stacking Ensemble), trained 2026-10-08 05:38:46
- Model SHA-256: `9c9f0fb18524eeda528180355d4d2f7318ea4b351530394d9460474d7415847c`
- Data: `holdout_eval.csv` — 15939 rows loaded, 0 removed as seen in training, **15939 evaluated** (6174 attack, 9765 normal)

## Results against the test plan targets (section 3.3)

| Metric | Target | Measured | Result |
|---|---|---|---|
| accuracy | >= 0.9 | 0.8169 | Not met |
| f1 | >= 0.9 | 0.7956 | Not met |
| recall | >= 0.9 | 0.9203 | Met |
| false_positive_rate | <= 0.08 | 0.2485 | Not met |
| roc_auc | >= 0.95 | 0.9375 | Not met |

Precision 0.7007 · specificity 0.7515 · prediction 0.144 ms per row

## Confusion matrix

| | Predicted Normal | Predicted Attack |
|---|---|---|
| Actual Normal | 7338 | 2427 |
| Actual Attack | 492 | 5682 |

## By attack category (SYS-03)

| Category | Rows | Measure | Rate |
|---|---|---|---|
| Normal | 9765 | specificity (labelled Normal) | 0.7515 |
| Fuzzers | 1520 | recall (flagged as Attack) | 0.752 |
| Worms | 14 | recall (flagged as Attack) | 0.8571 |
| Shellcode | 113 | recall (flagged as Attack) | 0.885 |
| Generic | 252 | recall (flagged as Attack) | 0.9127 |
| DoS | 647 | recall (flagged as Attack) | 0.9691 |
| Exploits | 2800 | recall (flagged as Attack) | 0.9811 |
| Backdoor | 130 | recall (flagged as Attack) | 0.9846 |
| Analysis | 131 | recall (flagged as Attack) | 0.9924 |
| Reconnaissance | 567 | recall (flagged as Attack) | 0.9965 |

## Model comparison on the same rows (SYS-02)

| Model | Accuracy | Recall | F1 | FPR | ROC-AUC | ms/row |
|---|---|---|---|---|---|---|
| Random Forest | 0.8174 | 0.9261 | 0.7971 | 0.2514 | 0.9315 | 0.0076 |
| Stacking Ensemble | 0.8169 | 0.9203 | 0.7956 | 0.2485 | 0.9375 | 0.1438 |
| Gradient Boosting | 0.7851 | 0.978 | 0.779 | 0.3368 | 0.9307 | 0.0032 |
| K-Nearest Neighbors | 0.7785 | 0.9198 | 0.7629 | 0.3108 | 0.897 | 0.0081 |
| AdaBoost | 0.7394 | 0.9739 | 0.7433 | 0.4088 | 0.8924 | 0.0291 |
| Naive Bayes | 0.6866 | 0.1908 | 0.3205 | 0.0 | 0.6107 | 0.0039 |
