# How AlgoGuard's models are trained, evaluated and chosen

This document explains, in one place, where the detection model comes from: the data, the
features, the six candidates, how they are compared, the gate a model must pass before it is
deployed, how the deployed model is measured on data it has never seen, and what the measured
results mean. Every figure is taken from a committed record, named in brackets. The commands
at the end reproduce the whole chain.

It is written for readers of the thesis (Chapter 4, methodology and results) and for teammates
who need to explain the model. Nothing here is required to install or use AlgoGuard; the
analyst guide covers that.

## 1. Anomaly or attack? What the model actually learns

The thesis title says *network anomaly detection*; the application's verdicts are named
**Normal** and **Attack**, with an **attack score**. Both are right, and the difference matters:

- AlgoGuard learns by **supervised classification** from UNSW-NB15, a dataset in which every
  flow is labelled Normal or one of nine attack categories. The model learns what attack
  traffic looks like *compared with* normal traffic. Its output is the probability that a flow
  belongs to the Attack class.
- An **anomaly**, in the wider sense, is a flow that does not look like normal traffic. In
  AlgoGuard, an anomaly is reported as an Attack verdict, because the only examples of
  abnormal traffic the model was trained on are attacks. The attack score says "this flow
  resembles the attacks in the training data"; it is not a measure of harm or impact.
- AlgoGuard is **not** an unsupervised anomaly detector that learns one network's own baseline
  and flags departures from it. A new kind of attack that resembles nothing in UNSW-NB15 can
  pass as Normal, and ordinary traffic that the dataset never contained can be flagged
  (section 8, false alarms on live traffic).

So the accurate one-line description is: *a supervised network anomaly detector that learns
attack traffic from labelled data and reports flows resembling it as attacks.* The word
"anomaly" names the goal; "Attack" names the class the model was taught.

## 2. Data

### 2.1 UNSW-NB15

UNSW-NB15 (UNSW Canberra, 2015) is a labelled dataset of network flows with 49 features and
nine attack categories (Fuzzers, Analysis, Backdoor, DoS, Exploits, Generic, Reconnaissance,
Shellcode, Worms). It is published with two official CSV partitions: a **training set** of
175,341 rows and a **testing set** of 82,332 rows.

One naming fact affects every result below. The file committed to this repository as
`research/dataset/raw/UNSW_NB15_training-set.csv` is byte-for-byte the official **testing**
partition (82,332 rows) [`research/README.md`; `docs/migration/06a-evidence.md`, 6A.2]. Every
model up to the deployed one was therefore trained on rows of the official testing partition,
and the official training partition (175,341 rows) was untouched until Stage 6A used it as the
untouched evaluation set (section 6) and training run 12 trained on part of it (section 7).

### 2.2 The 16 features (feature schema v2)

Live capture, PCAP replay and the training data all use the same 16 features, computed by one
flow tracker [`services/flow_tracker_service.py`]:

| Group | Features | Meaning |
|---|---|---|
| Per-flow (13) | `dur`, `proto`, `service`, `state`, `spkts`, `dpkts`, `sbytes`, `dbytes`, `rate`, `sload`, `dload`, `sinpkt`, `dinpkt` | Duration, protocol, application service, connection state, packets and bytes in each direction, packet rate, bits per second each way, mean gap between packets each way |
| Connection counts (3) | `ct_dst_src_ltm`, `ct_dst_ltm`, `ct_srv_dst` | Among the last 100 finished connections: those with the same source and destination, the same destination, and the same service and destination |

Two UNSW-NB15 features are deliberately **not** used, and one is reported differently:

- **No TTL features** (`sttl`, `dttl`). In the dataset they mostly identify the lab's own hosts.
  A Windows PC sends with TTL 128, a value the dataset never contains, and on the same benign
  live flows a model with TTLs labelled 97% Attack against 82% without
  [`test-results/def_fixes_20261023/ttl_experiment.json`].
- **Only destination-keyed connection counts.** On a single capturing PC almost every flow
  shares the source address, so source-keyed counts would sit at the window size.
- **HTTPS reports service `-`.** The dataset's `ssl` rows are all attacks (20 of 20 in the
  repository file, 56 of 56 in the official training partition), so a model that sees `ssl`
  reads every HTTPS connection as an attack [DEF-19 part 1, commit 6cb88e6].

Stage 6A.1 checked the live features against Argus, the tool that produced UNSW-NB15: on a
fresh capture 15 of 16 agreed within predeclared tolerances; `sload` agrees on 99% of flows once
Ethernet-header bytes are discounted [`docs/migration/06a-evidence.md`, addendum].

### 2.3 Hold-out and training files

The evaluation sets were fixed before any model was chosen, so that selection and measurement
never use the same rows:

| Set | Rows | How it was built | Used for |
|---|---|---|---|
| **Hold-out** | 15,939 (6,174 attack, 9,765 normal) | 30% of the per-flow feature groups that the cycle 1 model had never seen, drawn with seed 42; listed in `research/dataset/holdout_ids.csv`. No training row shares its per-flow features with a hold-out row [`test-results/sys01_holdout/holdout_manifest.json`] | Selecting between candidates (the *gate*), calibration checks |
| **Training file (schema v2)** | 66,393 | Every other row of the repository file, with the 16 features and the label [`scripts/build_flow_datasets.py`] | Training runs 8 and 9 |
| **Training file with local benign traffic** | 68,853 (56.9% attack) | The 66,393 rows plus 2,460 benign flows from two 10-minute captures on PC A, labelled Normal: 410 from a page generator and 2,050 from headless Chrome with the PC's background traffic [`test-results/iter6_6a/results_def19_part2.md`] | Training run 10, the **deployed model** |
| **Untouched set** | 108,700 (57,798 attack, 50,902 normal) | The official training partition minus every row whose per-flow features appear in the training file [`test-results/cycle2_qa_20261111/tc03/summary.md`] | The final, once-only measurement of the deployed model (TC-03) |
| **Run 11 training file** | 144,942 | The 68,853 rows plus 76,089 rows of the official training partition (7 of 10 grouped folds of the 108,699 rows not matching the repository file) [`test-results/model_run11/protocol_run11.md`] | Training run 12 (not deployed) |
| **Run 11 final test** | 32,610 | The other 3 folds; no model has read it | Reserved for a future candidate |

The three bundled samples (`datasets/algoguard_big.csv` 5,000 rows, `algoguard_bigger.csv`
10,000, `algoguard_biggest.csv` 20,000) are seeded samples of the training file. They are for
demonstrations and the Live Monitor's labelled replay; the match rate on them is **not** an
accuracy claim, because the model has seen those rows.

## 3. Preprocessing and the check split

`train.py` reads one CSV whose last column is the label [`services/preprocessing_service.py`]:

1. **Label encoding.** Exactly two classes are required. `normal`, `benign`, `0` and similar
   become 0; the other class becomes 1 (Attack). `id` and `attack_cat` are never features: a
   model trained on the attack name scores 100% and learns nothing about traffic (DEF-08).
2. **Feature selection.** A file with all 16 flow features trains on exactly those 16, so the
   model can be published and used on captured traffic.
3. **Stratified split, 75% train / 25% check**, seed 42. This in-app check split gives the
   comparison table and feeds the deployment gate (section 5). Its figures read high because
   UNSW-NB15 repeats identical flows, so the check rows often duplicate training rows; the
   real measurement is the untouched set (section 6).
4. **Preprocessing inside every pipeline**, fitted on the training rows only (no leakage):
   numeric columns get median imputation and standard scaling; `proto`, `service` and `state`
   get most-frequent imputation and one-hot encoding (unknown values are ignored at prediction
   time). The fitted preprocessing is saved with the classifier in one artifact.

## 4. The six candidates

Every training run trains the same six candidates from fresh, unfitted instances
[`services/model_registry.py`]:

| Candidate | Settings |
|---|---|
| Random Forest | 100 trees, balanced class weights, fixed seed |
| Gradient Boosting | scikit-learn defaults, fixed seed |
| AdaBoost | 100 estimators, fixed seed |
| K-Nearest Neighbors | k = 5 |
| Naive Bayes | Gaussian |
| **Stacking Ensemble** | The five above as base learners, each producing class probabilities through 5-fold cross-validation; a Logistic Regression (balanced class weights, up to 1,000 iterations) learns how to combine the five probability outputs |

Stacking is a *second-level* model: it does not vote. During training, each base learner is
fitted on four fifths of the training rows and predicts the fifth, so the final estimator
learns from predictions the base learners made on rows they had not seen. At prediction time
the five base learners score the flow and the logistic regression turns their five
probabilities into one attack probability.

**Ranking of the five individual models** is for research comparison only. Nine metrics
(accuracy, precision, recall, F1, ROC-AUC, false-positive rate, CPU time, peak RAM increase
and artifact size) are min-max normalised across the valid candidates and averaged with equal
weights [`services/evaluation_service.py`]. The top individual model can never be deployed;
the ranking shows how the base learners compare, which is the research question the ensemble
answers.

Training run 10's comparison table (in-app check split, 25% of the 68,853 rows)
[`reports/training_run_10_model_results.csv`]:

| Model | Accuracy | Precision | Recall | F1 | ROC-AUC | FPR | Rank |
|---|---|---|---|---|---|---|---|
| K-Nearest Neighbors | 95.63% | 96.59% | 95.70% | 96.14% | 98.53% | 4.46% | 1 |
| Random Forest | 97.26% | 98.16% | 97.01% | 97.58% | 99.62% | 2.40% | 2 |
| Gradient Boosting | 94.03% | 94.84% | 94.66% | 94.75% | 98.65% | 6.79% | 3 |
| AdaBoost | 89.64% | 92.26% | 89.26% | 90.74% | 96.43% | 9.87% | 4 |
| Naive Bayes | 54.28% | 100.00% | 19.61% | 32.79% | 62.22% | 0.00% | 5 |
| **Stacking Ensemble** | **97.17%** | 97.94% | 97.07% | 97.50% | 99.63% | 2.69% | deployable |

K-Nearest Neighbors ranks first although Random Forest has the better accuracy, because the
ranking also rewards small artifacts and low CPU and RAM use. These check-split figures are
optimistic for the reason given in section 3; the figures that count are in section 6.

## 5. Deployment gate and publication

Only the Stacking Ensemble can be deployed, and only if it passes the **quality gate**: at
least 70% accuracy, 70% F1 and 70% ROC-AUC on the check split, a completed run and a complete
evaluation [`services/deployment_service.py`; the minimums are settings]. A run that fails the
gate leaves the previous model running. `train.py --deploy` promotes the model locally;
`publish_model.py` uploads it to the cloud project after checking the quality gate again, the
Python runtime (3.14.6), the 16 features and the file's SHA-256, and makes it the single active
model that every analyst computer downloads and verifies [`docs/MAINTAINER_GUIDE.md`, 5].

The artifact is one compressed joblib file holding the fitted pipeline and its metadata
(feature columns, label mapping, training defaults, runtime): about 11.6 MB for the deployed
model, 28.7 MB for run 12.

## 6. Measuring a model on data it has never seen

`scripts/evaluate_model.py` is the official measurement (test case TC-03). It:

1. loads the artifact and an evaluation CSV with the 16 features, `label` and `attack_cat`;
2. **removes every evaluation row whose per-flow features appear in the training file**
   (13-feature key for schema v2), so only unseen traffic is scored;
3. reports accuracy, precision, recall, F1, false-positive rate (FPR), ROC-AUC, the confusion
   matrix and recall per attack category, and checks them against the test plan's targets.

The targets, fixed in the Master Test Plan (section 3.3) before cycle 1: accuracy, F1 and
attack recall **at least 90%**, FPR **at most 8%**, ROC-AUC **at least 0.95**.

Calibration was checked in Stage 6A.2 with the expected calibration error (ECE, 10 bins): 0.015
on the hold-out, so no calibrator was added; 0.112 on the untouched set, where 21.9% of the
flows scored Low were in fact attacks. The attack score is therefore reliable on traffic like
the training data and not beyond it [`docs/migration/06a-evidence.md`, 6A.2].

## 7. Training runs and what each one showed

| Run | Date | Training rows | Features | Role | Hold-out: accuracy / F1 / recall / FPR / ROC-AUC | Untouched set |
|---|---|---|---|---|---|---|
| 3 | 2026-10-08 | 5,000 (bundled sample) | 15 (with TTLs) | Cycle 1 model A, deployment #1 | 81.7% accuracy, 24.9% FPR | 53,243-row independent set: 81.5% / 79.5% / 92.4% / 25.5% / 0.937 (DEF-04 to DEF-07) |
| 6 | 2026-10-19 | 66,393 | 15 (with TTLs) | Cycle 1 model B, comparison only | 90.8% / 87.4% / 82.2% / 3.8% / 0.971 | – |
| 8 | 2026-10-23 | 66,393 | 16 (schema v2) | Deployment #2 on the pilot | 95.2% / 93.7% / 91.8% / 2.7% / 0.989 | 108,739 rows: 86.0% / 85.2% / 75.9% / 2.5% / 0.969 |
| 9 | 2026-10-23 | 66,393 | 16 | Run 8 repeated: reproducibility | 0 of 15,939 verdicts differ | – |
| **10** | 2026-10-26 | 68,853 | 16 | **Deployed model** (local model 60, deployment #3 on the pilot) | 95.1% / 93.6% / 91.6% / 2.7% / 0.989 | 108,700 rows: **86.2% / 85.4% / 76.0% / 2.3% / 0.970** (TC-03, 2026-11-11) |
| 12 | 2026-11-11 | 144,942 | 16 | Candidate with the official training partition; **gate failed, not deployed** | 92.7% / 91.0% / 96.0% / 9.4% / 0.984 | not scored (protocol) |

Runs 1, 2 and 4 trained on the same 5,000-row sample during development and cycle 1. Run 5
used all 44 columns of the raw file, including the attack name, by mistake (DEF-08) and is
excluded. Run 7 was feature schema v2 with the TTLs still in (18 features) and was replaced by
run 8 after the TTL finding. Run 11 was interrupted during the Stacking step and repeated
unchanged as run 12. Row and feature counts per run are in the maintainer's training store. Sources: cycle 1 Test Completion Report v1.2,
`test-results/iter6_6a/`, `test-results/model_run11/`, `test-results/cycle2_qa_20261111/tc03/`.

What the sequence shows:

- **Features decide the ceiling, not the learner.** With the 15 original features, no model
  reaches the targets on unseen rows (best F1 about 0.88 even with thresholds chosen using the
  labels). Adding the three connection counts lifts hold-out F1 from 87.4% to 93.7% on the same
  training rows (run 6 against run 8).
- **Dropping the TTLs and reporting HTTPS as `-` fixed most false alarms on live traffic**,
  and adding 2,460 benign flows from the PC (run 10) brought them to 3.2% on familiar sites and
  6.6% on sites no training capture used, with the dataset figures unchanged.
- **More attack rows raised recall but also false alarms.** Run 12 raised hold-out recall from
  91.6% to 96.0% but FPR from 2.7% to 9.4%, and labelled 10.8% of benign flows to unseen sites
  Attack. For an analyst, false alarms on ordinary traffic are what is seen first, so the gate
  written in advance refused it. The two official UNSW-NB15 partitions are not drawn from one
  distribution, so rows from the second file move the decision boundary rather than sharpen it.

## 8. What the measured results mean

The deployed model, on 108,700 flows it had never seen, catches **76.0%** of attacks and
wrongly flags **2.3%** of normal flows; accuracy **86.2%**, F1 **85.4%**, ROC-AUC **0.970**.
Three of the five targets (accuracy, F1, recall) are **not met**; FPR and ROC-AUC are met.
This is recorded as DEF-24 (High) and AlgoGuard is presented as a **limited research pilot**.

Where the misses are: of the 13,869 missed attacks, 11,113 (80%) are **Fuzzers**, caught only
22.8% of the time on the untouched set (79% on the hold-out). Every other category is caught at
54% (Analysis) to 98% (Backdoor); Exploits, DoS, Reconnaissance and Worms above 93%.

On live traffic, the only evidence is benign: 3–7% of ordinary browsing flows on the PC the
model was trained with are labelled Attack (DEF-19 target 8%, met), and real attacks on live
traffic have not been tested. Test cycle 2 added a second PC's benign traffic to that
evidence; its figures are in the cycle 2 Test Completion Report.

## 9. Decision line study (DEF-24, 2026-11-13)

The verdict is Attack when the attack probability is 0.50 or more. Because the model ranks
attacks above normal flows well (ROC-AUC 0.97) but is cautious (FPR 2.3% against the 8%
allowed), the question was whether a lower line could reach 90% recall while keeping false
alarms within target. The study scored model 60 and run 12 at lines from 0.01 to 0.95 on the
selection data only (the hold-out and the four benign captures); the untouched set was used
only to show the ceiling, which is an upper bound and not a result, because choosing a line on
the set it is measured on is not allowed.

A line *passes selection* when hold-out recall is at least 90%, hold-out FPR at most 8% and
each benign capture at most 8% Attack [`test-results/cycle2_qa_20261111/def24_decision_line.py`
and `.json`, 2026-11-13]:

| Model, line | Selection: hold-out recall / FPR, worst benign capture | Untouched set (upper bound): accuracy / F1 / recall / FPR, Fuzzers recall |
|---|---|---|
| Model 60, 0.50 (deployed) | 91.6% / 2.7%, 6.6% — passes | 86.1% / 85.3% / 75.9% / 2.3%, 22.8% |
| Model 60, 0.40 (lowest line that passes) | 92.8% / 3.2%, 7.3% — passes | 86.5% / 85.9% / 77.2% / 2.8%, 25.5% |
| Model 60, 0.03 (best accuracy on the untouched set) | 98.6% / 14.8%, 30.6% — fails | 89.4% / 90.0% / 89.9% / 11.3%, 65.3% |
| Run 12, 0.50 (as gated) | 96.0% / 9.4%, 10.8% — fails | not scored: it trained on that partition |
| Run 12, 0.70 | 92.8% / 6.0%, 6.0% — passes | not scored |

What it shows:

- **The line is not the problem.** For model 60, no line meets all targets on the untouched
  set even when the line is chosen *on that set*: the best accuracy is 89.4%, and it needs 11.3%
  false alarms on the dataset and 31% on unseen live browsing. The best line that respects the
  selection rules (0.40) gains less than one point on each target.
- **The missed attacks look normal to the model.** Of the 13,973 attacks model 60 misses at
  0.50, 9,480 (68%) score below 0.10 and 4,385 below 0.02. They sit among normal traffic in the
  16-feature space, so no line can separate them; most are Fuzzers.
- **Run 12 with a stricter line would pass the gate it failed** (at 0.70: recall 92.8%, FPR
  6.0%, unseen browsing 6.0%), but at equal hold-out recall it raises about twice the false
  alarms of model 60 at 0.40, it needs an app change (the verdict now always uses 0.50), and the
  only untouched data left for it comes from the partition it was trained on.

Conclusion: DEF-24 stays a documented limit. Closing it needs better features for Fuzzers-like
traffic, or a new, predeclared protocol around a candidate like run 12; both are future work.

## 10. Reproducing the chain

From the repository folder, inside the venv (about 20 minutes for the data, 20 for one training
run on a laptop):

```powershell
# Data: hold-out, schema v2 training file and the bundled samples
python scripts\build_flow_datasets.py --evidence test-results\<folder>

# Add benign flows recorded on this PC (optional, as run 10 did)
python test-results\iter6_6a\build_def19_training.py --capture <a.pcap> --capture <b.pcap> --out research\dataset\processed\def19\training_local.csv

# Train and compare the six candidates; --deploy promotes Stacking if it passes the gate
python train.py research\dataset\processed\def19\training_local.csv --deploy

# Measure on the hold-out (selection) and on the untouched official training partition
python scripts\evaluate_model.py --model saved_models\run_<id>\stacking.joblib --data research\dataset\processed\flow_window_v2\holdout_eval.csv --train-csv research\dataset\processed\def19\training_local.csv
python scripts\evaluate_model.py --model saved_models\run_<id>\stacking.joblib --data research\dataset\processed\official_unsw\UNSW_NB15_training-set.csv --train-csv research\dataset\processed\def19\training_local.csv

# Share of benign captured flows labelled Attack
python test-results\iter6_6a\benign_attack_share.py --pcap <capture.pcap> --model saved_models\run_<id>\stacking.joblib
```

The official UNSW-NB15 files are downloaded into `research/dataset/processed/official_unsw/`
and stay out of git; the dataset page is <https://research.unsw.edu.au/projects/unsw-nb15-dataset>
and academic work should cite its publications.
