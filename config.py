"""Centralized, typed application configuration for AlgoGuard.

Stage 5A.1: replaces scattered os.environ reads (previously in app.py,
database_service.py, deployment_service.py, train.py, and
traffic_source_service.py) with a single validated settings object, while
preserving today's exact local defaults and behavior.

Precedence (highest to lowest):
1. Explicit process environment variables (os.environ) at the time
   `load_config()` is called — this includes variables set by tests
   (see conftest.py) or shell exports. This always wins.
2. Values from the repository's `.env` file (via python-dotenv), if present.
   The file is read on each load without changing the process environment.
3. Built-in defaults matching today's hardcoded/fallback behavior.

Import-time caching:
`get_config()` returns a cached singleton (built once per process) so
call sites don't repeatedly reparse env vars. Call `reset_config_cache()`
before re-reading config if you've mutated os.environ after an earlier
import in the same process (e.g. in tests that change env vars between
cases). `load_config()` itself is never cached — call it directly if you
want a guaranteed-fresh read regardless of the singleton.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Mapping, Optional

from dotenv import dotenv_values

from redaction import redact_for_logging  # re-exported; see redaction.py

__all__ = [
    "AppConfig",
    "ConfigError",
    "get_config",
    "load_config",
    "redact_for_logging",
    "reset_config_cache",
]

# NOTE: this file is expected to live at the repo root, alongside app.py.
# BASE_DIR must resolve to the same directory that app.py / database_service.py
# / deployment_service.py currently compute as their repo-root BASE_DIR.
BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"


class ConfigError(RuntimeError):
    """Raised when configuration is present but invalid, as opposed to
    simply absent. Invalid configuration must fail loudly here rather than
    silently degrading to a default the operator did not ask for."""


_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})


def _raw(env: Mapping[str, str], name: str) -> Optional[str]:
    """Read a setting, treating a blank value as absent.

    `.env` files conventionally use `KEY=` to mean "not set" — our own
    `.env.example` ships `NEXT_PUBLIC_SUPABASE_URL=` exactly that way. Every
    getter routes through here so a blank value falls back to the documented
    default instead of propagating an empty string, which previously caused:

      * ALGOGUARD_ADMIN_PASSWORD= -> the first Administrator was created with
        an EMPTY password, and the "store this generated password" notice was
        suppressed because the value was not None
      * ALGOGUARD_HOST=           -> app.run(host="") binds every interface,
        contradicting the loopback-only default app.py documents
      * ALGOGUARD_DATABASE_PATH=  -> the database path resolved to the repo
        root directory rather than a file (same for the model artifact path)
      * ALGOGUARD_SECRET_KEY=     -> reported as non-ephemeral while actually
        falling back to a per-process random key
    """
    value = env.get(name)
    if value is None:
        return None
    return value if value.strip() else None


def _get_bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    """Parse a boolean setting, accepting the spellings people actually
    write in a `.env` file.

    Previously this accepted only the literal string "1", so a reasonable
    `ALGOGUARD_SECURE_COOKIES=true` silently evaluated to False. For a
    security-relevant flag that is the worst possible failure mode, so an
    unrecognized value is now a hard error rather than a silent False.
    """
    raw = _raw(env, name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    raise ConfigError(
        f"{name}={raw!r} is not a recognized boolean. "
        f"Use one of {sorted(_TRUE_VALUES)} or {sorted(_FALSE_VALUES)}, "
        "or leave it blank to use the default."
    )


def _get_int(
    env: Mapping[str, str],
    name: str,
    default: int,
    minimum: Optional[int] = None,
    maximum: Optional[int] = None,
) -> int:
    """Parse an integer setting, validating type and range.

    Previously a malformed value silently fell back to the default, which
    hid typos such as ALGOGUARD_PORT=500O.
    """
    raw = _raw(env, name)
    if raw is None:
        return default
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError) as error:
        raise ConfigError(f"{name}={raw!r} is not a valid integer.") from error
    if minimum is not None and value < minimum:
        raise ConfigError(f"{name}={value} is below the minimum of {minimum}.")
    if maximum is not None and value > maximum:
        raise ConfigError(f"{name}={value} is above the maximum of {maximum}.")
    return value


def _get_percentage(env: Mapping[str, str], name: str, default: float) -> float:
    """Matches deployment_service._quality_threshold behavior exactly:
    invalid/non-finite values fall back to the clamped default; valid
    values are clamped into [0, 100]."""
    fallback = min(max(default, 0.0), 100.0)
    raw = _raw(env, name)
    if raw is None:
        return fallback
    try:
        value = float(raw)
    except ValueError:
        return fallback
    if not math.isfinite(value):
        return fallback
    return min(max(value, 0.0), 100.0)


def _get_str(env: Mapping[str, str], name: str, default: str) -> str:
    """Return the configured string, or the default when absent or blank."""
    raw = _raw(env, name)
    return default if raw is None else raw


def _get_optional_str(env: Mapping[str, str], name: str) -> Optional[str]:
    """Return the configured string, or None when absent or blank."""
    return _raw(env, name)


@dataclass(frozen=True)
class AppConfig:
    # --- Flask / web ---
    secret_key: Optional[str]          # None => app.py generates an ephemeral one
    secret_key_ephemeral: bool
    secure_cookies: bool
    host: str
    port: int
    debug: bool

    # --- Filesystem paths ---
    base_dir: Path
    database_folder: Path
    database_path: Path
    saved_model_folder: Path
    active_model_path: Path
    report_folder: Path
    capture_folder: Path

    # --- Admin bootstrap (train.py / database_service.seed_default_admin) ---
    admin_username: str
    admin_email: str
    admin_password: Optional[str]      # None => a random password is generated

    # --- Deployment quality gates (deployment_service.py) ---
    min_stacking_accuracy: float
    min_stacking_f1: float
    min_stacking_roc_auc: float

    # --- Fixed, centralized constants (previously hardcoded in app.py) ---
    admin_roles: tuple = field(default=("Administrator", "Analyst"))

    # --- Cloud mode (Stage 5D+ analyst-facing switch; enforced starting 5A.2) ---
    db_mode: str = "sqlite"
    supabase_url: Optional[str] = None
    supabase_publishable_key: Optional[str] = None


def load_config(env: Optional[Mapping[str, str]] = None) -> AppConfig:
    """Build a validated AppConfig from the given environment mapping.

    Merge the repository's `.env` beneath `os.environ`. An explicit mapping
    bypasses both sources, giving tests a fully isolated configuration.
    Dotenv values are literal (no interpolation of other environment variables).
    """
    if env is None:
        file_values = dotenv_values(ENV_FILE, interpolate=False, encoding="utf-8-sig")
        env = {key: value for key, value in file_values.items() if value is not None}
        env.update(os.environ)

    db_mode = _get_str(env, "ALGOGUARD_DB_MODE", "sqlite").strip().lower()
    supabase_url = _get_optional_str(env, "NEXT_PUBLIC_SUPABASE_URL")
    supabase_publishable_key = _get_optional_str(env, "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY")

    if db_mode not in ("sqlite", "supabase"):
        raise ConfigError(
            f"ALGOGUARD_DB_MODE={db_mode!r} is not recognized. "
            "Use 'sqlite' or 'supabase'."
        )

    if db_mode == "supabase":
        # Deliberately a hard error, not a fallback to sqlite: silently
        # degrading to the legacy local database when cloud mode was
        # requested would violate Stage 5A.2's requirement that cloud mode
        # never quietly falls back to the legacy business database.
        if not supabase_url or not supabase_publishable_key:
            raise ConfigError(
                "ALGOGUARD_DB_MODE=supabase requires both NEXT_PUBLIC_SUPABASE_URL "
                "and NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY to be set. Refusing to "
                "silently fall back to the local SQLite database."
            )
        # No code path reads `db_mode` yet: the application still persists
        # exclusively to SQLite until Stage 5D wires the cloud repository
        # layer. Accepting this setting here and then running on SQLite
        # anyway would be exactly the silent fallback the check above
        # exists to prevent, so refuse until 5D lands.
        raise ConfigError(
            "ALGOGUARD_DB_MODE=supabase is not implemented yet. The cloud "
            "persistence path lands in Stage 5D; until then the application "
            "reads and writes local SQLite only. Set ALGOGUARD_DB_MODE=sqlite. "
            "(Configuration is validated now so the switch is ready to use.)"
        )

    default_database_path = BASE_DIR / "database" / "algoguard.sqlite3"
    database_path = Path(
        _get_str(env, "ALGOGUARD_DATABASE_PATH", str(default_database_path))
    ).resolve()
    saved_model_folder = Path(
        _get_str(env, "ALGOGUARD_SAVED_MODEL_FOLDER", str(BASE_DIR / "saved_models"))
    ).resolve()
    default_model_path = saved_model_folder / "deployed_model.joblib"

    secret_key = _get_optional_str(env, "ALGOGUARD_SECRET_KEY")

    return AppConfig(
        secret_key=secret_key,
        secret_key_ephemeral=secret_key is None,
        secure_cookies=_get_bool(env, "ALGOGUARD_SECURE_COOKIES", False),
        host=_get_str(env, "ALGOGUARD_HOST", "127.0.0.1"),
        port=_get_int(env, "ALGOGUARD_PORT", 5000, minimum=1, maximum=65535),
        debug=_get_bool(env, "FLASK_DEBUG", False),

        base_dir=BASE_DIR,
        database_folder=database_path.parent,
        database_path=database_path,
        saved_model_folder=saved_model_folder,
        active_model_path=Path(
            _get_str(env, "ALGOGUARD_DEPLOYED_MODEL_PATH", str(default_model_path))
        ).resolve(),
        report_folder=Path(
            _get_str(env, "ALGOGUARD_REPORT_FOLDER", str(BASE_DIR / "reports"))
        ).resolve(),
        capture_folder=Path(
            _get_str(env, "ALGOGUARD_CAPTURE_FOLDER", str(BASE_DIR / "captures"))
        ).resolve(),

        admin_username=_get_str(env, "ALGOGUARD_ADMIN_USERNAME", "admin").strip() or "admin",
        admin_email=_get_str(env, "ALGOGUARD_ADMIN_EMAIL", "admin@algoguard.local"),
        admin_password=_get_optional_str(env, "ALGOGUARD_ADMIN_PASSWORD"),

        min_stacking_accuracy=_get_percentage(env, "ALGOGUARD_MIN_STACKING_ACCURACY", 70.0),
        min_stacking_f1=_get_percentage(env, "ALGOGUARD_MIN_STACKING_F1", 70.0),
        min_stacking_roc_auc=_get_percentage(env, "ALGOGUARD_MIN_STACKING_ROC_AUC", 70.0),

        db_mode=db_mode,
        supabase_url=supabase_url,
        supabase_publishable_key=supabase_publishable_key,
    )


@lru_cache(maxsize=1)
def _cached_config() -> AppConfig:
    return load_config()


def get_config() -> AppConfig:
    """Return the process-wide cached config singleton. This is what
    application modules should call."""
    return _cached_config()


def reset_config_cache() -> None:
    """Clear the cached singleton.

    Call this in test setup after mutating os.environ, whenever a test
    needs config to reflect env changes made *after* config.py was first
    imported elsewhere in the same process. Without this, the cached
    singleton would silently keep serving values captured at first import
    — the exact "frozen fixture values at import" failure mode Stage 5A.1
    calls out.
    """
    _cached_config.cache_clear()
