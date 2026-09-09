import pandas as pd
import pytest

from services.preprocessing_service import prepare_dataset


@pytest.mark.parametrize(
    ("normal", "attack"),
    [("Normal", "Attack"), (" BENIGN ", "malicious"), ("clean", "DoS"), (0, 1), (0.0, 1.0)],
)
def test_known_normal_labels_keep_attack_as_positive(tmp_path, normal, attack):
    csv_path = tmp_path / "labels.csv"
    pd.DataFrame(
        {"value": range(8), "label": [normal] * 4 + [attack] * 4}
    ).to_csv(csv_path, index=False)

    prepared = prepare_dataset(csv_path)

    assert prepared.label_mapping["normal_label"] == str(normal).strip()
    assert prepared.label_mapping["attack_label"] == str(attack).strip()
    for features, labels in (
        (prepared.X_train, prepared.y_train), (prepared.X_test, prepared.y_test)
    ):
        assert labels.tolist() == (features["value"] >= 4).astype(int).tolist()


@pytest.mark.parametrize(
    "labels", [("attack", "safe"), ("class_a", "class_b"), ("Normal", "Benign")]
)
def test_ambiguous_normal_labels_are_rejected(tmp_path, labels):
    csv_path = tmp_path / "ambiguous.csv"
    pd.DataFrame(
        {"value": range(8), "label": [labels[0]] * 4 + [labels[1]] * 4}
    ).to_csv(csv_path, index=False)

    with pytest.raises(ValueError, match="Rename the target values to Normal and Attack"):
        prepare_dataset(csv_path)


@pytest.mark.parametrize("label", ["Normal", "Attack"])
def test_training_still_requires_both_classes(tmp_path, label):
    csv_path = tmp_path / "single_class.csv"
    pd.DataFrame({"value": range(8), "label": [label] * 8}).to_csv(csv_path, index=False)

    with pytest.raises(ValueError, match="exactly two target classes"):
        prepare_dataset(csv_path)


@pytest.mark.parametrize(
    ("header", "message"),
    [
        ("value,value,label", "duplicate column names"),
        ("value,label,label", "duplicate column names"),
        ("value,other,", "final target column must have a name"),
    ],
)
def test_invalid_csv_headers_are_rejected_before_pandas_renames_them(tmp_path, header, message):
    csv_path = tmp_path / "invalid_header.csv"
    rows = [f"{index},{index},{'Normal' if index < 4 else 'Attack'}" for index in range(8)]
    csv_path.write_text(header + "\n" + "\n".join(rows), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        prepare_dataset(csv_path)


def test_csv_header_validation_preserves_bom_quoted_names_and_blank_lines(tmp_path):
    csv_path = tmp_path / "quoted_header.csv"
    rows = [f"{index},{'Normal' if index < 4 else 'Attack'}" for index in range(8)]
    csv_path.write_text('\n  \n"value,quoted",label\n' + "\n".join(rows), encoding="utf-8-sig")

    prepared = prepare_dataset(csv_path)

    assert prepared.feature_columns == ["value,quoted"]
    assert prepared.target_column == "label"
