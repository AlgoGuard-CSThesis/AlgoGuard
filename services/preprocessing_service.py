import csv
from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

NORMAL_LABELS = {"0", "0.0", "normal", "benign", "legitimate", "clean"}


@dataclass
class PreparedDataset:
    """Leakage-safe binary dataset and metadata used by every candidate."""

    X_train: pd.DataFrame
    X_test: pd.DataFrame
    y_train: pd.Series
    y_test: pd.Series
    feature_columns: list
    target_column: str
    numeric_columns: list
    categorical_columns: list
    feature_defaults: dict
    label_mapping: dict
    summary: dict


def _make_one_hot_encoder():
    try:
        return OneHotEncoder(handle_unknown="ignore", sparse_output=False)
    except TypeError:
        return OneHotEncoder(handle_unknown="ignore", sparse=False)


def build_feature_preprocessor(numeric_columns, categorical_columns):
    """Build an unfitted transformer that is fitted only inside each pipeline."""
    transformers = []

    if numeric_columns:
        transformers.append(
            (
                "numeric",
                Pipeline(
                    steps=[
                        ("imputer", SimpleImputer(strategy="median")),
                        ("scaler", StandardScaler()),
                    ]
                ),
                numeric_columns,
            )
        )

    if categorical_columns:
        transformers.append(
            (
                "categorical",
                Pipeline(
                    steps=[
                        ("imputer", SimpleImputer(strategy="most_frequent")),
                        ("encoder", _make_one_hot_encoder()),
                    ]
                ),
                categorical_columns,
            )
        )

    if not transformers:
        raise ValueError("The dataset does not contain usable feature columns.")

    return ColumnTransformer(transformers=transformers, remainder="drop")


def _read_and_validate_csv(file_path):
    try:
        with open(file_path, encoding="utf-8-sig", newline="") as csv_file:
            # Pandas silently renames duplicate and unnamed headers. Retain the
            # original names so those validation errors cannot be hidden.
            header = next(
                (row for row in csv.reader(csv_file) if row and (len(row) > 1 or row[0].strip())),
                [],
            )
            csv_file.seek(0)
            dataframe = pd.read_csv(csv_file)
    except Exception as error:
        raise ValueError(f"Unable to read the CSV file. Details: {error}") from error

    dataframe = dataframe.dropna(how="all")
    if dataframe.empty:
        raise ValueError("The uploaded CSV is empty.")
    if dataframe.shape[1] < 2:
        raise ValueError(
            "The dataset must contain at least one feature column and one target column."
        )
    if len(header) != len(set(header)):
        raise ValueError("The CSV contains duplicate column names.")

    target_column = dataframe.columns[-1]
    if not header[-1].strip():
        raise ValueError("The final target column must have a name.")

    usable_features = dataframe.iloc[:, :-1].dropna(axis=1, how="all")
    if usable_features.shape[1] == 0:
        raise ValueError("The dataset does not contain usable feature columns.")

    dataframe = pd.concat([usable_features, dataframe[[target_column]]], axis=1)
    return dataframe


def encode_binary_target(raw_target, *, allow_single_class=False):
    """Use the same class meaning for training and labelled traffic replay."""
    if raw_target.isna().any() or raw_target.astype(str).str.strip().eq("").any():
        raise ValueError("The target label column contains missing values.")

    cleaned = raw_target.astype(str).str.strip()
    labels = list(cleaned.value_counts().index)
    if allow_single_class and len(labels) == 1:
        label = labels[0]
        is_normal = label.lower() in NORMAL_LABELS
        if not is_normal and label.lower() not in {"1", "1.0", "attack"}:
            raise ValueError("Rename the target values to Normal and Attack (or 0 and 1).")
        return cleaned.map({label: 0 if is_normal else 1}).astype(int), {
            "normal_label": label if is_normal else None,
            "attack_label": None if is_normal else label,
            "output_labels": {"0": "Normal", "1": "Attack"},
        }
    if len(labels) != 2:
        raise ValueError("AlgoGuard requires exactly two target classes: Normal and Attack.")

    normal_labels = [label for label in labels if label.lower() in NORMAL_LABELS]
    if len(normal_labels) != 1:
        raise ValueError(
            "Cannot identify exactly one Normal class. Rename the target values "
            "to Normal and Attack (or 0 and 1) before training."
        )
    normal_label = normal_labels[0]
    attack_label = next(label for label in labels if label != normal_label)
    encoded = cleaned.map({normal_label: 0, attack_label: 1}).astype(int)
    return encoded, {
        "normal_label": normal_label,
        "attack_label": attack_label,
        "output_labels": {"0": "Normal", "1": "Attack"},
    }


def _training_defaults(X_train, numeric_columns, categorical_columns):
    defaults = {}
    for column in numeric_columns:
        median = X_train[column].replace([np.inf, -np.inf], np.nan).median()
        defaults[column] = None if pd.isna(median) else float(median)
    for column in categorical_columns:
        mode = X_train[column].dropna().astype(str).mode()
        defaults[column] = mode.iloc[0] if not mode.empty else ""
    return defaults


def prepare_dataset(file_path, test_size=0.25, random_state=42):
    """Validate one CSV, split first, and return unfitted preprocessing metadata."""
    dataframe = _read_and_validate_csv(file_path)
    target_column = dataframe.columns[-1]
    X = dataframe.iloc[:, :-1].copy().replace([np.inf, -np.inf], np.nan)
    y, label_mapping = encode_binary_target(dataframe[target_column])

    class_counts = y.value_counts().sort_index()
    if class_counts.min() < 3:
        raise ValueError(
            "Each class needs at least three rows for stratified training and testing."
        )

    test_rows = max(int(np.ceil(len(dataframe) * test_size)), 2)
    max_test_rows = len(dataframe) - 4
    if test_rows > max_test_rows:
        raise ValueError(
            "The dataset is too small to preserve both classes in training and testing."
        )

    try:
        X_train, X_test, y_train, y_test = train_test_split(
            X,
            y,
            test_size=test_rows,
            random_state=random_state,
            stratify=y,
        )
    except ValueError as error:
        raise ValueError(f"Stratified train/test split failed: {error}") from error

    if y_train.value_counts().min() < 2:
        raise ValueError("The training split needs at least two rows per class for stacking.")

    numeric_columns = X.select_dtypes(include=np.number).columns.tolist()
    categorical_columns = [column for column in X.columns if column not in numeric_columns]
    defaults = _training_defaults(X_train, numeric_columns, categorical_columns)

    raw_counts = dataframe[target_column].astype(str).str.strip().value_counts()
    summary = {
        "rows": int(len(dataframe)),
        "features": int(X.shape[1]),
        "target_column": str(target_column),
        "class_count": 2,
        "label_counts": {str(label): int(count) for label, count in raw_counts.items()},
        "normal_count": int((y == 0).sum()),
        "anomaly_count": int((y == 1).sum()),
        "train_rows": int(len(X_train)),
        "test_rows": int(len(X_test)),
        "numeric_features": len(numeric_columns),
        "categorical_features": len(categorical_columns),
    }

    return PreparedDataset(
        X_train=X_train,
        X_test=X_test,
        y_train=y_train,
        y_test=y_test,
        feature_columns=X.columns.tolist(),
        target_column=str(target_column),
        numeric_columns=numeric_columns,
        categorical_columns=categorical_columns,
        feature_defaults=defaults,
        label_mapping=label_mapping,
        summary=summary,
    )
