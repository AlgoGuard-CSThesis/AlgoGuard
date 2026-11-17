# Training run 13: protocol (written before any run 13 data was built or model trained)

Written 2026-11-17 23:53 (Asia/Manila) and committed before `build_run13_data.py` was run. The code is that
of commits 9bd15a5 and cb13f3f: workflow `stacking-five-v4` and
`train.py --decision-line`. The rules are the same as run 11's (`../model_run11/protocol_run11.md`):
- every choice below is fixed now;
- the final test is scored once;
- results that miss a target are reported as they are.

Design: `docs/superpowers/specs/2026-11-17-def24-run13-design.md`. Evidence for the choices:
`test-results/def24_check_20261117/summary.md`.

## Why

DEF-24: model 60 catches 76.0% of attacks on unseen UNSW-NB15 rows (target 90%). 80% of its misses
are Fuzzers of the official training partition ("file 2").

The 2026-11-17 check found that the following meets every target on a held slice of file 2, and
keeps PC A's browsing recordings within 8%:
- train on both files;
- weight PC A's benign flows 10x;
- use a histogram Gradient Boosting member;
- set a lower decision line.

## Data roles

| Role | Data |
|---|---|
| Training | `research/dataset/processed/run13/training.csv`: run 11's training file plus the PC A flows 9 more times, 167,082 rows. Run 11's file holds 66,393 file-1 rows, 2,460 PC A benign flows and 76,089 file-2 rows. Built by `build_run13_data.py`; SHA-256 in `run13_data_manifest.json` |
| Line choice (selection) | Out-of-fold scores on the 76,089 file-2 rows: `StratifiedGroupKFold(3, shuffle=True, random_state=13)`, groups = the 13-feature key hashed with `pandas.util.hash_pandas_object`, strata = attack category. Fold averages on the hold-out and the four selection recordings |
| Selection recordings | `research/dataset/processed/def19/generator.pcap`, `browser.pcap`, `final_browser.pcap`, `final_unseen.pcap` |
| Hold-out | `research/dataset/processed/flow_window_v2/holdout_eval.csv` (file 1, 15,939 rows); reported, not gated |
| Fresh browsing | Two 120-second recordings on PC A made after the final model exists |
| Final test | `research/dataset/processed/run11/final_test.csv`: 32,610 rows of file 2, SHA-256 in `../model_run11/run11_data_manifest.json`. No model has read it. Scored once |

## Steps

1. **Line choice** (`choose_line.py`).
   - Lines 0.20 to 0.50 in steps of 0.05.
   - A line qualifies if the out-of-fold file-2 accuracy, F1 and recall are at least 0.90, the
     FPR is at most 0.08, and every selection recording is at most 8% Attack.
   - Choose the qualifying line with the largest smallest margin over those five limits; on a
     tie, the higher line.
   - If no line qualifies, stop: no model is trained.
2. **Final model.** `python train.py research\dataset\processed\run13\training.csv
   --decision-line <line>`.
   - One candidate: no tuning and no second try.
   - If the process is interrupted, the same command is run again unchanged, as run 11 became
     run 12.
3. **Gate** (`run_gate.py`).
   - Each selection recording at most 8% Attack.
   - Artifact at most 50 MiB.
   - Deployment quality gate passed.
   - The artifact carries the chosen line and workflow v4.
   - The hold-out is reported, not gated. Its FPR is expected at about 11-15% (run 10: 2.7%).
4. **Fresh browsing** (`score_fresh.py`).
   - Two recordings: the generator sites, and `--sites unseen`.
   - Each needs at least 50 flows (fewer: recorded again, not counted) and at most 8% Attack by
     run 13. Model 60 is scored for comparison.
5. **Final test, once** (`final_eval_run13.py`).
   - `scripts/evaluate_model.py` with run 13's training file as `--train-csv`; run 13 and model 60
     on the same rows.
   - Targets: accuracy, F1 and recall at least 0.90; FPR at most 0.08; ROC-AUC at least 0.95.
   - Also reported: recall per attack category, Brier score, ECE (10 bins) and score bands.
6. **Ship rule.** Run 13 ships only if steps 3 and 4 passed and all five targets are met in
   step 5. Otherwise:
   - commits 9bd15a5 and cb13f3f are reverted;
   - nothing is released, and model 60 stays;
   - DEF-24 stays a known limit, with this result recorded.

## Limits stated in advance

- **Same-file test.** The final test comes from file 2, which run 13 trains on. On these rows,
  model 60's figure is a cross-file test, and neither is comparable with model 60's 108,700-row
  figure.
- **File-1 false alarms rise** (hold-out FPR about 11-15% expected).
- **Fuzzers recall** is expected at about 70%, not 90%.
- **Live evidence is benign traffic on one PC.** The selection recordings also chose the line, so
  only the fresh recordings and TC-24 are independent of it.
- **Fold models see more file-1 rows** than the final model: `train.py` keeps 25% of rows back as
  its check split.
