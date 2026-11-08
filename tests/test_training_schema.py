"""Training uses the 16 flow features when a CSV carries them, never label-derived columns."""
from pathlib import Path

import pandas as pd

from services.flow_tracker_service import FLOW_FEATURE_COLUMNS
from services.preprocessing_service import prepare_dataset

BUNDLED = Path(__file__).resolve().parents[1] / "datasets" / "algoguard_big.csv"


def sample_rows(count=240):
    frame = pd.read_csv(BUNDLED)
    normal = frame[frame["label"] == "Normal"].head(count // 2)
    attack = frame[frame["label"] == "Attack"].head(count // 2)
    return pd.concat([normal, attack]).reset_index(drop=True)


def test_raw_unsw_style_columns_are_reduced_to_the_flow_schema(tmp_path):
    frame = sample_rows()
    raw = frame.drop(columns=["label"])
    raw.insert(0, "id", range(1, len(raw) + 1))
    raw["sloss"] = 0
    raw["attack_cat"] = frame["label"].map({"Normal": "Normal", "Attack": "Exploits"})
    raw["label"] = frame["label"].map({"Normal": 0, "Attack": 1})
    path = tmp_path / "raw_unsw_like.csv"
    raw.to_csv(path, index=False)

    prepared = prepare_dataset(path)

    assert sorted(prepared.feature_columns) == sorted(FLOW_FEATURE_COLUMNS)
    assert "attack_cat" not in prepared.feature_columns
    assert "id" not in prepared.feature_columns


def test_bundled_dataset_keeps_its_own_column_order(tmp_path):
    frame = sample_rows()
    path = tmp_path / "bundled_sample.csv"
    frame.to_csv(path, index=False)

    prepared = prepare_dataset(path)

    assert prepared.feature_columns == [c for c in frame.columns if c != "label"]


def test_id_and_attack_cat_are_never_features_even_without_the_full_flow_schema(tmp_path):
    """DEF-08: label-derived and row-id columns are dropped from any training file."""
    frame = sample_rows()[["dur", "proto", "sbytes", "dbytes", "label"]]
    frame.insert(0, "id", range(1, len(frame) + 1))
    attack_cat = ["Normal" if value == "Normal" else "Exploits" for value in frame["label"]]
    frame.insert(4, "attack_cat", attack_cat)
    path = tmp_path / "partial_with_label_columns.csv"
    frame.to_csv(path, index=False, encoding="utf-8-sig")  # byte-order mark, like the raw file

    prepared = prepare_dataset(path)

    assert prepared.feature_columns == ["dur", "proto", "sbytes", "dbytes"]
