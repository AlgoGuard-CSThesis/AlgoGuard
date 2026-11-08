# Stage 6A.1 confirmation — fresh capture with the pipeline frozen (written before the capture)

Written 2026-10-26, before the confirmation capture was recorded.

## Why

After the first comparison (`results_6a1.md`), two kinds of change were made while looking at its
results:

- **Tracker** (`services/flow_tracker_service.py`): a closed TCP flow is held 50 ms so packets already
  in flight are counted, as Argus counts them.
- **Reference** (`wire_equivalent.py`): the wire-equivalent copy now gives every packet a distinct
  timestamp, as a tap does, and removes 802.1Q tags, which are link-layer bytes UNSW-NB15 does not
  count.

Each change has a reason independent of the scores, but both were chosen on the same capture they
improved. This run checks them on a capture none of them has seen.

## Method (unchanged from `protocol_6a1.md` except as listed)

1. Record a new capture with `capture_fidelity.py --seconds 120` (same sites, new traffic).
2. Build the wire-equivalent copy with `wire_equivalent.py` and run Argus 3.0.8.2 exactly as in
   `results_6a1.md` (fresh output files).
3. Run `compare_fidelity.py` against Argus on the recorded capture and on the wire-equivalent copy, with
   the code of the commit that adds this file. Nothing in the tracker, the reference construction or
   the comparison is changed after the capture is recorded.

## Tolerances and verdict

The tolerances of `protocol_6a1.md` apply unchanged; a feature is supported when at least 90% of matched
flows agree. The verdict uses the wire-equivalent reference. The load diagnostic with Argus's bytes taken
at the IP layer is reported beside the official result and does not replace it. Whatever the result,
it is reported; a feature that fails here is unsupported even if it passed on the first capture.
