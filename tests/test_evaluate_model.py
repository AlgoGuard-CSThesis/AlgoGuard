"""scripts/evaluate_model.py removes every evaluation row a training file already contains."""
import pandas as pd
import pytest

from scripts import evaluate_model

PER_FLOW = {"dur": 0.5, "proto": "tcp", "service": "-", "state": "FIN", "spkts": 4, "dpkts": 2,
            "sbytes": 300, "dbytes": 120, "rate": 10.0, "sload": 1.0, "dload": 2.0,
            "sinpkt": 0.1, "dinpkt": 0.2}


def evaluation_rows():
    first = {**PER_FLOW, "sttl": 254, "dttl": 252, "label": 1}
    second = {**PER_FLOW, "sbytes": 999, "sttl": 31, "dttl": 29, "label": 0}
    return pd.DataFrame([first, second])


def test_schema_v2_training_file_is_compared_on_its_per_flow_features():
    # Feature schema v2 files have no sttl or dttl; the first row is the same flow and must go.
    schema_v2 = pd.DataFrame([{**PER_FLOW, "ct_dst_src_ltm": 1, "ct_dst_ltm": 1, "ct_srv_dst": 1,
                               "label": 1}])
    seen = evaluate_model.seen_in_training(evaluation_rows(), schema_v2, "training.csv")
    assert seen.tolist() == [True, False]


def test_full_unsw_training_file_still_uses_all_fifteen_features():
    other_ttl = pd.DataFrame([{**PER_FLOW, "sttl": 62, "dttl": 252, "label": 1}])
    seen = evaluate_model.seen_in_training(evaluation_rows(), other_ttl, "raw.csv")
    assert seen.tolist() == [False, False]


def test_training_file_without_the_flow_features_is_refused_not_skipped():
    with pytest.raises(SystemExit, match="sbytes"):
        evaluate_model.seen_in_training(evaluation_rows(), pd.DataFrame([{"label": 1}]), "x.csv")
