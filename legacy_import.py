"""Maintainer-only, atomic legacy import and explicit node cutover gates (5E)."""

from __future__ import annotations

import argparse
import json
from contextlib import closing
from pathlib import Path
from uuid import UUID

from legacy_backup import TABLE_KEYS, TransferError, read_only, verify_backup

IMPORT_LOCK = 0x41474C49
ORDER = tuple(TABLE_KEYS)
TARGETS = {**{name: name for name in ORDER}, "admin": "profile"}
TARGET_KEYS = {**TABLE_KEYS, "admin": "profile_id"}
REFERENCES = {
    "training_run": {
        "admin_id": ("admin", "created_by"),
        "recommended_model_id": ("detection_model", "recommended_model_id"),
    },
    "detection_model": {"run_id": ("training_run", "run_id")},
    "model_deployment": {
        "model_id": ("detection_model", "model_id"),
        "run_id": ("training_run", "run_id"),
        "deployed_by": ("admin", "deployed_by"),
        "replaced_deployment_id": ("model_deployment", "replaced_deployment_id"),
    },
    "prediction": {
        "traffic_id": ("network_traffic", "traffic_id"),
        "model_id": ("detection_model", "model_id"),
        "deployment_id": ("model_deployment", "deployment_id"),
    },
    "alert": {"prediction_id": ("prediction", "prediction_id")},
    "report": {"run_id": ("training_run", "run_id")},
    "system_log": {
        "run_id": ("training_run", "run_id"),
        "prediction_id": ("prediction", "prediction_id"),
    },
    "report_alert": {"report_id": ("report", "report_id"), "alert_id": ("alert", "alert_id")},
}


def _insert(cursor, table, values):
    from psycopg2 import sql

    cursor.execute(
        sql.SQL("insert into public.{} ({}) values ({})").format(
            sql.Identifier(table),
            sql.SQL(",").join(map(sql.Identifier, values)),
            sql.SQL(",").join(sql.Placeholder() for _ in values),
        ),
        tuple(values.values()),
    )


def _key(table, row):
    return json.dumps([row[column] for column in TABLE_KEYS[table].split(",")])


def _source_rows(directory):
    with closing(read_only(directory / "legacy.sqlite3")) as connection:
        return {
            table: [
                dict(row)
                for row in connection.execute(
                    f'select * from "{table}" order by {TABLE_KEYS[table]}'
                )
            ]
            for table in ORDER
        }


def _artifact_reference(manifest, table, row):
    entry = next(
        (
            item
            for item in manifest["artifacts"]
            if item["table"] == table and item["id"] == row[TABLE_KEYS[table]]
        ),
        None,
    )
    if not entry:
        return None
    name = entry.get("sha256") or f"unavailable-{table}-{entry['id']}"
    return f"legacy/{manifest['source_id']}/{name}.joblib"


def import_bundle(connection, directory: Path, node_id: UUID, identity_map=None):
    """One immutable snapshot, one transaction. Retrying it never duplicates rows.

    identity_map explicitly maps legacy admin IDs to fresh Auth UUIDs without
    existing profiles. No role is copied or granted by import.
    """
    from psycopg2 import sql
    from psycopg2.extras import Json

    manifest = verify_backup(directory)
    source_id = manifest["source_id"]
    rows = _source_rows(directory)
    identities = {int(key): str(UUID(str(value))) for key, value in (identity_map or {}).items()}
    if set(identities) - {row["admin_id"] for row in rows["admin"]}:
        raise TransferError("Identity mapping names an unknown legacy account.")
    # Commit the protective gate separately so an interrupted first import cannot
    # reopen a normal (NULL-state) node. Business rows still import atomically.
    with connection, connection.cursor() as cursor:
        cursor.execute("set local lock_timeout='10s'")
        cursor.execute("select pg_advisory_xact_lock(%s)", (IMPORT_LOCK,))
        cursor.execute("select 1 from private.legacy_source where source_id=%s", (source_id,))
        if cursor.fetchone() is None:
            cursor.execute(
                "select 1 from private.legacy_source where node_id=%s", (str(node_id),)
            )
            if cursor.fetchone():
                raise TransferError("This node already belongs to another legacy source.")
            cursor.execute(
                "select status from public.node where node_id=%s for update", (str(node_id),)
            )
            node = cursor.fetchone()
            if not node or node[0] != "approved":
                raise TransferError("Enroll and approve the dedicated legacy node before import.")
            for table in (
                "capture_session",
                "network_traffic",
                "prediction",
                "alert",
                "report",
                "system_log",
            ):
                cursor.execute(
                    f"select 1 from public.{table} where node_id=%s limit 1", (str(node_id),)
                )
                if cursor.fetchone():
                    raise TransferError("Use a dedicated empty node for the legacy installation.")
            cursor.execute(
                "update public.node set migration_state='importing' where node_id=%s",
                (str(node_id),),
            )
    with connection, connection.cursor() as cursor:
        cursor.execute("set local lock_timeout='10s'")
        cursor.execute("set local statement_timeout='120s'")
        cursor.execute("select pg_advisory_xact_lock(%s)", (IMPORT_LOCK,))
        cursor.execute(
            "select node_id,snapshot_sha256 from private.legacy_source where source_id=%s",
            (source_id,),
        )
        previous = cursor.fetchone()
        if previous:
            if str(previous[0]) != str(node_id) or previous[1] != manifest["snapshot_sha256"]:
                raise TransferError(
                    "Source identity is already bound to a different snapshot/node."
                )
            cursor.execute(
                "select legacy_admin_id,auth_user_id from public.profile "
                "where legacy_source_id=%s and auth_user_id is not null",
                (source_id,),
            )
            if {key: str(value) for key, value in cursor.fetchall()} != identities:
                raise TransferError("Identity mapping differs from the original import.")
            cursor.execute(
                "select source_table,count(*) from private.legacy_record "
                "where source_id=%s group by source_table",
                (source_id,),
            )
            actual = dict(cursor.fetchall())
            if any(actual.get(t, 0) != count for t, count in manifest["counts"].items()):
                raise TransferError("Import receipts are incomplete; restore the cloud backup.")
            cursor.execute(
                "select target_table,target_key from private.legacy_record where source_id=%s",
                (source_id,),
            )
            for target, keys in cursor.fetchall():
                cursor.execute(
                    sql.SQL("select 1 from public.{} where {}").format(
                        sql.Identifier(target),
                        sql.SQL(" and ").join(
                            sql.SQL("{}=%s").format(sql.Identifier(key)) for key in keys
                        ),
                    ),
                    tuple(keys.values()),
                )
                if cursor.fetchone() is None:
                    raise TransferError("An imported row is missing; restore the cloud backup.")
            return {"source_id": source_id, "counts": manifest["counts"], "replayed": True}
        cursor.execute(
            "select status from public.node where node_id=%s for update", (str(node_id),)
        )
        node = cursor.fetchone()
        if not node or node[0] != "approved":
            raise TransferError("Enroll and approve the dedicated legacy node before import.")
        for table in (
            "capture_session",
            "network_traffic",
            "prediction",
            "alert",
            "report",
            "system_log",
        ):
            cursor.execute(
                f"select 1 from public.{table} where node_id=%s limit 1", (str(node_id),)
            )
            if cursor.fetchone():
                raise TransferError("Use a dedicated empty node for the legacy installation.")
        cursor.execute(
            "update public.node set migration_state='importing' where node_id=%s", (str(node_id),)
        )
        cursor.execute(
            "insert into private.legacy_source "
            "(source_id,node_id,snapshot_sha256,counts,state) values(%s,%s,%s,%s,'verified')",
            (source_id, str(node_id), manifest["snapshot_sha256"], Json(manifest["counts"])),
        )
        mapped = {}
        for table in ORDER:
            mapped[table] = {}
            if table == "report_alert":
                continue
            for row in rows[table]:
                cursor.execute(
                    "select nextval(pg_get_serial_sequence(%s,%s))",
                    ("public." + TARGETS[table], TARGET_KEYS[table]),
                )
                mapped[table][row[TABLE_KEYS[table]]] = cursor.fetchone()[0]
        cursor.execute(
            "insert into public.profile(username,legacy_source_id,legacy_username,"
            "is_active) values(%s,%s,'Unattributed legacy record',false) returning profile_id",
            ("legacy_" + UUID(source_id).hex + "_unattributed", source_id),
        )
        unknown = cursor.fetchone()[0]

        def account(value):
            return mapped["admin"].get(value, unknown)

        # SQLite traffic has no owner. Only a unique recorded prediction actor is evidence.
        candidates = {}
        for row in rows["system_log"]:
            if row.get("prediction_id") is not None and row.get("admin_id") is not None:
                candidates.setdefault(row["prediction_id"], set()).add(row["admin_id"])
        traffic_actors = {}
        for row in rows["prediction"]:
            traffic_actors.setdefault(row["traffic_id"], set()).update(
                candidates.get(row["prediction_id"], set())
            )
        traffic_owners = {
            row["traffic_id"]: (
                account(next(iter(traffic_actors[row["traffic_id"]])))
                if len(traffic_actors.get(row["traffic_id"], set())) == 1
                else unknown
            )
            for row in rows["network_traffic"]
        }
        prediction_owners = {
            row["prediction_id"]: traffic_owners[row["traffic_id"]] for row in rows["prediction"]
        }
        alert_owners = {
            row["alert_id"]: prediction_owners[row["prediction_id"]] for row in rows["alert"]
        }
        unresolved_reports = {
            row["report_id"]
            for row in rows["report_alert"]
            if alert_owners[row["alert_id"]] == unknown
        }
        replacements = []
        unresolved_count = 0
        for table in ORDER:
            target = TARGETS[table]
            for original in rows[table]:
                row = dict(original)
                row.pop("password_hash", None)
                archived = dict(row)
                unresolved = False
                if table == "admin":
                    row = {
                        "profile_id": mapped[table][row["admin_id"]],
                        "legacy_source_id": source_id,
                        "legacy_admin_id": row["admin_id"],
                        "legacy_username": row["username"],
                        "email": row.get("email"),
                        "legacy_created_at": row["created_at"],
                        "username": f"legacy_{UUID(source_id).hex}_{row['admin_id']}",
                        "auth_user_id": identities.get(row["admin_id"]),
                        "is_active": row["admin_id"] in identities,
                    }
                else:
                    if table != "report_alert":
                        row[TARGET_KEYS[table]] = mapped[table][original[TABLE_KEYS[table]]]
                    for field, (parent, destination) in REFERENCES.get(table, {}).items():
                        value = row.pop(field, None)
                        row[destination] = None if value is None else mapped[parent][value]
                    if table in ("detection_model", "model_deployment"):
                        row["artifact_path"] = _artifact_reference(manifest, table, original)
                        archived["artifact_path"] = row["artifact_path"]
                        if table == "detection_model":
                            row["is_deployed"] = 0
                        else:
                            row["is_active"] = 0
                            replacements.append(
                                (row["replaced_deployment_id"], row["deployment_id"])
                            )
                            row["replaced_deployment_id"] = None
                    if table in ("capture_session", "report"):
                        owner = account(row.pop("admin_id", None))
                    elif table == "network_traffic":
                        owner = traffic_owners[original["traffic_id"]]
                    elif table == "prediction":
                        owner = prediction_owners[original["prediction_id"]]
                    elif table == "alert":
                        owner = alert_owners[original["alert_id"]]
                    elif table == "system_log":
                        actor = row.pop("admin_id", None)
                        owner = prediction_owners.get(original.get("prediction_id"), account(actor))
                    else:
                        owner = None
                    if owner is not None:
                        unresolved = owner == unknown or (
                            table == "report" and original["report_id"] in unresolved_reports
                        )
                        row["profile_id" if table == "system_log" else "owner_profile_id"] = owner
                        row["node_id"] = str(node_id)
                        row["legacy_unresolved"] = unresolved
                        if table in ("report", "system_log") and row.get("run_id") is not None:
                            if not row.get("prediction_id"):
                                row["node_id"] = None
                    if table == "report_alert":
                        report = next(
                            r for r in rows["report"] if r["report_id"] == original["report_id"]
                        )
                        row["node_id"] = None if report.get("run_id") is not None else str(node_id)
                _insert(cursor, target, row)
                key = {name: row[name] for name in TARGET_KEYS[table].split(",")}
                cursor.execute(
                    "insert into private.legacy_record "
                    "(source_id,source_table,source_key,target_table,target_key,"
                    "original_record,unresolved) values(%s,%s,%s,%s,%s,%s,%s)",
                    (
                        source_id,
                        table,
                        _key(table, original),
                        target,
                        Json(key),
                        Json(archived),
                        unresolved,
                    ),
                )
                unresolved_count += int(unresolved)
        for previous_id, deployment_id in replacements:
            cursor.execute(
                "update public.model_deployment set replaced_deployment_id=%s "
                "where deployment_id=%s",
                (previous_id, deployment_id),
            )
        cursor.execute(
            "update public.node set migration_state='verified' where node_id=%s", (str(node_id),)
        )
        # IDs always come from the destination sequences, including colliding BIGINT source IDs.
        return {
            "source_id": source_id,
            "counts": manifest["counts"],
            "unresolved": unresolved_count,
            "replayed": False,
        }


def set_write_gate(connection, source_id: UUID, *, enabled: bool):
    from model_runtime import verify_manifest

    with connection, connection.cursor() as cursor:
        cursor.execute("select pg_advisory_xact_lock(%s)", (IMPORT_LOCK,))
        cursor.execute(
            "select node_id from private.legacy_source where source_id=%s for update",
            (str(source_id),),
        )
        row = cursor.fetchone()
        if row is None:
            raise TransferError("Import and verify this source before changing its write gate.")
        if enabled:
            cursor.execute(
                "select row_to_json(m) from public.model_manifest m where status='active'"
            )
            active = cursor.fetchone()
            if not active:
                raise TransferError(
                    "Publish a compatible active model through publish_model.py first."
                )
            verify_manifest(active[0])
        state = "live" if enabled else "frozen"
        cursor.execute(
            "update public.node set migration_state=%s where node_id=%s", (state, row[0])
        )
        cursor.execute(
            "update private.legacy_source set state=%s,enabled_at=case when %s "
            "then coalesce(enabled_at,now()) else enabled_at end where source_id=%s",
            (state, enabled, str(source_id)),
        )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    load = sub.add_parser("import")
    load.add_argument("directory", type=Path)
    load.add_argument("--node-id", type=UUID, required=True)
    load.add_argument("--identity-map", type=Path)
    for command in ("enable", "freeze"):
        gate = sub.add_parser(command)
        gate.add_argument("--source-id", type=UUID, required=True)
    parser.add_argument(
        "--writers-stopped",
        action="store_true",
        required=True,
        help="Attest that affected capture processes and upload workers are stopped",
    )
    args = parser.parse_args(argv)
    from maintainer_env import require_database_url, resolve_sslmode
    from maintenance_connections import connect_database

    dsn = require_database_url()
    try:
        with closing(
            connect_database(dsn, sslmode=resolve_sslmode(dsn), connect_timeout=10)
        ) as conn:
            if args.command == "import":
                identities = (
                    json.loads(args.identity_map.read_text(encoding="utf-8"))
                    if args.identity_map
                    else None
                )
                print(json.dumps(import_bundle(conn, args.directory, args.node_id, identities)))
            else:
                set_write_gate(conn, args.source_id, enabled=args.command == "enable")
                print("Node write gate updated; local application configuration is unchanged.")
        return 0
    except Exception:
        print("Transfer refused or rolled back. Check the snapshot, node, and maintenance target.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
