"""Evidence for DEF-24 (2026-11-17), moved from the session scratchpad; see summary.md.

THROWAWAY spike (DEF-24): do UNSW-NB15 measurements the app does not use yet separate the
Fuzzers that model 60 misses? Never reads research/dataset/processed/run11/final_test.csv.

Data:
  F1train  = repository file (official testing partition) minus the hold-out ids (66,393 rows)
  HO       = the hold-out (15,939 rows, same file)
  S2       = the 76,089 official-training-partition rows run 12 already trained on, found by
             matching run11/training.csv rows 68,853+ back to the pool by the 13-feature key.
E1: Random Forest trained on F1train with each feature set, scored on S2 (cross-file, the DEF-24
    situation) and on HO.
"""
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from services.flow_tracker_service import (  # noqa: E402
    CONNECTION_COUNT_COLUMNS,
    FLOW_FEATURE_COLUMNS,
)

OUT = Path(__file__).resolve().parent
KEY = [c for c in FLOW_FEATURE_COLUMNS if c not in CONNECTION_COUNT_COLUMNS]
CATS = ["proto", "service", "state"]
V2 = list(FLOW_FEATURE_COLUMNS)
GROUPS = {
    "size_mean": ["smean", "dmean"],
    "tcp_window": ["swin", "dwin"],
    "handshake": ["tcprtt", "synack", "ackdat"],
    "jitter": ["sjit", "djit"],
    "loss": ["sloss", "dloss"],
    "http": ["trans_depth", "response_body_len", "ct_flw_http_mthd"],
    "src_counts": ["ct_src_ltm", "ct_src_dport_ltm", "ct_dst_sport_ltm", "ct_srv_src"],
    "sm_ips_ports": ["is_sm_ips_ports"],
    "ttl_REFERENCE_ONLY": ["sttl", "dttl", "ct_state_ttl"],
}


def keys(frame):
    norm = pd.DataFrame(index=frame.index)
    for col in KEY:
        column = frame[col]
        if pd.api.types.is_numeric_dtype(column):
            norm[col] = column.astype(float).round(9)
        else:
            norm[col] = column.astype(str).str.strip()
    return pd.Series([hash(r) for r in norm.itertuples(index=False, name=None)],
                     index=frame.index)


def load():
    used = pd.read_csv(ROOT / "research/dataset/raw/UNSW_NB15_training-set.csv",
                       encoding="utf-8-sig")
    official = pd.read_csv(
        ROOT / "research/dataset/processed/official_unsw/UNSW_NB15_training-set.csv",
        encoding="utf-8-sig")
    run11 = pd.read_csv(ROOT / "research/dataset/processed/run11/training.csv")
    added = run11.iloc[68853:]
    pool = official[~keys(official).isin(set(keys(used)))].reset_index(drop=True)
    s2 = pool[keys(pool).isin(set(keys(added)))].reset_index(drop=True)
    if len(s2) != len(added) or int(s2["label"].sum()) != int(added["label"].sum()):
        raise SystemExit(f"S2 match failed: {len(s2)} rows vs {len(added)}")
    hold = set(pd.read_csv(ROOT / "research/dataset/holdout_ids.csv")["id"].astype(int))
    f1 = used[~used["id"].astype(int).isin(hold)].reset_index(drop=True)
    ho = used[used["id"].astype(int).isin(hold)].reset_index(drop=True)
    for frame in (f1, ho, s2):
        frame["attack_cat"] = frame["attack_cat"].astype(str).str.strip()
        for c in CATS:
            frame[c] = frame[c].astype(str).str.strip()
    return f1, ho, s2


def model(features):
    cats = [c for c in features if c in CATS]
    nums = [c for c in features if c not in CATS]
    pre = ColumnTransformer([("cat", OneHotEncoder(handle_unknown="ignore"), cats),
                             ("num", "passthrough", nums)])
    rf = RandomForestClassifier(n_estimators=100, class_weight="balanced", random_state=42,
                                n_jobs=-1)
    return Pipeline([("pre", pre), ("rf", rf)])


def metrics(y, p, cats):
    pred = (p >= 0.5).astype(int)
    tp = int(((pred == 1) & (y == 1)).sum())
    fp = int(((pred == 1) & (y == 0)).sum())
    fn = int(((pred == 0) & (y == 1)).sum())
    tn = int(((pred == 0) & (y == 0)).sum())
    rec = tp / max(tp + fn, 1)
    prec = tp / max(tp + fp, 1)
    out = {"acc": (tp + tn) / len(y), "f1": 2 * prec * rec / max(prec + rec, 1e-12),
           "recall": rec, "fpr": fp / max(fp + tn, 1), "auc": roc_auc_score(y, p)}
    fz = cats == "Fuzzers"
    out["fuzzers_recall"] = float(pred[fz].mean()) if fz.any() else None
    return {k: (round(float(v), 4) if v is not None else None) for k, v in out.items()}


def main():
    t0 = time.time()
    f1, ho, s2 = load()
    print(f"F1train {len(f1)}  HO {len(ho)}  S2 {len(s2)} (attack {int(s2.label.sum())}, "
          f"Fuzzers {int((s2.attack_cat == 'Fuzzers').sum())})  load {time.time()-t0:.0f}s",
          flush=True)

    # Univariate: how well does each candidate alone separate S2 Fuzzers from S2 Normal?
    uni = {}
    fz = s2[s2.attack_cat == "Fuzzers"]
    nm = s2[s2.attack_cat == "Normal"]
    y = np.r_[np.ones(len(fz)), np.zeros(len(nm))]
    for cols in GROUPS.values():
        for c in cols:
            auc = roc_auc_score(y, np.r_[fz[c].to_numpy(float), nm[c].to_numpy(float)])
            uni[c] = round(max(auc, 1 - auc), 4)
    for c in V2:
        if c in CATS:
            continue
        auc = roc_auc_score(y, np.r_[fz[c].to_numpy(float), nm[c].to_numpy(float)])
        uni["v2:" + c] = round(max(auc, 1 - auc), 4)

    sets = {"v2": V2}
    for name, cols in GROUPS.items():
        sets["v2+" + name] = V2 + cols
    safe = [c for n, cols in GROUPS.items() if n != "ttl_REFERENCE_ONLY" for c in cols]
    sets["v2+all_non_ttl"] = V2 + safe

    results = {}
    for name, feats in sets.items():
        t = time.time()
        m = model(feats).fit(f1[feats], f1["label"])
        results[name] = {
            "S2_crossfile": metrics(s2["label"].to_numpy(), m.predict_proba(s2[feats])[:, 1],
                                    s2["attack_cat"].to_numpy()),
            "HO_samefile": metrics(ho["label"].to_numpy(), m.predict_proba(ho[feats])[:, 1],
                                   ho["attack_cat"].to_numpy()),
        }
        print(f"{name:28s} S2 {results[name]['S2_crossfile']}  "
              f"HO fpr {results[name]['HO_samefile']['fpr']} rec "
              f"{results[name]['HO_samefile']['recall']}  ({time.time()-t:.0f}s)", flush=True)

    (OUT / "e1_results.json").write_text(json.dumps(
        {"univariate_auc_s2_fuzzers_vs_normal": dict(sorted(uni.items(), key=lambda kv: -kv[1])),
         "e1": results}, indent=2), encoding="utf-8")
    print("univariate (S2 Fuzzers vs Normal):",
          dict(sorted(uni.items(), key=lambda kv: -kv[1])[:20]))
    print(f"done {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
