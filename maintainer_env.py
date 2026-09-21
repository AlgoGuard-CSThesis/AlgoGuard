"""Credential loading for MAINTAINER-ONLY tooling.

Stage 5A.2 requires privileged tools to load a separate credential source
from the analyst application, and requires that maintainer credentials never
enter analyst configuration.

Before this module existed, `check_db_connection.py` and `measure_latency.py`
both called a bare `load_dotenv()`, which loads the *analyst* `.env`. That
meant `DATABASE_URL` — a maintainer credential, documented as such in
`.env.maintainer.example` — had to be written into the analyst file for those
scripts to work, contradicting the split the stage delivers.

This module loads `.env.maintainer` explicitly and never touches `.env`.
The Flask application must never import it.

Usage:
    from maintainer_env import require_database_url, resolve_sslmode, safe

    dsn = require_database_url()
    conn = psycopg2.connect(dsn, sslmode=resolve_sslmode(dsn))
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

from redaction import redact_for_logging as safe

__all__ = [
    "describe_target",
    "load_maintainer_env",
    "require_database_url",
    "resolve_sslmode",
    "safe",
]

REPO_ROOT = Path(__file__).resolve().parent
MAINTAINER_ENV_FILE = REPO_ROOT / ".env.maintainer"

# Hosts where TLS is genuinely unavailable: the local Supabase stack's
# Postgres container does not serve TLS at all.
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0", "host.docker.internal"})

# Every mode libpq accepts, so an explicit override is validated rather than
# passed blindly to psycopg2.
_VALID_SSLMODES = frozenset(
    {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}
)


def load_maintainer_env() -> bool:
    """Load `.env.maintainer` into os.environ. Returns True if a file was read.

    Deliberately does NOT fall back to `.env`: analyst configuration must
    never be a source of privileged credentials. Existing process
    environment variables always win, so CI and shell exports still work.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return False
    if not MAINTAINER_ENV_FILE.exists():
        return False
    return bool(load_dotenv(dotenv_path=MAINTAINER_ENV_FILE, override=False))


def require_database_url() -> str:
    """Return DATABASE_URL from maintainer configuration, or exit clearly."""
    load_maintainer_env()
    connection_string = os.environ.get("DATABASE_URL")
    if not connection_string:
        print(
            "DATABASE_URL is not set.\n"
            f"Add it to {MAINTAINER_ENV_FILE.name} (copy .env.maintainer.example), "
            "or export it in your shell.\n"
            "Do NOT put it in .env — that file ships to analyst installations.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    return connection_string


def _extract_host(connection_string: str) -> str:
    """Resolve the effective host from either libpq connection-string form.

    Use the driver's parser so quoted password contents, duplicate host
    options and URL query overrides cannot disguise a remote target as local.
    A hostaddr option controls the connection address ahead of host, and its
    environment default can apply even when host was supplied explicitly.

    Return "" for missing drivers, malformed DSNs or service-file settings
    whose defaults are unknown here. Callers then require TLS conservatively.
    """
    try:
        from psycopg2 import Error
        from psycopg2.extensions import parse_dsn
    except ImportError:
        return ""
    try:
        options = parse_dsn(connection_string or "")
    except (Error, TypeError, ValueError):
        return ""
    if options.get("service") or os.environ.get("PGSERVICE"):
        return ""
    return (
        options.get("hostaddr")
        or os.environ.get("PGHOSTADDR")
        or options.get("host")
        or os.environ.get("PGHOST")
        or ""
    ).lower()


def resolve_sslmode(connection_string: str) -> str:
    """Choose an explicit libpq sslmode for this target.

    Stage 5A.3 requires explicit TLS and connection-mode settings. The
    previous hardcoded `sslmode="prefer"` was chosen to work against the
    local Docker stack, which serves no TLS — but `prefer` means "try TLS,
    and silently continue in plaintext if the server declines". Applied to
    the cloud pilot over the public internet, that would put a database
    password on the wire in the clear whenever negotiation failed.

    So: `prefer` for local hosts only, `require` everywhere else.
    `ALGOGUARD_DB_SSLMODE` overrides both, for maintainers who want
    `verify-full` with a pinned root certificate.
    """
    override = os.environ.get("ALGOGUARD_DB_SSLMODE")
    if override:
        normalized = override.strip().lower()
        if normalized not in _VALID_SSLMODES:
            print(
                f"ALGOGUARD_DB_SSLMODE={override!r} is not a valid libpq sslmode. "
                f"Use one of: {', '.join(sorted(_VALID_SSLMODES))}.",
                file=sys.stderr,
            )
            raise SystemExit(1)
        return normalized

    # An unparseable DSN yields "", which is not in _LOCAL_HOSTS and so
    # resolves to `require`. Fail toward demanding TLS, never away from it.
    return "prefer" if _extract_host(connection_string) in _LOCAL_HOSTS else "require"


def describe_target(connection_string: str) -> str:
    """A human-readable description of the target. Never raises, and never
    includes the password: this is printed before the connection attempt,
    and a typo in the DSN must produce a useful line, not a traceback."""
    host = _extract_host(connection_string) or "unknown host"
    port = None
    if "://" in (connection_string or ""):
        try:
            port = urlsplit(connection_string).port
        except ValueError:
            port = None  # malformed port; the driver will report it properly
    where = f"{host}:{port}" if port else host
    return f"{where} (sslmode={resolve_sslmode(connection_string)})"
