from pathlib import Path

import pandas as pd
import pytest
from pandas.core.util.hashing import hash_pandas_object

DATASET_FOLDER = Path(__file__).resolve().parents[1] / "datasets"
EXPECTED_COLUMNS = [
    "dur",
    "proto",
    "service",
    "state",
    "spkts",
    "dpkts",
    "sbytes",
    "dbytes",
    "rate",
    "sttl",
    "dttl",
    "sload",
    "dload",
    "sinpkt",
    "dinpkt",
    "label",
]


@pytest.mark.parametrize(
    ("filename", "rows", "normal_count", "attack_count"),
    [
        ("algoguard_big.csv", 5_000, 1_597, 3_403),
        ("algoguard_bigger.csv", 10_000, 3_194, 6_806),
        ("algoguard_biggest.csv", 20_000, 6_388, 13_612),
    ],
)
def test_included_dataset_is_ready_for_replay_and_retraining(
    filename,
    rows,
    normal_count,
    attack_count,
):
    dataframe = pd.read_csv(DATASET_FOLDER / filename)
    assert len(dataframe) == rows
    assert dataframe.columns.tolist() == EXPECTED_COLUMNS
    assert "id" not in dataframe.columns
    assert "attack_cat" not in dataframe.columns
    assert dataframe["label"].value_counts().to_dict() == {
        "Attack": attack_count,
        "Normal": normal_count,
    }


def test_included_datasets_are_nested_for_size_comparison():
    frames = {
        name: pd.read_csv(DATASET_FOLDER / name)
        for name in (
            "algoguard_big.csv",
            "algoguard_bigger.csv",
            "algoguard_biggest.csv",
        )
    }
    row_hashes = {
        name: set(hash_pandas_object(frame, index=False)) for name, frame in frames.items()
    }
    assert row_hashes["algoguard_big.csv"] <= row_hashes["algoguard_bigger.csv"]
    assert row_hashes["algoguard_bigger.csv"] <= row_hashes["algoguard_biggest.csv"]
