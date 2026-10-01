"""Frozen-writer application backups and forward restore into an empty recovery database.

Auth credentials, platform configuration and signing keys are managed separately
by Supabase. This package preserves Auth UUID references, never credentials.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import urllib.error
from contextlib import closing
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request
from uuid import UUID

from cloud_connections import owned_opener
from cloud_outbox import restrict_owner
from cloud_repository import _NoRedirect
from legacy_backup import TransferError, checked_file, digest, read_only, verify_backup, write_json
from model_runtime import MAX_MODEL_BYTES
from publish_model import validate_api_url

RECOVERY_TABLES = (
    "public.profile",
    "public.node",
    "public.user_role",
    "public.node_membership",
    "public.training_run",
    "public.detection_model",
    "public.model_deployment",
    "public.capture_session",
    "public.network_traffic",
    "public.prediction",
    "public.alert",
    "public.report",
    "public.report_alert",
    "public.system_log",
    "public.ingest_event",
    "public.model_manifest",
    "private.legacy_source",
    "private.legacy_record",
    "private.administration_request",
)


def _rows(cursor):
    from psycopg2 import sql

    result = {}
    for table in RECOVERY_TABLES:
        cursor.execute(
            sql.SQL("select row_to_json(t) from {} t order by to_jsonb(t)::text").format(
                sql.Identifier(*table.split("."))
            )
        )
        result[table] = [row[0] for row in cursor.fetchall()]
    return result


class ModelObjects:
    """Maintainer Storage access via registered connections; secrets never enter the package."""

    def __init__(self, api_url, key):
        validate_api_url(api_url)
        self.api_url = api_url.rstrip("/")
        self.key = key
        self.opener = owned_opener(_NoRedirect())

    def _request(self, path, content=None):
        if not path or path.startswith("/") or any(p in (".", "..") for p in path.split("/")):
            raise TransferError("Invalid model object path.")
        request = Request(
            self.api_url + "/storage/v1/object/models/" + quote(path, safe="/"),
            data=content,
            method="GET" if content is None else "POST",
            headers={
                "apikey": self.key,
                "Authorization": "Bearer " + self.key,
                "Content-Type": "application/octet-stream",
                "x-upsert": "false",
            },
        )
        try:
            with self.opener.open(request, timeout=10) as response:
                data = response.read(MAX_MODEL_BYTES + 1)
                if len(data) > MAX_MODEL_BYTES:
                    raise TransferError("Model object exceeds the supported size.")
                return data
        except urllib.error.HTTPError as error:
            code = error.code
            error.close()
            if content is None and code in (400, 404):
                return None
            raise TransferError("Model Storage request was refused.") from None

    def get(self, path):
        return self._request(path)

    def ensure(self, path, content):
        existing = self.get(path)
        if existing is None:
            self._request(path, content)
            existing = self.get(path)
        if existing != content:
            raise TransferError("Destination model object differs; it was not overwritten.")


def export_cloud(connection, output: Path, objects, *, legacy_bundles=(), outboxes=()):
    if output.exists():
        raise TransferError("Choose a new recovery directory.")
    restrict_owner(output)
    with connection, connection.cursor() as cursor:
        cursor.execute("set transaction isolation level repeatable read, read only")
        cursor.execute("set local time zone 'UTC'")
        cursor.execute("select 1 from private.legacy_source where state<>'frozen' limit 1")
        if cursor.fetchone():
            raise TransferError("Freeze imported nodes before taking a cloud recovery snapshot.")
        rows = _rows(cursor)
    supplied = {}
    for directory in legacy_bundles:
        manifest = verify_backup(directory)
        supplied[manifest["source_id"]] = (directory, manifest)
    for source in rows["private.legacy_source"]:
        selected = supplied.get(source["source_id"])
        if not selected or selected[1]["snapshot_sha256"] != source["snapshot_sha256"]:
            raise TransferError(
                "Supply the verified legacy bundle for every imported installation."
            )
        shutil.copytree(selected[0], output / "legacy" / source["source_id"])
    artifact_records = []
    for manifest in rows["public.model_manifest"]:
        content = objects.get(manifest["object_path"])
        entry = {"object_path": manifest["object_path"], "sha256": manifest["object_sha256"]}
        if content is None:
            if manifest["status"] in ("active", "superseded", "published"):
                raise TransferError("A required published model object is missing.")
            entry["status"] = "unpublished_missing"
        else:
            sha = hashlib.sha256(content).hexdigest()
            if sha != manifest["object_sha256"] or len(content) != manifest["object_bytes"]:
                raise TransferError("Cloud model bytes disagree with their protected manifest.")
            file = output / "artifacts" / (sha + ".joblib")
            file.parent.mkdir(exist_ok=True)
            file.write_bytes(content)
            entry.update(status="copied", file=file.relative_to(output).as_posix())
        artifact_records.append(entry)
    pending = []
    for index, directory in enumerate(outboxes):
        destination = output / "outboxes" / str(index) / "outbox.sqlite3"
        destination.parent.mkdir(parents=True)
        with closing(read_only(directory / "outbox.sqlite3")) as source:
            with closing(sqlite3.connect(destination)) as target:
                source.backup(target)
                if target.execute("pragma quick_check").fetchone()[0] != "ok":
                    raise TransferError("Pending-event spool failed integrity verification.")
        pending.append(destination.relative_to(output).as_posix())
    write_json(output / "records.json", rows)
    files = {
        path.relative_to(output).as_posix(): digest(path)
        for path in output.rglob("*")
        if path.is_file()
    }
    summary = {
        "format": 1,
        "files": files,
        "artifacts": artifact_records,
        "outboxes": pending,
        "counts": {table: len(records) for table, records in rows.items()},
    }
    write_json(output / "recovery.json", summary)
    verify_recovery(output)
    return summary


def verify_recovery(directory):
    try:
        summary = json.loads((directory / "recovery.json").read_text(encoding="utf-8"))
        if summary["format"] != 1:
            raise TransferError("Unsupported recovery format.")
        required = {"records.json", *summary["outboxes"]}
        required.update(a["file"] for a in summary["artifacts"] if a["status"] == "copied")
        if not required <= summary["files"].keys():
            raise TransferError("Recovery file inventory is incomplete.")
        for relative, sha in summary["files"].items():
            if digest(checked_file(directory, relative)) != sha:
                raise TransferError("Recovery file hash mismatch.")
        rows = json.loads(checked_file(directory, "records.json").read_text(encoding="utf-8"))
        if set(rows) != set(RECOVERY_TABLES):
            raise TransferError("Recovery table inventory is incomplete.")
        if {table: len(records) for table, records in rows.items()} != summary["counts"]:
            raise TransferError("Recovery row counts disagree with the manifest.")
        manifests = {row["object_path"]: row for row in rows["public.model_manifest"]}
        artifacts = {item["object_path"]: item for item in summary["artifacts"]}
        if set(artifacts) != set(manifests) or len(artifacts) != len(summary["artifacts"]):
            raise TransferError("Recovery model inventory is incomplete or duplicated.")
        for name, manifest in manifests.items():
            artifact = artifacts[name]
            if artifact["sha256"] != manifest["object_sha256"]:
                raise TransferError("Recovery artifact disagrees with its protected manifest.")
            if artifact["status"] == "copied":
                path = checked_file(directory, artifact["file"])
                if (
                    digest(path) != manifest["object_sha256"]
                    or path.stat().st_size != manifest["object_bytes"]
                ):
                    raise TransferError("Recovery model bytes disagree with their manifest.")
            elif artifact["status"] != "unpublished_missing" or manifest["status"] in (
                "active",
                "superseded",
                "published",
            ):
                raise TransferError("A required recovery model is unavailable.")
        for source in rows["private.legacy_source"]:
            source_id = str(UUID(source["source_id"]))
            legacy = verify_backup(directory / "legacy" / source_id)
            if (
                legacy["source_id"] != source_id
                or legacy["snapshot_sha256"] != source["snapshot_sha256"]
            ):
                raise TransferError("Recovery legacy snapshot disagrees with its source ledger.")
        return summary, rows
    except (KeyError, TypeError, ValueError, OSError) as error:
        raise TransferError("Recovery package is invalid or incomplete.") from error


def restore_cloud(connection, directory, objects):
    """Insert a complete application snapshot, preserving keys and deduplication receipts.

    Never deletes/overwrites destination rows. Supabase migrations and referenced
    Auth users must already exist. Identical restore retries are harmless.
    """
    from psycopg2 import sql
    from psycopg2.extras import Json

    summary, rows = verify_recovery(directory)
    with connection, connection.cursor() as cursor:
        cursor.execute("set local lock_timeout='10s'")
        cursor.execute("set local time zone 'UTC'")
        for table in RECOVERY_TABLES:
            cursor.execute(
                sql.SQL("lock table {} in exclusive mode").format(sql.Identifier(*table.split(".")))
            )
        existing = _rows(cursor)
        if existing == rows:
            replayed = True
        elif any(existing.values()):
            raise TransferError(
                "Recovery target contains different application data; restore refused."
            )
        else:
            replayed = False
        for artifact in summary["artifacts"]:
            if artifact["status"] == "copied":
                objects.ensure(
                    artifact["object_path"], checked_file(directory, artifact["file"]).read_bytes()
                )
        if replayed:
            return {"replayed": True, "counts": summary["counts"]}
        replacements = []
        for table in RECOVERY_TABLES:
            for value in rows[table]:
                record = dict(value)
                if table == "public.model_deployment":
                    replacements.append((record["replaced_deployment_id"], record["deployment_id"]))
                    record["replaced_deployment_id"] = None
                cursor.execute(
                    sql.SQL(
                        "insert into {} select * from json_populate_record(null::{},%s)"
                    ).format(sql.Identifier(*table.split(".")), sql.Identifier(*table.split("."))),
                    (Json(record),),
                )
        for previous, deployment_id in replacements:
            cursor.execute(
                "update public.model_deployment set replaced_deployment_id=%s "
                "where deployment_id=%s",
                (previous, deployment_id),
            )
        if _rows(cursor) != rows:
            raise TransferError("Restored data differs from the recovery snapshot.")
        # Explicit keys do not advance sequences. Never rewind a sequence, even on a retry.
        for table in RECOVERY_TABLES:
            schema, name = table.split(".")
            cursor.execute(
                "select column_name from information_schema.columns "
                "where table_schema=%s and table_name=%s and is_identity='YES'",
                (schema, name),
            )
            for (column,) in cursor.fetchall():
                cursor.execute("select pg_get_serial_sequence(%s,%s)", (table, column))
                sequence = cursor.fetchone()[0]
                cursor.execute(
                    sql.SQL("select last_value from {}").format(
                        sql.Identifier(*sequence.split("."))
                    )
                )
                last = cursor.fetchone()[0]
                highest = max([last, *[record[column] for record in rows[table]]])
                cursor.execute("select setval(%s,%s,true)", (sequence, highest))
        return {"replayed": False, "counts": summary["counts"]}


def restore_outbox(directory, index, output):
    summary, _ = verify_recovery(directory)
    if output.exists():
        raise TransferError("Pending-event restore requires a new state directory.")
    relative = summary["outboxes"][index]
    restrict_owner(output)
    shutil.copyfile(checked_file(directory, relative), output / "outbox.sqlite3")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("export", "restore", "verify", "restore-outbox"))
    parser.add_argument("directory", type=Path)
    parser.add_argument("--writers-stopped", action="store_true")
    parser.add_argument("--legacy-bundle", type=Path, action="append", default=[])
    parser.add_argument("--outbox", type=Path, action="append", default=[])
    parser.add_argument("--outbox-index", type=int, default=0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "verify":
            result, _ = verify_recovery(args.directory)
        elif args.command == "restore-outbox":
            if args.output is None:
                parser.error("--output is required for pending-event restore")
            restore_outbox(args.directory, args.outbox_index, args.output)
            print("Pending records restored; original-user online login is still required.")
            return 0
        else:
            if not args.writers_stopped:
                parser.error("Stop affected apps/upload workers and attest with --writers-stopped")
            from maintainer_env import require_database_url, resolve_sslmode
            from maintenance_connections import connect_database

            dsn = require_database_url()
            objects = ModelObjects(
                os.environ["SUPABASE_API_URL"], os.environ["SUPABASE_SECRET_KEY"]
            )
            with closing(
                connect_database(dsn, sslmode=resolve_sslmode(dsn), connect_timeout=10)
            ) as conn:
                result = (
                    export_cloud(
                        conn,
                        args.directory,
                        objects,
                        legacy_bundles=args.legacy_bundle,
                        outboxes=args.outbox,
                    )
                    if args.command == "export"
                    else restore_cloud(conn, args.directory, objects)
                )
        print(json.dumps({"counts": result["counts"]}, indent=2))
        return 0
    except Exception:
        print(
            "Recovery refused or rolled back. Preserve the package and check the isolated target."
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
