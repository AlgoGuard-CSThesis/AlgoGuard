"""Offline tests for the migration runner's decisions.

Everything here runs in the fast lane: no database, no psycopg2. The
runner's job is to REFUSE things — an edited migration, a missing file, a
duplicate version — and those refusals are the part worth testing without
a Docker stack in the loop.
"""

import re
from pathlib import Path

import pytest

import cloud_migrate
from cloud_migrate import (
    AppliedMigration,
    MigrationError,
    build_plan,
    checksum_bytes,
    discover_migrations,
    validate_timeout,
)


def write(directory: Path, name: str, body: str = "select 1;\n") -> Path:
    path = directory / name
    path.write_text(body, encoding="utf-8", newline="")
    return path


def applied_from(migration, checksum=None):
    return AppliedMigration(
        migration.version, migration.name, checksum or migration.checksum
    )


# ---------------------------------------------------------------------
# Checksums
# ---------------------------------------------------------------------


def test_checksum_ignores_line_endings_and_trailing_blank_lines():
    """A Windows checkout must not look like a tampered migration."""
    unix = b"create table t ();\nselect 1;\n"
    windows = b"create table t ();\r\nselect 1;\r\n\r\n"
    assert checksum_bytes(unix) == checksum_bytes(windows)


def test_checksum_changes_when_sql_changes():
    assert checksum_bytes(b"select 1;\n") != checksum_bytes(b"select 2;\n")


# ---------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------


def test_discover_returns_version_order(tmp_path):
    write(tmp_path, "20260922020000_second.sql")
    write(tmp_path, "20260922010000_first.sql")
    versions = [m.version for m in discover_migrations(tmp_path)]
    assert versions == ["20260922010000", "20260922020000"]


def test_discover_ignores_non_sql_files(tmp_path):
    write(tmp_path, "20260922010000_first.sql")
    (tmp_path / "README.md").write_text("notes", encoding="utf-8")
    assert len(discover_migrations(tmp_path)) == 1


@pytest.mark.parametrize(
    "name",
    [
        "0001_first.sql",  # not a CLI timestamp
        "20260922010000-first.sql",  # wrong separator
        "20260922010000_First.sql",  # uppercase
        "20260922010000.sql",  # no name
    ],
)
def test_discover_rejects_unrecognised_filenames(tmp_path, name):
    write(tmp_path, name)
    with pytest.raises(MigrationError, match="valid migration filename"):
        discover_migrations(tmp_path)


def test_discover_rejects_duplicate_versions(tmp_path):
    write(tmp_path, "20260922010000_one.sql")
    write(tmp_path, "20260922010000_two.sql")
    with pytest.raises(MigrationError, match="share version"):
        discover_migrations(tmp_path)


def test_discover_rejects_missing_directory(tmp_path):
    with pytest.raises(MigrationError, match="No migrations directory"):
        discover_migrations(tmp_path / "nope")


# ---------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------


def test_plan_lists_everything_as_pending_on_an_empty_database(tmp_path):
    write(tmp_path, "20260922010000_first.sql")
    write(tmp_path, "20260922020000_second.sql")
    plan = build_plan(discover_migrations(tmp_path), {})
    assert [m.name for m in plan.pending] == ["first", "second"]
    assert plan.problems == ()


def test_plan_skips_applied_migrations(tmp_path):
    write(tmp_path, "20260922010000_first.sql")
    write(tmp_path, "20260922020000_second.sql")
    migrations = discover_migrations(tmp_path)
    applied = {migrations[0].version: applied_from(migrations[0])}
    plan = build_plan(migrations, applied)
    assert [m.name for m in plan.pending] == ["second"]
    assert plan.problems == ()


def test_plan_refuses_a_migration_edited_after_it_was_applied(tmp_path):
    """The whole point of the checksum: silence here would mean the
    database no longer matches the file that claims to describe it."""
    write(tmp_path, "20260922010000_first.sql")
    migrations = discover_migrations(tmp_path)
    applied = {migrations[0].version: applied_from(migrations[0], "0" * 64)}

    plan = build_plan(migrations, applied)

    assert plan.pending == ()
    assert any("changed after it was applied" in p for p in plan.problems)


def test_plan_reports_an_applied_migration_whose_file_vanished(tmp_path):
    write(tmp_path, "20260922020000_second.sql")
    migrations = discover_migrations(tmp_path)
    applied = {"20260922010000": AppliedMigration("20260922010000", "first", "a" * 64)}

    plan = build_plan(migrations, applied)

    assert any("its file is gone" in p for p in plan.problems)


def test_plan_blocks_a_pending_migration_older_than_the_newest_applied(tmp_path):
    """Two branches each adding a migration is how a schema quietly ends
    up in an order nobody tested."""
    write(tmp_path, "20260922010000_older.sql")
    write(tmp_path, "20260922020000_newer.sql")
    migrations = discover_migrations(tmp_path)
    applied = {migrations[1].version: applied_from(migrations[1])}

    plan = build_plan(migrations, applied)
    assert any("older than the newest applied" in p for p in plan.problems)

    allowed = build_plan(migrations, applied, allow_out_of_order=True)
    assert [m.name for m in allowed.pending] == ["older"]
    assert allowed.problems == ()


# ---------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------


@pytest.mark.parametrize("value", ["30s", "500ms", "5min", "0s"])
def test_validate_timeout_accepts_libpq_style_values(value):
    assert validate_timeout(value, "--lock-timeout") == value


@pytest.mark.parametrize("value", ["30 s", "forever", "1h", "30s; drop table t"])
def test_validate_timeout_rejects_anything_else(value):
    # These are interpolated into SET LOCAL, which takes no bind
    # parameters, so the validation is the only thing standing there.
    with pytest.raises(MigrationError):
        validate_timeout(value, "--lock-timeout")


def test_module_does_not_import_psycopg2_at_module_level():
    """An analyst clone has requirements-dev.txt only. `pytest -q` imports
    this module during collection and must not fail on a missing driver."""
    assert not hasattr(cloud_migrate, "psycopg2")


def test_an_unparseable_dsn_counts_as_remote():
    """Fail toward asking for confirmation, never away from it."""
    assert cloud_migrate.is_remote("this is not a dsn") is True


def strip_sql_comments(sql: str) -> str:
    """So a guard does not trip over the comment that explains it."""
    without_block = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    return re.sub(r"--[^\n]*", " ", without_block)


def test_repository_migrations_are_all_well_formed():
    """The real supabase/migrations directory, not a fixture.

    Both rules exist because the runner wraps one file in one
    transaction: an inner COMMIT would defeat rollback-on-failure, and
    CREATE INDEX CONCURRENTLY cannot run inside a transaction at all.
    """
    migrations = discover_migrations()
    assert migrations, "no migrations found in supabase/migrations"
    assert len(migrations) == len({m.version for m in migrations})

    for migration in migrations:
        statements = strip_sql_comments(migration.sql).lower()
        assert not re.search(r"\b(begin|commit|rollback)\s*;", statements), (
            f"{migration.path.name} contains transaction control; the runner "
            "wraps each file in one transaction already."
        )
        assert "concurrently" not in statements, (
            f"{migration.path.name} uses CONCURRENTLY, which cannot run "
            "inside the runner's transaction."
        )
