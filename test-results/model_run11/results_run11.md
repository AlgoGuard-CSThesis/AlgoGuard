# Training run 11 — results: the gate failed, model 60 stays

Plan: `protocol_run11.md` (committed in `ab7dff9` before any run 11 data was built). Every number below is
in a file in this folder.

## What ran

| When (Asia/Manila) | Step | Evidence |
|---|---|---|
| 2026-11-10 18:19 | Data built: 144,942 training rows (run 10's 68,853 plus 76,089 of the official training partition), 32,610 final test rows; no key group on both sides | `run11_data_manifest.json`, `build_run11_data.py` |
| 2026-11-10 18:20 | Training run #11 started; the five base models finished by 18:31; the process ended with the Claude Code session during the Stacking step, so no ensemble was saved (run #11 marked failed in the training database) | `train_console.log` |
| 2026-11-10 | Pre-check, recorded only: grouped 5-fold Random Forest on the training rows, recall 0.929, false-positive rate 0.066, Fuzzers recall 0.691 | `precheck_run11.json` |
| 2026-11-11 00:43–01:16 | The same command, data and seeds again as **training run #12**, the candidate; in-app check split (optimistic, see the QA report): Stacking accuracy 94.1%, F1 94.6%, ROC-AUC 0.989; `stacking.joblib` written 01:16:31 (the log's last line repeats the start time because `cmd` expanded `%TIME%` when it read the command) | `train_console_run12.log` |
| 2026-11-11 01:30 | Gate on the selection data | `gate_run11.json`, `gate_holdout_*/` |

## Gate — failed (2 of 3 rules)

**Rule 1, hold-out (15,936 rows; 3 removed as matching a training row, the same for both models): failed.**

| Measure | Run 10 (model 60) | Run 12 | Floor |
|---|---|---|---|
| F1 | 0.9355 | **0.9103** | ≥ 0.9255 — not met |
| Recall | 0.9159 | 0.9601 | ≥ 0.9059 — met |
| Precision | 0.9559 | 0.8654 | — |
| False-positive rate | 0.0267 | 0.0944 | — |
| Accuracy | 0.9511 | 0.9267 | — |
| ROC-AUC | 0.9889 | 0.9840 | — |

**Rule 2, benign captures (at most 8% labelled Attack each): failed.**

| Capture | Flows | Run 10 | Run 12 |
|---|---|---|---|
| `generator.pcap` | 110 | 0 (0.0%) | 1 (0.9%) |
| `browser.pcap` | 542 | 18 (3.3%) | 25 (4.6%) |
| `final_browser.pcap` | 502 | 16 (3.2%) | 20 (4.0%) |
| `final_unseen.pcap` (sites no training capture used) | 687 | 45 (6.6%) | **74 (10.8%)** |

**Rule 3, artifact: passed.** 30,129,529 bytes (28.7 MiB, under the 50 MiB storage limit); the deployment
quality gate passed.

## Decision

As the protocol fixed in advance: run 12 is not evaluated further and not deployed. **Model 60 (training
run 10) stays the deployed and published model.** The final test (`research/dataset/processed/run11/
final_test.csv`, SHA-256 `6f7b4188…b7da`) was not read by any model, so it remains an untouched set for a
future candidate under a new protocol.

## What it shows

Adding the official training partition did what it was meant to for attacks: recall on the hold-out rose
from 0.916 to 0.960, and the pre-check's Random Forest caught 69% of Fuzzers in grouped cross-validation
(a different test from run 10's 23% on the untouched set, so not a like-for-like comparison). It
also made the model label more normal traffic as an attack: the hold-out false-positive rate rose from 2.7%
to 9.4%, and on live browsing to sites it had never seen, from 6.6% to 10.8%. For AlgoGuard that trade is
worse, because false alarms on ordinary traffic are what an analyst sees first (DEF-19). The two official
UNSW-NB15 files are not drawn from one distribution, so more rows from the second file move the decision
boundary rather than simply sharpening it.

The 90% targets on untouched data therefore stay unmet for the deployed model (accuracy 86.2%, F1 85.4%,
recall 76.0%, Stage 6A.2), and AlgoGuard stays a limited research pilot. A further attempt needs a new
protocol, for example choosing the decision threshold on the hold-out to keep false alarms at run 10's level
and checking the recall that remains, with the unread final test above as its untouched set.
