"""Evidence for DEF-24 (2026-11-17), moved from the session scratchpad; see summary.md.

THROWAWAY spike (DEF-24), part 3: with both files in training, is there a decision line where
the held slice of file 2 meets every target AND each benign recording stays at most 8% Attack?
Levers: weight on PC A's local benign flows, weight on Fuzzers, Random Forest vs gradient boosting.
Also: how many file-2 Fuzzers are exact copies of a Normal row (no model can separate those).

Never reads research/dataset/processed/run11/final_test.csv.
"""
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import StratifiedGroupKFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

sys.path.insert(0, str(Path(__file__).resolve().parent))
from spike_e1 import CATS, V2, keys, load, model  # noqa: E402
from spike_e2 import SEL_CAPS, TRAIN_CAPS, pcap_flows  # noqa: E402

OUT = Path(__file__).resolve().parent
LINES = [round(x, 2) for x in np.arange(0.05, 0.96, 0.05)]
VARIANTS = {
    "rf": ("rf", 1.0, 1.0),
    "rf_local10": ("rf", 10.0, 1.0),
    "rf_local10_fuzz2": ("rf", 10.0, 2.0),
    "hgb_local10": ("hgb", 10.0, 1.0),
    "hgb_local10_fuzz2": ("hgb", 10.0, 2.0),
}


def hgb(features):
    cats = [c for c in features if c in CATS]
    nums = [c for c in features if c not in CATS]
    pre = ColumnTransformer([("cat", OneHotEncoder(handle_unknown="ignore", sparse_output=False,
                                                   min_frequency=20), cats),
                             ("num", "passthrough", nums)])
    clf = HistGradientBoostingClassifier(max_iter=400, learning_rate=0.1, max_leaf_nodes=63,
                                         class_weight="balanced", random_state=42)
    return Pipeline([("pre", pre), ("clf", clf)])


def at_line(y, p, line):
    pred = p >= line
    tp = int((pred & (y == 1)).sum())
    fp = int((pred & (y == 0)).sum())
    fn = int((~pred & (y == 1)).sum())
    tn = int((~pred & (y == 0)).sum())
    rec = tp / max(tp + fn, 1)
    prec = tp / max(tp + fp, 1)
    return {"acc": (tp + tn) / len(y), "f1": 2 * prec * rec / max(prec + rec, 1e-12),
            "recall": rec, "fpr": fp / max(fp + tn, 1)}


def main():
    t0 = time.time()
    f1, ho, s2 = load()
    caps = {n: pcap_flows(n) for n in TRAIN_CAPS + SEL_CAPS}
    for frame in [f1, ho, s2] + list(caps.values()):
        frame["service"] = frame["service"].astype(str)
    local = pd.concat([caps[n] for n in TRAIN_CAPS], ignore_index=True).assign(
        label=0, attack_cat="Normal (local)")

    # Ceiling: file-2 Fuzzers whose 16 features equal some Normal row's.
    allrows = pd.concat([f1[V2 + ["label"]], s2[V2 + ["label"]], local[V2 + ["label"]]])
    norm_keys = set(map(tuple, allrows[allrows.label == 0][V2].round(6).astype(str).to_numpy()))
    fz = s2[s2.attack_cat == "Fuzzers"]
    fz_keys = list(map(tuple, fz[V2].round(6).astype(str).to_numpy()))
    twin_share = round(sum(k in norm_keys for k in fz_keys) / len(fz_keys), 4)
    print(f"file-2 Fuzzers identical to a Normal row (16 features): {twin_share}", flush=True)

    folds = list(StratifiedGroupKFold(n_splits=3, shuffle=True, random_state=7)
                 .split(s2, s2["attack_cat"], keys(s2)))
    y, cats = s2["label"].to_numpy(), s2["attack_cat"].to_numpy()
    yho = ho["label"].to_numpy()
    report = {"fuzzers_twin_of_normal": twin_share, "variants": {}}
    for name, (kind, w_local, w_fuzz) in VARIANTS.items():
        t = time.time()
        oof = np.zeros(len(s2))
        p_ho = np.zeros(len(ho))
        p_caps = {c: np.zeros(len(caps[c])) for c in SEL_CAPS}
        columns = V2 + ["label", "attack_cat"]
        for tr, te in folds:
            train = pd.concat([f1[columns], local[columns], s2.iloc[tr][columns]],
                              ignore_index=True)
            w = np.where(train.attack_cat == "Normal (local)", w_local, 1.0)
            w = np.where(train.attack_cat == "Fuzzers", w * w_fuzz, w)
            m = model(V2) if kind == "rf" else hgb(V2)
            step = "rf" if kind == "rf" else "clf"
            m.fit(train[V2], train["label"], **{f"{step}__sample_weight": w})
            oof[te] = m.predict_proba(s2.iloc[te][V2])[:, 1]
            p_ho += m.predict_proba(ho[V2])[:, 1] / len(folds)
            for c in SEL_CAPS:
                p_caps[c] += m.predict_proba(caps[c][V2])[:, 1] / len(folds)
        sweep = []
        for line in LINES:
            s = at_line(y, oof, line)
            s["fuzzers_recall"] = float((oof[cats == "Fuzzers"] >= line).mean())
            h = at_line(yho, p_ho, line)
            cap = {c: float((p_caps[c] >= line).mean()) for c in SEL_CAPS}
            row = {"line": line, **{f"s2_{k}": round(v, 4) for k, v in s.items()},
                   "ho_fpr": round(h["fpr"], 4), "ho_recall": round(h["recall"], 4),
                   "caps_max": round(max(cap.values()), 4)}
            row["s2_targets_met"] = (s["acc"] >= .9 and s["f1"] >= .9 and s["recall"] >= .9
                                     and s["fpr"] <= .08)
            row["all_met_incl_browsing"] = row["s2_targets_met"] and row["caps_max"] <= .08
            sweep.append(row)
        auc = round(float(roc_auc_score(y, oof)), 4)
        report["variants"][name] = {"s2_auc": auc, "sweep": sweep}
        ok = [r["line"] for r in sweep if r["all_met_incl_browsing"]]
        print(f"\n== {name} ({time.time()-t:.0f}s) S2 AUC {auc}; lines meeting all: {ok}",
              flush=True)
        for r in sweep:
            if r["line"] in (0.1, 0.2, 0.25, 0.3, 0.35, 0.4, 0.45, 0.5, 0.6, 0.7):
                print(f"  {r['line']:.2f} S2 acc {r['s2_acc']:.3f} f1 {r['s2_f1']:.3f} "
                      f"rec {r['s2_recall']:.3f} fpr {r['s2_fpr']:.3f} fuzz "
                      f"{r['s2_fuzzers_recall']:.3f} | HO fpr {r['ho_fpr']:.3f} | "
                      f"browse max {r['caps_max']:.3f}", flush=True)
    (OUT / "e3_results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"done {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
