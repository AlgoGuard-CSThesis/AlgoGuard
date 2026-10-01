"""Consistent, owner-restricted legacy backups. Never imports or starts the app."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path
from uuid import UUID

from cloud_outbox import restrict_owner

TABLE_KEYS = {
    "admin": "admin_id",
    "training_run": "run_id",
    "detection_model": "model_id",
    "model_deployment": "deployment_id",
    "capture_session": "capture_id",
    "network_traffic": "traffic_id",
    "prediction": "prediction_id",
    "alert": "alert_id",
    "report": "report_id",
    "system_log": "log_id",
    "report_alert": "report_id,alert_id",
}


class TransferError(RuntimeError):
    """A controlled failure, with no credentials or record contents in its message."""


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def read_only(path: Path):
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def write_json(path: Path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False), encoding="utf-8")


def checked_file(directory: Path, relative: str) -> Path:
    if Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise TransferError("Backup file must use a relative path inside its directory.")
    path = (directory / relative).resolve()
    if not path.is_relative_to(directory.resolve()) or not path.is_file():
        raise TransferError("Backup file is missing or outside its directory.")
    return path


def inventory(connection):
    if connection.execute("pragma quick_check").fetchone()[0] != "ok":
        raise TransferError("SQLite integrity verification failed.")
    tables = {
        row[0] for row in connection.execute("select name from sqlite_master where type='table'")
    }
    missing = set(TABLE_KEYS) - tables
    if missing:
        raise TransferError("Upgrade a restored copy with migrate.py before making this backup.")
    violations = list(connection.execute("pragma foreign_key_check"))
    if violations:
        raise TransferError("Legacy relationships are broken; preserve and repair a copy first.")
    return {
        "counts": {
            table: connection.execute(f'select count(*) from "{table}"').fetchone()[0]
            for table in TABLE_KEYS
        },
        "schema_versions": [
            dict(row)
            for row in connection.execute(
                "select version,name,applied_at from schema_migration order by version"
            )
        ],
        "account_ids": [row[0] for row in connection.execute("select admin_id from admin")],
        "deployments": [
            dict(row)
            for row in connection.execute(
                "select deployment_id,model_id,run_id,is_active from model_deployment"
            )
        ],
    }


def backup(database: Path, output: Path, source_id: UUID, installation_root: Path):
    """Online SQLite backup is consistent; final cutover still requires stopped writers."""
    if output.exists():
        raise TransferError("Choose a new backup directory; existing backups are never replaced.")
    restrict_owner(output)
    snapshot = output / "legacy.sqlite3"
    with closing(read_only(database)) as source, closing(sqlite3.connect(snapshot)) as target:
        source.backup(target)
    with closing(read_only(snapshot)) as connection:
        summary = inventory(connection)
        artifacts = []
        root = installation_root.resolve()
        for table in ("detection_model", "model_deployment"):
            key = TABLE_KEYS[table]
            for row in connection.execute(
                f'select "{key}",artifact_path from "{table}" where artifact_path is not null'
            ):
                original = str(row["artifact_path"])
                path = Path(original)
                path = (root / path).resolve() if not path.is_absolute() else path.resolve()
                entry = {"table": table, "id": row[key], "original_path": original}
                if not path.is_relative_to(root) or path.suffix.lower() != ".joblib":
                    entry["status"] = "outside_artifact_scope"
                elif not path.is_file():
                    entry["status"] = "missing"
                else:
                    sha = digest(path)
                    relative = f"artifacts/{sha}.joblib"
                    destination = output / relative
                    destination.parent.mkdir(exist_ok=True)
                    shutil.copyfile(path, destination)
                    if digest(destination) != sha:
                        raise TransferError(
                            "An artifact changed during backup; stop writers and retry."
                        )
                    entry.update(
                        status="copied", sha256=sha, size=destination.stat().st_size, file=relative
                    )
                artifacts.append(entry)
    manifest = {
        "format": 1,
        "source_id": str(source_id),
        "snapshot_sha256": digest(snapshot),
        **summary,
        "artifacts": artifacts,
    }
    write_json(output / "manifest.json", manifest)
    verify_backup(output)
    return manifest


def verify_backup(directory: Path):
    try:
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        UUID(manifest["source_id"])
        if manifest["format"] != 1:
            raise TransferError("Unsupported backup format.")
        snapshot = checked_file(directory, "legacy.sqlite3")
        if digest(snapshot) != manifest["snapshot_sha256"]:
            raise TransferError("Legacy snapshot hash mismatch.")
        with closing(read_only(snapshot)) as connection:
            if inventory(connection)["counts"] != manifest["counts"]:
                raise TransferError("Legacy snapshot counts changed.")
            references = {
                (table, row[0])
                for table in ("detection_model", "model_deployment")
                for row in connection.execute(
                    f'select "{TABLE_KEYS[table]}" from "{table}" where artifact_path is not null'
                )
            }
        listed = [(entry["table"], entry["id"]) for entry in manifest["artifacts"]]
        if len(listed) != len(set(listed)) or set(listed) != references:
            raise TransferError("Backup artifact inventory is incomplete or duplicated.")
        for entry in manifest["artifacts"]:
            if entry["status"] not in ("copied", "missing", "outside_artifact_scope"):
                raise TransferError("Unknown backup artifact status.")
            if entry["status"] == "copied":
                path = checked_file(directory, entry["file"])
                if digest(path) != entry["sha256"] or path.stat().st_size != entry["size"]:
                    raise TransferError("Backup artifact integrity verification failed.")
        return manifest
    except (KeyError, TypeError, ValueError, OSError, sqlite3.Error) as error:
        raise TransferError("Backup is invalid or incomplete.") from error


def restore_legacy(directory: Path, output: Path):
    """Restore into a NEW installation directory; do not overwrite any working store."""
    manifest = verify_backup(directory)
    if output.exists():
        raise TransferError("Legacy restore requires a new directory.")
    missing = [item for item in manifest["artifacts"] if item["status"] != "copied"]
    if missing:
        raise TransferError(
            "Resolve missing referenced artifacts before declaring a usable restore."
        )
    restrict_owner(output)
    target = output / "legacy.sqlite3"
    shutil.copyfile(directory / "legacy.sqlite3", target)
    with closing(sqlite3.connect(target)) as connection, connection:
        for item in manifest["artifacts"]:
            artifact = output / item["file"]
            artifact.parent.mkdir(exist_ok=True)
            shutil.copyfile(checked_file(directory, item["file"]), artifact)
            table = item["table"]
            if table not in ("detection_model", "model_deployment"):
                raise TransferError("Unsupported artifact reference.")
            connection.execute(
                f'update "{table}" set artifact_path=? where "{TABLE_KEYS[table]}"=?',
                (str(artifact.resolve()), item["id"]),
            )
        if connection.execute("pragma foreign_key_check").fetchone():
            raise TransferError("Restored relationships failed verification.")
    return target


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("backup")
    create.add_argument("database", type=Path)
    create.add_argument("output", type=Path)
    create.add_argument("--source-id", type=UUID, required=True)
    create.add_argument("--installation-root", type=Path, required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("directory", type=Path)
    restore = sub.add_parser("restore")
    restore.add_argument("directory", type=Path)
    restore.add_argument("output", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "backup":
            result = backup(args.database, args.output, args.source_id, args.installation_root)
        elif args.command == "verify":
            result = verify_backup(args.directory)
        else:
            restore_legacy(args.directory, args.output)
            print(
                "Legacy restore verified in the new directory; active configuration is unchanged."
            )
            return 0
        print(
            json.dumps(
                {
                    key: result[key]
                    for key in (
                        "source_id",
                        "counts",
                        "account_ids",
                        "schema_versions",
                        "snapshot_sha256",
                    )
                },
                indent=2,
            )
        )
        print("Unavailable artifacts:", sum(x["status"] != "copied" for x in result["artifacts"]))
        return 0
    except (TransferError, OSError, sqlite3.Error):
        print("Backup/restore failed verification. Source files have not been changed.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
