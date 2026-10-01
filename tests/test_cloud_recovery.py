import pytest

from cloud_recovery import RECOVERY_TABLES, ModelObjects, verify_recovery
from legacy_backup import TransferError, digest, write_json


@pytest.mark.parametrize("damage", ["records", "counts", "inventory", "path"])
def test_recovery_rejects_damaged_packages(tmp_path, damage):
    rows = {table: [] for table in RECOVERY_TABLES}
    records = tmp_path / "records.json"
    write_json(records, rows)
    summary = {
        "format": 1,
        "files": {"records.json": digest(records)},
        "counts": {table: 0 for table in RECOVERY_TABLES},
        "artifacts": [],
        "outboxes": [],
    }
    write_json(tmp_path / "recovery.json", summary)
    assert verify_recovery(tmp_path)[1] == rows
    if damage == "records":
        write_json(records, {})
    elif damage == "counts":
        summary["counts"]["public.profile"] = 1
    elif damage == "inventory":
        summary["files"] = {}
    else:
        summary["files"]["../outside"] = "0" * 64
    write_json(tmp_path / "recovery.json", summary)
    with pytest.raises(TransferError):
        verify_recovery(tmp_path)


def test_model_restore_does_not_overwrite_different_bytes(monkeypatch):
    objects = ModelObjects("http://127.0.0.1:54321", "test-only")
    calls = []

    def request(path, content=None):
        calls.append((path, content))
        return b"existing model"

    monkeypatch.setattr(objects, "_request", request)
    with pytest.raises(TransferError, match="not overwritten"):
        objects.ensure("models/model.joblib", b"different model")
    assert calls == [("models/model.joblib", None)]


@pytest.mark.parametrize("damage", ["missing", "changed"])
def test_recovery_requires_artifacts_matching_protected_manifests(tmp_path, damage):
    model = tmp_path / "model.joblib"
    model.write_bytes(b"published bytes")
    sha = digest(model)
    rows = {table: [] for table in RECOVERY_TABLES}
    rows["public.model_manifest"] = [
        {
            "object_path": "publication/model.joblib",
            "object_sha256": sha,
            "object_bytes": model.stat().st_size,
            "status": "active",
        }
    ]
    records = tmp_path / "records.json"
    write_json(records, rows)
    if damage == "changed":
        model.write_bytes(b"different bytes")
    summary = {
        "format": 1,
        "files": {"records.json": digest(records), "model.joblib": digest(model)},
        "counts": {table: len(values) for table, values in rows.items()},
        "outboxes": [],
        "artifacts": []
        if damage == "missing"
        else [
            {
                "object_path": "publication/model.joblib",
                "status": "copied",
                "file": "model.joblib",
                "sha256": sha,
            }
        ],
    }
    write_json(tmp_path / "recovery.json", summary)
    with pytest.raises(TransferError):
        verify_recovery(tmp_path)
