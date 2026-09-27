"""Apply versioned cloud schema migrations. MAINTAINER ONLY.

Stage 5B.1 requires migrations that are repeatable, checksummed,
serialised against concurrent attempts, and rolled back on failure. The
Supabase CLI owns the file format and the local-stack workflow, but does
not verify that an already-applied file still has the contents it had
when it was applied, and does not serialise two people pushing at once.
This runner adds exactly those two things and nothing else:

    * every file is hashed; a file that changed after it was applied stops
      the run instead of being silently skipped;
    * each file is applied inside ONE transaction that first takes a
      transaction-scoped advisory lock, so a second runner waits rather
      than interleaving DDL;
    * a failure rolls that file back completely.

The CLI stays the source of truth for the files themselves
(`supabase migration new`, `supabase db reset` against the local stack).
This runner records what it applies in `supabase_migrations.schema_migrations`
as well as its own table, so the CLI does not re-apply the same file, and
adopts anything the CLI applied first, so the two views cannot drift.

Transaction-scoped locks (`pg_advisory_xact_lock`), not session locks: the
maintainer DSN points at the transaction pooler, where a "session" is not
a stable backend to hold a lock on.

Usage:
    python cloud_migrate.py --status
    python cloud_migrate.py --dry-run
    python cloud_migrate.py              # local target
    python cloud_migrate.py --yes        # remote target requires this
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from maintainer_env import describe_target, require_database_url, resolve_sslmode, safe

REPO_ROOT = Path(__file__).resolve().parent
MIGRATIONS_DIR = REPO_ROOT / "supabase" / "migrations"

# `supabase migration new` names files <14-digit UTC timestamp>_<name>.sql.
FILENAME_PATTERN = re.compile(r"^(?P<version>\d{14})_(?P<name>[a-z0-9_]+)\.sql$")

# One fixed key, so every runner contends for the same lock. Value is
# arbitrary but must never change: 'AGMG' as bytes.
ADVISORY_LOCK_KEY = 0x41474D47

TIMEOUT_PATTERN = re.compile(r"^\d{1,6}(ms|s|min)$")

DEFAULT_LOCK_TIMEOUT = "30s"
DEFAULT_STATEMENT_TIMEOUT = "300s"

BOOKKEEPING_DDL = """
create schema if not exists private;
revoke all on schema private from public;

create table if not exists private.algoguard_migration (
    version     text primary key,
    name        text        not null,
    checksum    text        not null,
    applied_at  timestamptz not null default now(),
    applied_by  text        not null default current_user,
    duration_ms integer
);

comment on table private.algoguard_migration is
    'Applied migrations and the SHA-256 of the file as applied (5B.1).';
"""


class MigrationError(RuntimeError):
    """Anything that must stop the run before the database is touched."""


@dataclass(frozen=True)
class Migration:
    version: str
    name: str
    path: Path
    checksum: str
    sql: str


@dataclass(frozen=True)
class AppliedMigration:
    version: str
    name: str
    checksum: str


@dataclass(frozen=True)
class Plan:
    pending: tuple[Migration, ...]
    problems: tuple[str, ...]


def validate_timeout(value: str, flag: str) -> str:
    """These go into SET LOCAL, which takes no bind parameters."""
    if not TIMEOUT_PATTERN.match(value):
        raise MigrationError(f"{flag}={value!r} must look like 30s, 500ms or 5min.")
    return value


def normalize(raw: bytes) -> bytes:
    """Line endings and trailing blank lines must not change a checksum.

    This repository is edited on Windows and applied from whichever
    machine is at hand. A checksum that flips because git checked the file
    out with CRLF would make the integrity check cry wolf on every run,
    and a check nobody believes is worse than no check.
    """
    return raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n").rstrip() + b"\n"


def checksum_bytes(raw: bytes) -> str:
    return hashlib.sha256(normalize(raw)).hexdigest()


def discover_migrations(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    """Read every migration file in version order, rejecting surprises."""
    if not directory.is_dir():
        raise MigrationError(f"No migrations directory at {directory}.")

    migrations: dict[str, Migration] = {}
    for path in sorted(directory.iterdir()):
        if path.is_dir() or path.suffix != ".sql":
            continue
        match = FILENAME_PATTERN.match(path.name)
        if not match:
            raise MigrationError(
                f"{path.name} is not a valid migration filename. "
                "Expected <14-digit timestamp>_<lower_snake_name>.sql — "
                "create it with `supabase migration new <name>`."
            )
        version = match.group("version")
        if version in migrations:
            raise MigrationError(
                f"Two migrations share version {version}: "
                f"{migrations[version].path.name} and {path.name}."
            )
        raw = path.read_bytes()
        migrations[version] = Migration(
            version=version,
            name=match.group("name"),
            path=path,
            checksum=checksum_bytes(raw),
            sql=normalize(raw).decode("utf-8"),
        )
    return [migrations[version] for version in sorted(migrations)]


def build_plan(
    migrations: list[Migration],
    applied: dict[str, AppliedMigration],
    *,
    allow_out_of_order: bool = False,
) -> Plan:
    """Decide what to apply, and refuse to guess about anything odd."""
    problems: list[str] = []
    on_disk = {migration.version for migration in migrations}

    for version, record in sorted(applied.items()):
        if version not in on_disk:
            problems.append(
                f"{version}_{record.name} is recorded as applied but its file is gone. "
                "Restore it from git: the database and the repository disagree."
            )

    for migration in migrations:
        record = applied.get(migration.version)
        if record and record.checksum != migration.checksum:
            problems.append(
                f"{migration.path.name} changed after it was applied "
                f"(recorded {record.checksum[:12]}…, file is {migration.checksum[:12]}…). "
                "Applied migrations are immutable — write a new migration instead."
            )

    pending = [m for m in migrations if m.version not in applied]

    if applied and pending and not allow_out_of_order:
        newest_applied = max(applied)
        stragglers = [m.path.name for m in pending if m.version < newest_applied]
        if stragglers:
            problems.append(
                "Pending migrations are older than the newest applied one "
                f"({newest_applied}): {', '.join(stragglers)}. "
                "Renumber them, or pass --allow-out-of-order if you are certain "
                "the order does not matter."
            )

    return Plan(pending=tuple(pending), problems=tuple(problems))


# ---------------------------------------------------------------------
# Database side. psycopg2 is a maintainer-only dependency, so every import
# of it is local to the function that needs it: `pytest -q` on an analyst
# clone imports this module and must not fail on a missing driver.
# ---------------------------------------------------------------------


def connect(dsn: str):
    from maintenance_connections import connect_database

    return connect_database(
        dsn,
        sslmode=resolve_sslmode(dsn),
        connect_timeout=10,
        application_name="algoguard-cloud-migrate",
    )


def ensure_bookkeeping(connection) -> None:
    with connection:
        with connection.cursor() as cursor:
            cursor.execute(BOOKKEEPING_DDL)


def read_applied(connection) -> dict[str, AppliedMigration]:
    with connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "select version, name, checksum from private.algoguard_migration"
            )
            return {
                version: AppliedMigration(version, name, checksum)
                for version, name, checksum in cursor.fetchall()
            }


def _cli_applied_versions(cursor) -> set[str]:
    cursor.execute(
        "select to_regclass('supabase_migrations.schema_migrations') is not null"
    )
    if not cursor.fetchone()[0]:
        return set()
    cursor.execute("select version from supabase_migrations.schema_migrations")
    return {row[0] for row in cursor.fetchall()}


def adopt_cli_applied(connection, migrations: list[Migration]) -> list[str]:
    """Record files the Supabase CLI already applied (e.g. `supabase db reset`).

    Without this, the first run against a freshly reset local stack would
    try to re-create every table. The checksum recorded is the file's
    CURRENT checksum, so a later edit is still caught.
    """
    adopted: list[str] = []
    with connection:
        with connection.cursor() as cursor:
            cli_versions = _cli_applied_versions(cursor)
            if not cli_versions:
                return adopted
            for migration in migrations:
                if migration.version not in cli_versions:
                    continue
                cursor.execute(
                    "insert into private.algoguard_migration "
                    "(version, name, checksum, applied_by) "
                    "values (%s, %s, %s, 'supabase-cli') "
                    "on conflict (version) do nothing",
                    (migration.version, migration.name, migration.checksum),
                )
                if cursor.rowcount:
                    adopted.append(migration.path.name)
    return adopted


def apply_one(connection, migration: Migration, *, lock_timeout: str,
              statement_timeout: str) -> bool:
    """Apply one migration in one transaction. Returns False if a
    concurrent runner got there first."""
    import psycopg2

    try:
        with connection:
            with connection.cursor() as cursor:
                cursor.execute(f"set local lock_timeout = '{lock_timeout}'")
                cursor.execute(f"set local statement_timeout = '{statement_timeout}'")

                # Held until this transaction ends, however it ends. A
                # second runner blocks here instead of interleaving DDL.
                cursor.execute("select pg_advisory_xact_lock(%s)", (ADVISORY_LOCK_KEY,))

                # Re-check under the lock: the other runner may have
                # applied this exact file while we were waiting.
                cursor.execute(
                    "select checksum from private.algoguard_migration where version = %s",
                    (migration.version,),
                )
                row = cursor.fetchone()
                if row:
                    if row[0] != migration.checksum:
                        raise MigrationError(
                            f"{migration.path.name} was applied concurrently with a "
                            "different checksum. Stopping."
                        )
                    return False

                cursor.execute("select clock_timestamp()")
                started = cursor.fetchone()[0]

                cursor.execute(migration.sql)

                cursor.execute("select clock_timestamp()")
                duration_ms = int((cursor.fetchone()[0] - started).total_seconds() * 1000)

                cursor.execute(
                    "insert into private.algoguard_migration "
                    "(version, name, checksum, duration_ms) values (%s, %s, %s, %s)",
                    (migration.version, migration.name, migration.checksum, duration_ms),
                )

                # Keep the CLI's own ledger in step, so `supabase db push`
                # does not try to apply this file a second time.
                cursor.execute(
                    "select to_regclass('supabase_migrations.schema_migrations') is not null"
                )
                if cursor.fetchone()[0]:
                    cursor.execute(
                        "insert into supabase_migrations.schema_migrations "
                        "(version, name, statements) values (%s, %s, %s) "
                        "on conflict (version) do nothing",
                        (migration.version, migration.name, [migration.sql]),
                    )
        return True
    except psycopg2.Error as exc:
        # The transaction is already rolled back; nothing from this file
        # survives. Report the database's own message, not a paraphrase.
        raise MigrationError(
            f"{migration.path.name} failed and was rolled back:\n  "
            f"{safe(str(exc).strip())}"
        ) from None


def is_remote(dsn: str) -> bool:
    """True for anything that is not the local Docker stack.

    Reuses maintainer_env's parser rather than a second one: an
    unparseable DSN resolves to "" there, which is NOT in the local set,
    so an odd connection string is treated as remote and needs --yes.
    """
    from maintainer_env import _LOCAL_HOSTS, _extract_host

    return _extract_host(dsn) not in _LOCAL_HOSTS


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--status", action="store_true",
                        help="show applied/pending migrations and exit")
    parser.add_argument("--dry-run", action="store_true",
                        help="check checksums and list what would be applied")
    parser.add_argument("--yes", action="store_true",
                        help="required to apply against a remote (cloud) target")
    parser.add_argument("--allow-out-of-order", action="store_true",
                        help="apply a pending migration older than the newest applied one")
    parser.add_argument("--migrations-dir", type=Path, default=MIGRATIONS_DIR)
    parser.add_argument("--lock-timeout", default=DEFAULT_LOCK_TIMEOUT)
    parser.add_argument("--statement-timeout", default=DEFAULT_STATEMENT_TIMEOUT)
    args = parser.parse_args(argv)

    try:
        validate_timeout(args.lock_timeout, "--lock-timeout")
        validate_timeout(args.statement_timeout, "--statement-timeout")
        migrations = discover_migrations(args.migrations_dir)
    except MigrationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if not migrations:
        print(f"No migrations found in {args.migrations_dir}.")
        return 0

    dsn = require_database_url()
    print(f"Target: {describe_target(dsn)}")

    if is_remote(dsn) and not (args.status or args.dry_run or args.yes):
        print(
            "Refusing to apply migrations to a remote target without --yes.\n"
            "Run --dry-run first, then re-run with --yes if the plan is what you expect.",
            file=sys.stderr,
        )
        return 1

    try:
        connection = connect(dsn)
    except Exception as exc:
        print(f"error: could not connect: {safe(str(exc).strip())}", file=sys.stderr)
        return 1

    try:
        ensure_bookkeeping(connection)
        adopted = adopt_cli_applied(connection, migrations)
        for name in adopted:
            print(f"adopted (applied by the Supabase CLI): {name}")

        applied = read_applied(connection)
        plan = build_plan(migrations, applied,
                          allow_out_of_order=args.allow_out_of_order)

        if args.status:
            for migration in migrations:
                mark = "applied" if migration.version in applied else "PENDING"
                print(f"  [{mark:>7}] {migration.path.name}")

        for problem in plan.problems:
            print(f"problem: {problem}", file=sys.stderr)
        if plan.problems:
            return 2

        if args.status:
            return 0

        if not plan.pending:
            print("Up to date: no pending migrations.")
            return 0

        print(f"{len(plan.pending)} pending migration(s):")
        for migration in plan.pending:
            print(f"  {migration.path.name}  sha256={migration.checksum[:12]}…")

        if args.dry_run:
            print("Dry run: nothing was applied.")
            return 0

        for migration in plan.pending:
            if apply_one(connection, migration,
                         lock_timeout=args.lock_timeout,
                         statement_timeout=args.statement_timeout):
                print(f"applied  {migration.path.name}")
            else:
                print(f"skipped  {migration.path.name} (applied concurrently)")
        with connection:
            with connection.cursor() as cursor:
                cursor.execute("NOTIFY pgrst, 'reload schema'")
        print("Done.")
        return 0
    except MigrationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
