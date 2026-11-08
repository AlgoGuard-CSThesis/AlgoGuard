# Stage 6A — Research validation and attack scores

Status: implemented and verified locally on 2026-10-25. The research release gate is **unmet**: live-capture
feature fidelity is only partly supported and the model does not transfer to live traffic (DEF-19). Iteration 6
therefore continues as a **limited research pilot**, as the Iteration 6 plan provides. The cloud migration for 6A.3
is written and tested on the local stack; a maintainer applies it to the hosted pilot (see Open items).

## Outcome

| Sub-stage | Result |
|---|---|
| 6A.1 Feature fidelity and latency | 8 of 16 features supported against Argus; flow boundaries keep the other 8 below 90%. Five tracker fixes bring live features to UNSW-NB15's definitions. Latency reported per measure with load and sample size; live lag is above the 500 ms budget today. |
| 6A.2 Generalization and calibration | Untouched final set (108,739 rows): accuracy 86.0%, F1 85.2%, recall 75.9%, FPR 2.5%, ROC-AUC 0.969. Calibrated on the hold-out (ECE 0.015, no calibrator added), not on the untouched set (ECE 0.112). |
| 6A.3 Attack probability and score bands | Stored with every new prediction, locally and in the cloud, under policy `attack-score-bands-v1`; older rows stay unscored; older clients still upload. |
| 6A.4 Presentation and documentation | Feed, alerts, reports, dashboard and manual prediction show the same band in text and colour; no band is Critical; legacy severity is shown as legacy. README explains the limits. |

## 6A.1 — Packet-feature fidelity and capture latency

Protocol `test-results/iter6_6a/protocol_6a1.md` (committed 7498b48 before the comparison); results
`test-results/iter6_6a/results_6a1.md`; code changes in e209d60.

- **Reference flows and predeclared tolerances:** one 120 s Wi-Fi capture of generated web traffic, DNS and pings
  (4,223 packets) against Argus 3.0.8.2 / ra 3.0.8.2 (`test-results/iter6_6a/argus/Dockerfile`). The tolerances
  cover direction, units, protocol, service, state and the connection counts. Flow closure is reported separately.
  The backlog's "fifteen features" are the sixteen of schema v2.
- **Comparison, exclusions, provenance:** all 108 Argus IPv4 flows matched. IPv6 flows are excluded (AlgoGuard
  reads IPv4), and `service` is checked against the port convention because Bro/Zeek is not part of the reference.
  Each fix was confirmed against the dataset itself (`UNSW_NB15_training-set.csv`, 175,341 rows):
  load leaves out the first packet (99.999% of rows), RST outranks FIN, pings are ECO, a reset trailing a closed
  flow is not a new flow, and a TCP packet larger than the MTU counts as its wire segments. Bytes stay IP-layer
  (15.2% of the dataset's TCP rows are below the Ethernet floor).
- **Supported (≥ 90% agreement):** proto, service, state, spkts, sbytes, sinpkt, rate, ct_srv_dst.
  **Unsupported:** dur 88.9%, dload 87.0%, ct_dst_src_ltm 87.0%, ct_dst_ltm 87.0%, dpkts 81.5%, dinpkt 71.3%,
  dbytes 69.4%, sload 8.3% (89.8% once Argus's Ethernet bytes are removed; diagnostic only). The causes are
  flow boundaries: AlgoGuard closes a flow at the first reset and after 15 s of silence, while Argus keeps counting.
  Changing either would delay verdicts.
- **Latency:** inference 75.3 ms per call for 1 flow and 109.2 ms for 64 (p50, 200 calls each, no load). Last
  packet to verdict for FIN/RST flows: p50 477.2 / p95 776.0 ms (n = 102, 50 packets/s) and p50 273.3 / p95
  606.1 ms (n = 76, 261 packets/s). Expiry to verdict for idle flows: p50 598.0 / p95 1,140.4 ms after the 15 s
  timeout (n = 256). Upload: p50 0.80–0.91 s (c775d13). Both live runs miss the 500 ms TC-11 budget; the tracker
  changes cost 1 µs per packet, so the cause is load and contention. Carried to 6C.4.
- **Gate:** the unsupported inputs leave the research release gate unmet; it is recorded as unmet, not as a success.

## 6A.2 — Generalization and probability calibration

Protocol `test-results/iter6_6a/protocol_6a2.md` (a42ff4b, before scoring); results in
`test-results/iter6_6a/evaluation_final/`, `calibration_6a2.json`, `final_eval_supplement.json` (8c3921a).

- **Data roles:** training = official UNSW-NB15 *testing* partition minus the hold-out (66,393 rows);
  calibration/selection = the cycle 1 hold-out (15,939); final = the official *training* partition minus every row
  whose features match a training or hold-out row (108,739 of 175,341). The repository's
  `research/dataset/raw/UNSW_NB15_training-set.csv` is byte-identical to the official testing file, so the official
  training partition had never been used. The bundled replay samples are drawn from training rows and enter no
  accuracy claim.
- **Results (n = 108,739):** accuracy 0.8602, precision 0.9720, recall 0.7589, F1 0.8524, FPR 0.0248, ROC-AUC
  0.9685, Brier 0.117, ECE 0.112. Accuracy, recall and F1 miss the 0.90 targets (reported, not re-targeted).
  Fuzzers recall 0.219 (n = 14,398); every other category ≥ 0.53. Score bands on the final set: Low 58.5% of rows
  (21.9% of them attacks), Medium 1.9% (75.1%), High 2.9% (85.9%), Very high 36.8% (99.2%).
- **Calibration:** ECE on the calibration set 0.0145 ≤ 0.05, so no calibrator was fitted (protocol rule 2) and no
  new artifact was produced. The untouched set shows the scores are not calibrated beyond the training distribution.
- **Candidates and policy:** the six candidates, the individual-model ranking and the Stacking-only deployment
  policy are unchanged; no quality-gate change is proposed.

## 6A.3 — Attack probability and versioned score bands

- **Extraction:** `services/alert_policy.attack_probability` reads the Attack class's probability by class value,
  not column position. The manual path (`simulation_service.predict_with_artifact`) and both monitors
  (`live_monitor_service.score_rows`, `cloud_monitor`) store it next to the verdict confidence, which stays separate.
- **One band function:** `attack_score_band` maps Low < 0.50 ≤ Medium < 0.70 ≤ High < 0.90 ≤ Very high;
  `attack_score_fields` stores `attack_probability` (rounded to 6 places), `attack_score_band` (taken from the
  stored value) and `score_band_policy = attack-score-bands-v1`.
- **Migrations:** SQLite migration 5 (`attack_score_bands`) and
  `supabase/migrations/20261025010000_attack_score_bands.sql` add the three nullable columns. Old predictions keep
  `NULL`; `severity_level` is still written as the legacy label. The cloud check constraint accepts either no score
  or a v1 band that matches its probability, so even a direct table insert cannot store a mismatched band.
  `normalize_flow` and `store_flow_batch` accept the three optional keys; an event without them (an older client)
  commits unscored. In the other direction, a new client checks for the columns before adding scores, so it never
  sends a database without the migration fields it would refuse. `alert_detail` gains the three columns at the end, listed explicitly because the alert table
  gained `legacy_unresolved` after the view was created.
- **Tests:** band boundaries, null, range (including NaN and infinity), class order [1, 0], a confident Normal
  scoring Low, rounding at a boundary, policy-version pinning to the migration text, an older SQLite database
  gaining the columns without scores (`tests/test_attack_scores.py`, `tests/test_live_monitor.py`). The cloud side
  is covered in `tests/test_cloud_app.py` and, against the local stack, in `tests/integration/test_atomic_flows.py`:
  scored events are stored, older events stay unscored, seven mismatched or out-of-range scores are refused, and a
  direct insert with a mismatched band is refused.
- **Policy changes:** a new threshold needs a new policy version in `alert_policy.py` and a migration that accepts
  it; `test_the_thresholds_are_pinned_to_their_policy_version` fails otherwise.

## 6A.4 — Consistent presentation and documentation

- `templates/_attack_score.html` renders a band badge ("Very high · 97.0%") for scored rows and a neutral
  **Not scored** badge with "Legacy severity: …" for older ones. Alerts, report detail, the dashboard and manual
  prediction use it. The monitor feed builds the same badge from the band sent with each event, so session-only rows
  need no database round trip. The badge text always names the band, so colour is never the only signal.
- Each page with scores explains them: a probability that the flow is an attack, not impact, and not calibrated
  on unfamiliar traffic. No band is named Critical.
- README: new section "Accuracy, Scores and Their Limits" (replay overlap, untouched results, the file mislabel,
  calibration, live fidelity and DEF-19, latency). The Live Monitor, Manual Prediction, Alerts and Database
  Migration sections are updated too.

## Verification (2026-10-25)

| Check | Result |
|---|---|
| Unit suite `pytest -q` | 615 passed |
| Integration lane `pytest -m integration` on the local stack with `RUN_WINDOWS_CAPTURE_TESTS=1` | 76 passed (the local edge-function container had to be restarted first) |
| `ruff check .`, `npm run typecheck` | clean |
| Corrected migration applied from the pre-migration state inside a rolled-back transaction | applies; strict check present |
| Browser check (headless Chrome on a throwaway app over a copy of the local database, temporary account) | manual Attack "Very high · 100.0%", Normal "Low · 0.5%"; Alerts: 3 scored, 247 older alerts "Not scored" with legacy severity, no Severity column, no "Critical"; dashboard names the band; monitor feed 50 session-only rows all scored, every Normal row Low or Medium |

## Open items

1. **Hosted migration (maintainer):** apply `20261025010000_attack_score_bands.sql` to the pilot with
   `python cloud_migrate.py --status`, then `python cloud_migrate.py --yes`. Until then the hosted database would
   refuse score fields, and the sync treats a refusal as permanent. So this version first asks whether the
   database has the score columns (`CloudRepository.attack_scores_supported`, cached per project and re-checked
   every 5 minutes while unconfirmed). Where they are missing it uploads unscored events, which the old database
   accepts. The analyst still sees the score on screen. Older clients are unaffected either way.
2. **DEF-19** stays open: with fidelity fixed, 73.0% of benign captured flows are still labelled Attack. It is a
   dataset gap; re-test in cycle 2.
3. **Live lag under load** (p95 606–776 ms on 2026-10-25 against the 500 ms budget): investigate in 6C.4 and re-run
   TC-11 in cycle 2.
4. PC B: `git pull` before its next session.

## Evidence files

`test-results/iter6_6a/`: protocols `protocol_6a1.md`, `protocol_6a2.md`; results `results_6a1.md`,
`fidelity_6a1*.json`, `benign_attack_share_6a1.json`, `latency_6a1*.json`, `calibration_6a2.json`,
`final_eval_supplement.json`, `evaluation_final/`; scripts `capture_fidelity.py`, `compare_fidelity.py`,
`wire_equivalent.py`, `benign_attack_share.py`, `latency_6a1.py`, `calibration_6a2.py`,
`final_eval_supplement.py`. Captures and datasets stay outside git under `research/dataset/processed/`.

## Addendum, 2026-10-26: the open findings fixed

The sections above record Stage 6A as finished on 2026-10-25 and stay as written. The next day the open findings
were worked on, each under a protocol committed before its result was seen:

| Finding | Fix | Result |
|---|---|---|
| Live lag p95 606–776 ms under load (DEF-17 budget 500 ms) | Live sessions score in a separate process (`services/inference_worker.py`, 5b5a36a) and capture and decode packets in another (`services/capture_worker.py`); flows are tracked on their own thread | Outside the app: p95 332.4 ms (`latency_def17_fix.json`). In the app, two 90 s runs: FIN/RST lag p50 89.1 and 91.8 ms, p95 113.7 and 129.2 ms, n = 59 and 82 (`tc11_inapp_def17_fix.json`) |
| 8 of 16 features unsupported (flow boundaries) | A closed TCP flow is held 50 ms for packets in flight; the wire-equivalent reference gives every packet its own timestamp and drops 802.1Q tags, as a tap would (cffb952) | On a fresh capture, predeclared in `protocol_6a1_confirmation.md`: 15 of 16 supported, all 102 flows matched; `sload` agrees on 99.0% only with the Ethernet-header correction (`results_6a1_confirmation.md`) |
| DEF-19: 73% of benign flows labelled Attack | Part 1 (6cb88e6): HTTPS reports `-`, because UNSW-NB15's `ssl` rows are all attacks. Part 2: run 10 = run 8's rows plus 2,460 benign PC flows, deployed with `train.py --deploy-run 10` | Part 1 alone: 10.0% and 26.6% (`results_def19.md`). Run 10 on captures recorded after training: 3.2% (familiar sites), 6.6% (unseen sites); untouched dataset F1 0.8538, recall 0.7602 (`results_def19_part2.md`) |

The research release gate stays **unmet**: accuracy, F1 and recall on the untouched set miss the 90% targets, `sload`
needs the link-layer correction, attacks on live traffic are untested, and the benign evidence comes from one PC.
Iteration 6 continues as a limited research pilot, now with live features that match the dataset's definitions and a
false-alarm rate on this PC within target.

Open items now: apply the 6A.3 migration to the hosted pilot (`python cloud_migrate.py --yes`); publish local model
60 (run 10) to the pilot with `publish_model.py`; re-test DEF-17 and DEF-19 formally in test cycle 2; PC B
`git pull`.
