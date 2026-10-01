import json
import sqlite3
from contextlib import closing
from uuid import uuid4

import pytest

from legacy_backup import TransferError, backup, read_only, restore_legacy, verify_backup
from tests.legacy_support import make_legacy


def test_restored_legacy_app_can_log_in_and_read_history(tmp_path, monkeypatch, client):
    from config import reset_config_cache

    database = make_legacy(tmp_path / "source", monkeypatch)
    folder = tmp_path / "backup"
    backup(database, folder, uuid4(), database.parent)
    restored = restore_legacy(folder, tmp_path / "restored")
    monkeypatch.setenv("ALGOGUARD_DATABASE_PATH", str(restored))
    reset_config_cache()
    client.get("/login")
    with client.session_transaction() as session:
        token = session["_csrf_token"]
    response = client.post(
        "/login",
        data={"username": "historical analyst", "password": "admin123", "_csrf_token": token},
    )
    assert response.status_code == 302
    for path in ("/", "/alerts", "/logs", "/admins"):
        response = client.get(path)
        assert response.status_code == 200
    assert b"historical analyst" in client.get("/admins").data
    assert b"High" in client.get("/alerts").data


def test_consistent_backup_and_pre_cutover_restore(tmp_path, monkeypatch):
    root = tmp_path / "source"
    database = make_legacy(root, monkeypatch)
    folder = tmp_path / "backup"
    result = backup(database, folder, uuid4(), root)
    assert result["counts"]["prediction"] == 2
    assert all(item["status"] == "copied" for item in result["artifacts"])
    with sqlite3.connect(database) as connection:
        connection.execute("update alert set alert_status='Reviewed'")
    restored = restore_legacy(folder, tmp_path / "restored")
    with closing(read_only(restored)) as connection:
        assert connection.execute("select alert_status from alert").fetchone()[0] == "New"
        path = connection.execute("select artifact_path from model_deployment").fetchone()[0]
        assert str(tmp_path / "restored") in path
        assert not list(connection.execute("pragma foreign_key_check"))
    assert verify_backup(folder)["counts"] == result["counts"]
    with pytest.raises(TransferError, match="new backup"):
        backup(database, folder, uuid4(), root)


@pytest.mark.parametrize(
    "damage", ["snapshot", "artifact", "traversal", "absolute", "inventory", "missing"]
)
def test_backup_integrity_refuses_damaged_inputs(tmp_path, monkeypatch, damage):
    database = make_legacy(tmp_path / "source", monkeypatch)
    folder = tmp_path / "backup"
    result = backup(database, folder, uuid4(), database.parent)
    if damage == "snapshot":
        with sqlite3.connect(folder / "legacy.sqlite3") as connection:
            connection.execute("delete from report_alert")
    elif damage == "artifact":
        (folder / result["artifacts"][0]["file"]).write_bytes(b"wrong")
    elif damage == "missing":
        (folder / result["artifacts"][0]["file"]).unlink()
    else:
        if damage == "inventory":
            result["artifacts"] = []
        elif damage == "absolute":
            result["artifacts"][0]["file"] = str(folder / result["artifacts"][0]["file"])
        else:
            result["artifacts"][0]["file"] = "../source/model.joblib"
        (folder / "manifest.json").write_text(json.dumps(result), encoding="utf-8")
    with pytest.raises(TransferError):
        verify_backup(folder)


def test_missing_artifacts_are_inventoried_but_restore_is_not_claimed(tmp_path, monkeypatch):
    database = make_legacy(tmp_path / "source", monkeypatch)
    (database.parent / "model.joblib").unlink()
    folder = tmp_path / "backup"
    result = backup(database, folder, uuid4(), database.parent)
    assert all(item["status"] == "missing" for item in result["artifacts"])
    assert verify_backup(folder)["counts"]["model_deployment"] == 1
    with pytest.raises(TransferError, match="missing referenced"):
        restore_legacy(folder, tmp_path / "restore")
