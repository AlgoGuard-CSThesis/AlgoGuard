"""Maintainer credential loading stays separate from analyst configuration."""

import sys

import pytest

import maintainer_env


@pytest.fixture(autouse=True)
def isolate_libpq_environment(monkeypatch):
    for name in ("ALGOGUARD_DB_SSLMODE", "PGHOST", "PGHOSTADDR", "PGSERVICE"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize(
    ("connection_string", "expected"),
    [
        ("postgresql://admin:synthetic@localhost:54322/postgres", "prefer"),
        ("postgresql://admin:synthetic@[::1]:54322/postgres", "prefer"),
        ("host=127.0.0.1 port=54322 password=synthetic", "prefer"),
        ("host='localhost' password='synthetic secret'", "prefer"),
        ("postgresql://admin:synthetic@database.example.test/postgres", "require"),
        ("host=database.example.test password=synthetic", "require"),
        ("host=localhost host=database.example.test", "require"),
        ("host=localhost hostaddr=192.0.2.12", "require"),
        ("password='synthetic host=localhost' host=database.example.test", "require"),
        ("postgresql://localhost/postgres?host=database.example.test", "require"),
        ("postgresql://localhost/postgres?hostaddr=192.0.2.12", "require"),
        ("host=localhost,database.example.test", "require"),
        ("service=synthetic host=localhost", "require"),
        ("dbname=postgres", "require"),
        ("postgresql://[malformed", "require"),
    ],
)
def test_tls_defaults_depend_on_target(monkeypatch, connection_string, expected):
    pytest.importorskip("psycopg2")
    monkeypatch.delenv("ALGOGUARD_DB_SSLMODE", raising=False)
    assert maintainer_env.resolve_sslmode(connection_string) == expected


def test_environment_address_override_requires_tls(monkeypatch):
    pytest.importorskip("psycopg2")
    monkeypatch.setenv("PGHOSTADDR", "192.0.2.12")
    assert maintainer_env.resolve_sslmode("host=localhost") == "require"


def test_missing_driver_requires_tls_conservatively(monkeypatch):
    monkeypatch.setitem(sys.modules, "psycopg2", None)
    assert maintainer_env.resolve_sslmode("host=localhost") == "require"


def test_tls_override_is_explicit_and_validated(monkeypatch):
    monkeypatch.setenv("ALGOGUARD_DB_SSLMODE", " verify-full ")
    assert maintainer_env.resolve_sslmode("host=localhost") == "verify-full"
    monkeypatch.setenv("ALGOGUARD_DB_SSLMODE", "typo")
    with pytest.raises(SystemExit, match="1"):
        maintainer_env.resolve_sslmode("host=localhost")


def test_maintainer_file_is_the_only_dotenv_source(tmp_path, monkeypatch):
    pytest.importorskip("dotenv")
    analyst_file = tmp_path / ".env"
    analyst_file.write_text("DATABASE_URL=analyst-must-not-be-loaded\n", encoding="utf-8")
    maintainer_file = tmp_path / ".env.maintainer"
    maintainer_file.write_text("DATABASE_URL=maintainer-test-value\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(maintainer_env, "MAINTAINER_ENV_FILE", maintainer_file)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert maintainer_env.require_database_url() == "maintainer-test-value"


def test_exported_credentials_win_over_maintainer_file(tmp_path, monkeypatch):
    pytest.importorskip("dotenv")
    maintainer_file = tmp_path / ".env.maintainer"
    maintainer_file.write_text("DATABASE_URL=maintainer-test-value\n", encoding="utf-8")
    monkeypatch.setattr(maintainer_env, "MAINTAINER_ENV_FILE", maintainer_file)
    monkeypatch.setenv("DATABASE_URL", "exported-test-value")
    assert maintainer_env.require_database_url() == "exported-test-value"


def test_missing_maintainer_file_does_not_fall_back_to_analyst(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("DATABASE_URL=analyst-must-not-be-loaded\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(maintainer_env, "MAINTAINER_ENV_FILE", tmp_path / ".env.maintainer")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(SystemExit, match="1"):
        maintainer_env.require_database_url()


def test_safe_is_the_shared_public_redactor():
    from redaction import redact_for_logging

    assert maintainer_env.safe is redact_for_logging
