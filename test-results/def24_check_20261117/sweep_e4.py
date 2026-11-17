"""Evidence for DEF-24 (2026-11-17), moved from the session scratchpad; see summary.md.

Line sweep over the part-4 scores (spike_e4.py writes e4_<variant>.npz). For each line: the held
file-2 slice (accuracy, F1, recall, FPR, Fuzzers caught), the hold-out (FPR, recall), file 2 and
the hold-out combined (file-2 counts scaled to the 32,610 reserved rows), and the worst benign
recording. Output: e4_sweep.txt.

    python sweep_e4.py <folder holding e4_hgb.npz, e4_stack_app.npz, e4_stack_hgb.npz>
"""
import sys

import numpy as np
from sklearn.metrics import roc_auc_score

LINES = (0.2, 0.25, 0.3, 0.35, 0.4, 0.45, 0.5, 0.6)
RESERVED_ROWS = 32610


def counts(y, p, line):
    """Accuracy, F1, recall, FPR and the confusion counts when p >= line means Attack."""
    pred = p >= line
    tp = (pred & (y == 1)).sum()
    fp = (pred & (y == 0)).sum()
    fn = (~pred & (y == 1)).sum()
    tn = (~pred & (y == 0)).sum()
    recall = tp / (tp + fn)
    precision = tp / max(tp + fp, 1)
    f1 = 2 * precision * recall / (precision + recall)
    return (tp + tn) / len(y), f1, recall, fp / (fp + tn), tp, fp, tp + fn, fp + tn


def main():
    folder = sys.argv[1]
    for variant in ("hgb", "stack_app", "stack_hgb"):
        z = np.load(f"{folder}/e4_{variant}.npz")
        y, p, cat = z["s2_y"], z["s2_p"], z["s2_cat"]
        print(f"== {variant}: held file-2 slice {len(y)} rows, AUC {roc_auc_score(y, p):.4f}; "
              f"hold-out AUC {roc_auc_score(z['ho_y'], z['ho_p']):.4f}")
        recordings = [key for key in z.files if key.startswith("cap_")]
        for line in LINES:
            acc, f1, rec, fpr, tp, fp, pos, neg = counts(y, p, line)
            _, _, ho_rec, ho_fpr, ho_tp, ho_fp, ho_pos, ho_neg = counts(z["ho_y"], z["ho_p"], line)
            # Combined: the file-2 slice scaled to the reserved rows, plus the hold-out.
            scale = RESERVED_ROWS / len(y)
            c_tp, c_fp = tp * scale + ho_tp, fp * scale + ho_fp
            c_pos, c_neg = pos * scale + ho_pos, neg * scale + ho_neg
            c_rec, c_prec, c_fpr = c_tp / c_pos, c_tp / (c_tp + c_fp), c_fp / c_neg
            c_acc = (c_tp + c_neg - c_fp) / (c_pos + c_neg)
            c_f1 = 2 * c_prec * c_rec / (c_prec + c_rec)
            fuzzers = (p[cat == "Fuzzers"] >= line).mean()
            browse = max((z[key] >= line).mean() for key in recordings)
            met = acc >= .9 and f1 >= .9 and rec >= .9 and fpr <= .08 and browse <= .08
            print(f"  {line:.2f} F2 acc {acc:.3f} f1 {f1:.3f} rec {rec:.3f} fpr {fpr:.3f} "
                  f"fuzz {fuzzers:.3f} | HO fpr {ho_fpr:.3f} rec {ho_rec:.3f} | comb acc "
                  f"{c_acc:.3f} f1 {c_f1:.3f} rec {c_rec:.3f} fpr {c_fpr:.3f} | browse max "
                  f"{browse:.3f} {'ALL F2+BROWSE MET' if met else ''}")


if __name__ == "__main__":
    main()
