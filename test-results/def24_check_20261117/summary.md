# DEF-24 check, 2026-11-17 (throwaway scripts kept as evidence)

Question: can DEF-24 be fixed, and how? Model 60 catches 76% of attacks on unseen UNSW-NB15
data, and 80% of the misses are Fuzzers of the official training partition ("file 2"). No script
reads `research/dataset/processed/run11/final_test.csv` (the 32,610 reserved rows).

Data:
- **file 1:** the repository file (official testing partition) minus the hold-out, 66,393 rows;
- **HO:** the hold-out, 15,939 rows;
- **S2:** the 76,089 file-2 rows that run 12 trained on, found by matching run 11's training rows
  back to the official file by the 13-feature key;
- **recordings:** PC A's benign captures (`research/dataset/processed/def19/*.pcap`).

## 1. More measurements, model trained on file 1 only (`spike_e1.py`, Random Forest)

| Features | S2 accuracy / F1 / recall / FPR | Fuzzers caught |
|---|---|---|
| the 16 of schema v2 | 86.4% / 85.6% / 76.5% / 2.4% | 24.1% |
| + smean, dmean | 87.0% / 86.4% / 77.6% / 2.4% | 27.2% |
| + tcprtt, synack, ackdat | 86.6% / 85.8% / 76.3% / 1.8% | 23.8% |
| + every unused non-TTL column | 86.8% / 86.1% / 76.9% / 2.0% | 25.0% |
| + TTLs (excluded on purpose; reference) | 86.9% / 86.2% / 76.9% / 1.6% | 24.7% |

No measurement helps a model that never saw file 2's Fuzzers.

## 2. Both files in training (`spike_e2.py`, Random Forest, 3 grouped folds of S2)

- `smean`/`dmean` equal bytes // packets on 100% of rows, so they add nothing new.
- Handshake timing is a capture-position artifact. Median `ackdat`: dataset normal 0.15 ms,
  dataset attacks 58.9 ms, PC A's own browsing 0.09 ms, because the PC answers itself. It lowers
  browsing false alarms for the wrong reason, so it was not used.
- With the 16 features: S2 accuracy 91.2%, F1 91.3%, recall 87.0%, FPR 4.0%, Fuzzers 57.7%;
  hold-out FPR 7.9%; worst browsing recording 8.8%.

## 3. Lower lines and weights (`spike_e3.py`, 3 grouped folds; `e3_results.json`)

- 0.05% of file-2 Fuzzers are exact copies of a Normal row, so they are separable.
- Histogram Gradient Boosting with PC A's flows weighted 10x passes every target on S2 and keeps
  every recording at most 8% at lines 0.30 and 0.35. At 0.35: 92.4% / 92.7% / 90.4% / 5.4%,
  Fuzzers 68.8%, hold-out FPR 10.5%, worst recording 6.8%.

## 4. The app's own Stacking Ensemble (`spike_e4.py`, one split; `e4_sweep.txt`)

PC A's flows were repeated 10x, because Stacking takes no sample weights.

| Model | Lines passing every target + recordings <= 8% | At 0.35: S2 recall / FPR, worst recording, hold-out FPR |
|---|---|---|
| Stacking as in 0.6.0 | 0.35 only | 90.8% / 6.9%, 7.9%, 13.0% |
| Stacking with the histogram Gradient Boosting member | 0.25 to 0.35 | 90.8% / 5.6%, 5.7%, 11.5% |

Caveat: `keys()` uses Python's `hash()`, which differs between processes, so each part-4 process
held out a different random third of S2. Differences under a point are noise.

Outcome: option 1 of the design (`docs/superpowers/specs/2026-11-17-def24-run13-design.md`).
