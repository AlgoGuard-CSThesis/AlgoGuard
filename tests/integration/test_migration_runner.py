"""Real local PostgreSQL verification of maintainer migration transactions."""

import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from stack_support import connect_local_database

from cloud_migrate import (
    Migration,
    MigrationError,
    apply_one,
    checksum_bytes,
    discover_migrations,
    main,
)

psycopg2 = pytest.importorskip("psycopg2")
pytestmark = pytest.mark.integration


def test_runner_refuses_edited_copy_of_applied_migration(local_stack, tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", local_stack["db_url"])
    assert main(["--status"]) == 0
    for migration in discover_migrations():
        (tmp_path / migration.path.name).write_bytes(migration.path.read_bytes())
    first = sorted(tmp_path.glob("*.sql"))[0]
    with first.open("a", encoding="utf-8") as output:
        output.write("\n-- checksum refusal probe\n")
    assert main(["--dry-run", "--migrations-dir", str(tmp_path)]) == 2


def test_runner_serializes_and_rolls_back(local_stack, tmp_path):
    token = uuid.uuid4().hex[:12]
    table = f"zz_migration_smoke_{token}"
    version = "20990101" + str(int(token, 16) % 1000000).zfill(6)
    sql = f"create table private.{table} (id bigint primary key); select pg_sleep(0.2);"
    migration = Migration(
        version, table, tmp_path / f"{version}_{table}.sql", checksum_bytes(sql.encode()), sql
    )

    def run(candidate):
        connection = connect_local_database(psycopg2.connect, local_stack["db_url"])
        try:
            return apply_one(connection, candidate, lock_timeout="5s", statement_timeout="10s")
        finally:
            connection.close()

    connection = connect_local_database(psycopg2.connect, local_stack["db_url"])
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(run, [migration, migration]))
        assert sorted(outcomes) == [False, True]
        bad_sql = f"create table private.{table}_bad (id bigint); select 1/0;"
        bad = Migration(
            "20991231000000",
            table + "_bad",
            tmp_path / "bad.sql",
            checksum_bytes(bad_sql.encode()),
            bad_sql,
        )
        with pytest.raises(MigrationError, match="rolled back"):
            run(bad)
        with connection.cursor() as cursor:
            cursor.execute("select to_regclass(%s)", (f"private.{table}_bad",))
            assert cursor.fetchone()[0] is None
            cursor.execute(
                "select count(*) from private.algoguard_migration where version=%s",
                (bad.version,),
            )
            assert cursor.fetchone()[0] == 0
    finally:
        with connection:
            with connection.cursor() as cursor:
                cursor.execute(f"drop table if exists private.{table}")
                cursor.execute(
                    "delete from private.algoguard_migration where version=%s", (version,)
                )
                cursor.execute(
                    "delete from supabase_migrations.schema_migrations where version=%s", (version,)
                )
        connection.close()
