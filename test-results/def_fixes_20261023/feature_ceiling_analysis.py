"""DEF-04 to DEF-07 root cause: what detection quality the flow features allow on unseen data.

Trains on the cycle 1 model B training rows (the UNSW-NB15 training file minus the hold-out)
and scores the cycle 1 hold-out (15,939 rows no training row shares 15 per-flow features with).
For each feature set it reports ROC-AUC, the best recall with at most 8% false positives, and
the best F1 at any threshold. Thresholds here are chosen with the hold-out labels, so they are
upper bounds for analysis, not results: the official TC-03 result is evaluate_model.py at the
model's own threshold.

  1. Cycle 1 model B (the project's Stacking Ensemble, 15 features) at its best threshold.
  2. A strong single learner (HistGradientBoosting) on the 15 features, and on 15 + derived
     ratios, 15 + per-flow TCP/HTTP fields, 15 + UNSW connection counts, and all features.
  3. Each connection count added alone, then the destination-keyed set used by schema v2.
     (The final schema v2 also drops sttl and dttl; see ttl_experiment.py.)

Run from the repository folder, inside the venv:
    python test-results/def_fixes_20261023/feature_ceiling_analysis.py > <log>
"""
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score, roc_curve

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "research" / "dataset" / "processed" / "sys01_holdout"
F15 = ["dur", "proto", "service", "state", "spkts", "dpkts", "sbytes", "dbytes", "rate", "sttl",
       "dttl", "sload", "dload", "sinpkt", "dinpkt"]
CAT = ["proto", "service", "state"]
COUNTS = ["ct_srv_src", "ct_state_ttl", "ct_dst_ltm", "ct_src_dport_ltm", "ct_dst_sport_ltm",
          "ct_dst_src_ltm", "ct_src_ltm", "ct_srv_dst", "is_sm_ips_ports"]
SCHEMA_V2 = F15 + ["ct_dst_src_ltm", "ct_dst_ltm", "ct_srv_dst"]

train = pd.read_csv(DATA / "model_b_train.csv")
holdout = pd.read_csv(DATA / "holdout_eval.csv")
y_train = train["label"].astype(int)
y_holdout = holdout["label"].astype(int).to_numpy()
positives, negatives = int(y_holdout.sum()), int(len(y_holdout) - y_holdout.sum())


def summarise(name, probabilities):
    fpr, tpr, _ = roc_curve(y_holdout, probabilities)
    at_8 = tpr[np.searchsorted(fpr, 0.08, side="right") - 1]
    best_f1 = max(2 * t * positives / (t * positives + f * negatives + positives)
                  for f, t in zip(fpr, tpr))
    print(f"{name:58s} ROC-AUC {roc_auc_score(y_holdout, probabilities):.4f}  "
          f"recall@FPR<=8% {at_8:.3f}  best F1 {best_f1:.3f}", flush=True)


def single_learner(name, columns):
    both = pd.concat([train[columns], holdout[columns]])
    for column in CAT:
        if column in both:
            both[column] = both[column].astype("category")
    model = HistGradientBoostingClassifier(categorical_features="from_dtype", max_iter=500,
                                           learning_rate=0.05, random_state=42)
    model.fit(both.iloc[:len(train)], y_train)
    summarise(name, model.predict_proba(both.iloc[len(train):])[:, 1])


print(f"train rows {len(train)}, hold-out rows {len(holdout)} "
      f"({positives} attack, {negatives} normal)\n")
print("1. Cycle 1 model B (Stacking Ensemble, 15 features)")
artifact = joblib.load(ROOT / "saved_models" / "cycle1_B" / "run_6" / "stacking.joblib")
summarise("model B stacking", artifact["pipeline"].predict_proba(holdout[F15])[:, 1])

print("\n2. Feature families (HistGradientBoosting)")
derived = {"smean": lambda d: d["sbytes"] / d["spkts"].clip(lower=1),
           "dmean": lambda d: d["dbytes"] / d["dpkts"].clip(lower=1),
           "byte_ratio": lambda d: d["sbytes"] / (d["dbytes"] + 1),
           "pkt_ratio": lambda d: d["spkts"] / (d["dpkts"] + 1)}
for frame in (train, holdout):
    for column, function in derived.items():
        frame[column] = function(frame)
single_learner("15 per-flow features", F15)
single_learner("15 + derived ratios", F15 + list(derived))
single_learner("15 + per-flow TCP/HTTP fields", F15 + ["swin", "dwin", "stcpb", "dtcpb", "synack",
                                                       "ackdat", "tcprtt", "trans_depth",
                                                       "response_body_len"])
single_learner("15 + all connection counts", F15 + COUNTS)
single_learner("all UNSW features", [c for c in train.columns
                                     if c not in ("id", "label", "attack_cat", *derived)])

print("\n3. Connection counts one at a time, then schema v2")
for column in COUNTS:
    single_learner(f"15 + {column}", F15 + [column])
single_learner("schema v2: 15 + ct_dst_src_ltm, ct_dst_ltm, ct_srv_dst", SCHEMA_V2)
sys.exit(0)
