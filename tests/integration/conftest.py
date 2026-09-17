"""
tests/integration/conftest.py

Loads local Supabase stack credentials from .env.supabase.local (generated
via `supabase status -o env > .env.supabase.local`). These tests require
the local stack to be running (`supabase start`) and are excluded from the
default fast unit-test run — invoke explicitly with `pytest -m integration`.

Never point these tests at a real cloud project. They create and delete
disposable data prefixed with `zz_migration_smoke_` / `algoguard.test+`
so it's always distinguishable from real application data, and clean up
after themselves.
"""
import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = REPO_ROOT / ".env.supabase.local"


def _load_env_file(path: Path) -> dict:
    values = {}
    if not path.exists():
        return values
    # PowerShell's `>` redirect can write UTF-16 (with a BOM) depending on
    # the PowerShell version, while Python's default read_text() assumes
    # UTF-8. Try UTF-8 first, fall back to UTF-16, so this works regardless
    # of which PowerShell version generated the file.
    try:
        raw_text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raw_text = path.read_text(encoding="utf-16")

    for line in raw_text.splitlines():
        line = line.strip().lstrip("\ufeff")  # also strip a stray UTF-8 BOM
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"')
    return values


_LOCAL_ENV = _load_env_file(ENV_FILE)


def _require(key: str) -> str:
    value = _LOCAL_ENV.get(key) or os.environ.get(key)
    if not value:
        pytest.skip(
            f"{key} not found. Run: "
            "supabase status -o env > .env.supabase.local"
        )
    return value


@pytest.fixture(scope="session")
def local_stack():
    """Bundle of local Supabase stack connection details."""
    return {
        "api_url": _require("API_URL"),
        "rest_url": _require("REST_URL"),
        "functions_url": _require("FUNCTIONS_URL"),
        "db_url": _require("DB_URL"),
        "publishable_key": _require("PUBLISHABLE_KEY"),
        "secret_key": _require("SECRET_KEY"),
        "anon_key": _require("ANON_KEY"),
        "service_role_key": _require("SERVICE_ROLE_KEY"),
        "storage_s3_url": _LOCAL_ENV.get("STORAGE_S3_URL") or os.environ.get("STORAGE_S3_URL", ""),
    }
