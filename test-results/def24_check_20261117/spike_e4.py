"""Evidence for DEF-24 (2026-11-17), moved from the session scratchpad; see summary.md.

THROWAWAY spike (DEF-24), part 4: does the app's own deployable model (the Stacking Ensemble,
built exactly as services/model_registry.py does) behave like the part-3 candidate?

One split (part 3's fold 0 of file 2 held out): train = F1train + PC A local benign repeated 10x
(Stacking cannot take sample weights because KNN cannot) + the other two thirds of S2.
Variants (argv[1]): stack_app (unchanged), stack_hgb (Gradient Boosting base learner swapped for
HistGradientBoosting), hgb (HistGradientBoosting alone, same split, reference).
Scores are saved for a line sweep. Never reads run11/final_test.csv.
"""
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.model_selection import StratifiedGroupKFold
from sklearn.pipeline import Pipeline

sys.path.insert(0, str(Path(__file__).resolve().parent))
from spike_e1 import CATS, V2, keys, load  # noqa: E402
from spike_e2 import SEL_CAPS, TRAIN_CAPS, pcap_flows  # noqa: E402

from services.model_registry import build_model_candidates  # noqa: E402
from services.preprocessing_service import build_feature_preprocessor  # noqa: E402

OUT = Path(__file__).resolve().parent
NUMS = [c for c in V2 if c not in CATS]


def hgb():
    return HistGradientBoostingClassifier(max_iter=400, learning_rate=0.1, max_leaf_nodes=63,
                                          class_weight="balanced", random_state=42)


def build(variant):
    if variant == "hgb":
        clf = hgb()
    else:
        clf = build_model_candidates()["stacking"]["estimator"]
        if variant == "stack_hgb":
            clf.estimators = [(n, hgb() if n == "gradient_boosting" else e)
                              for n, e in clf.estimators]
    return Pipeline([("preprocessor", build_feature_preprocessor(NUMS, CATS)),
                     ("classifier", clf)])


def main():
    variant = sys.argv[1]
    t0 = time.time()
    f1, ho, s2 = load()
    caps = {n: pcap_flows(n) for n in TRAIN_CAPS + SEL_CAPS}
    for frame in [f1, ho, s2] + list(caps.values()):
        frame["service"] = frame["service"].astype(str)
    local = pd.concat([caps[n] for n in TRAIN_CAPS], ignore_index=True).assign(label=0)
    tr, te = next(iter(StratifiedGroupKFold(n_splits=3, shuffle=True, random_state=7)
                       .split(s2, s2["attack_cat"], keys(s2))))
    train = pd.concat([f1[V2 + ["label"]]] + [local[V2 + ["label"]]] * 10
                      + [s2.iloc[tr][V2 + ["label"]]], ignore_index=True)
    print(f"{variant}: training on {len(train)} rows", flush=True)
    m = build(variant).fit(train[V2], train["label"])
    print(f"{variant}: fitted in {time.time()-t0:.0f}s", flush=True)
    attack = list(m.classes_).index(1)
    out = {"s2_y": s2.iloc[te]["label"].to_numpy(),
           "s2_cat": s2.iloc[te]["attack_cat"].to_numpy().astype(str),
           "s2_p": m.predict_proba(s2.iloc[te][V2])[:, attack],
           "ho_y": ho["label"].to_numpy(), "ho_p": m.predict_proba(ho[V2])[:, attack]}
    for c in SEL_CAPS:
        out["cap_" + c] = m.predict_proba(caps[c][V2])[:, attack]
    np.savez(OUT / f"e4_{variant}.npz", **out)
    print(f"{variant}: done in {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
