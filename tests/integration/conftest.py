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
from stack_support import (
    LocalStackConfigError,
    LocalStackHttp,
    MissingLocalStackConfig,
    build_local_stack,
    load_local_env,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = REPO_ROOT / ".env.supabase.local"


@pytest.fixture(scope="session")
def local_stack():
    """Validate every target before allowing a network client to be created.

    An existing .env.supabase.local supplies the entire configuration. Only
    when that file is absent do we use process variables; the two never mix.
    """
    try:
        return build_local_stack(load_local_env(ENV_FILE), os.environ)
    except MissingLocalStackConfig as exc:
        pytest.skip(str(exc))
    except LocalStackConfigError as exc:
        pytest.fail(str(exc), pytrace=False)


@pytest.fixture(scope="session")
def local_http(local_stack):
    """Use a session with proxy discovery and redirects disabled."""
    requests = pytest.importorskip("requests")
    with requests.Session() as session:
        yield LocalStackHttp(session)
