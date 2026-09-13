"""Centralized, typed application configuration for AlgoGuard.

Stage 5A.1: replaces scattered os.environ reads (previously in app.py,
database_service.py, deployment_service.py, train.py, and
traffic_source_service.py) with a single validated settings object, while
preserving today's exact local defaults and behavior.

Precedence (highest to lowest):
1. Explicit process environment variables (os.environ) at the time
   `load_config()` is called — this includes variables set by tests
   (see conftest.py) or shell exports. This always wins.
2. Values from a loaded `.env` file (via python-dotenv), if present —
   python-dotenv never overrides a variable already set in the real
   environment, so this naturally sits below (1).
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

try:
    from dotenv import load_dotenv
    load_dotenv()  # no-op if no .env file is found; never overrides existing env vars
except ImportError:
    pass

# NOTE: this file is expected to live at the repo root, alongside app.py.
# BASE_DIR must resolve to the same directory that app.py / database_service.py
# / deployment_service.py currently compute as their repo-root BASE_DIR.
BASE_DIR = Path(__file__).resolve().parent


def _get_bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name)
    if raw is None:
        return default
    return raw == "1"


def _get_int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _get_percentage(env: Mapping[str, str], name: str, default: float) -> float:
    """Matches deployment_service._quality_threshold behavior exactly:
    invalid/non-finite values fall back to the clamped default; valid
    values are clamped into [0, 100]."""
    fallback = min(max(default, 0.0), 100.0)
    raw = env.get(name)
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
    return env.get(name, default)


def _get_optional_str(env: Mapping[str, str], name: str) -> Optional[str]:
    return env.get(name)


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


class ConfigError(RuntimeError):
    """Raised when configuration is present but invalid, as opposed to
    simply absent. Cloud mode misconfiguration must fail loudly here rather
    than silently falling back to the legacy SQLite database."""


def load_config(env: Optional[Mapping[str, str]] = None) -> AppConfig:
    """Build a validated AppConfig from the given environment mapping.

    Defaults to `os.environ`. Pass an explicit mapping in tests if you want
    to build a config from something other than the live process env
    without touching os.environ at all.
    """
    env = os.environ if env is None else env

    db_mode = _get_str(env, "ALGOGUARD_DB_MODE", "sqlite").strip().lower()
    supabase_url = _get_optional_str(env, "NEXT_PUBLIC_SUPABASE_URL")
    supabase_publishable_key = _get_optional_str(env, "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY")

    if db_mode not in ("sqlite", "supabase"):
        raise ConfigError(
            f"ALGOGUARD_DB_MODE={db_mode!r} is not recognized. "
            "Use 'sqlite' or 'supabase'."
        )

    if db_mode == "supabase" and (not supabase_url or not supabase_publishable_key):
        # Deliberately a hard error, not a fallback to sqlite: silently
        # degrading to the legacy local database when cloud mode was
        # requested would violate Stage 5A.2's requirement that cloud mode
        # never quietly falls back to the legacy business database.
        raise ConfigError(
            "ALGOGUARD_DB_MODE=supabase requires both NEXT_PUBLIC_SUPABASE_URL "
            "and NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY to be set. Refusing to "
            "silently fall back to the local SQLite database."
        )

    database_folder = BASE_DIR / "database"
    default_database_path = database_folder / "algoguard.sqlite3"
    saved_model_folder = BASE_DIR / "saved_models"
    default_model_path = saved_model_folder / "deployed_model.joblib"

    secret_key = _get_optional_str(env, "ALGOGUARD_SECRET_KEY")

    return AppConfig(
        secret_key=secret_key,
        secret_key_ephemeral=secret_key is None,
        secure_cookies=_get_bool(env, "ALGOGUARD_SECURE_COOKIES", False),
        host=_get_str(env, "ALGOGUARD_HOST", "127.0.0.1"),
        port=_get_int(env, "ALGOGUARD_PORT", 5000),
        debug=_get_bool(env, "FLASK_DEBUG", False),

        base_dir=BASE_DIR,
        database_folder=database_folder,
        database_path=Path(
            _get_str(env, "ALGOGUARD_DATABASE_PATH", str(default_database_path))
        ).resolve(),
        saved_model_folder=saved_model_folder,
        active_model_path=Path(
            _get_str(env, "ALGOGUARD_DEPLOYED_MODEL_PATH", str(default_model_path))
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


_REDACT_KEYWORDS = ("key", "secret", "password", "token", "signature")


def redact_for_logging(text: str) -> str:
    """Best-effort redaction of secrets, tokens, and signed URL query
    parameters before writing a string to any log. Not a substitute for
    simply not logging privileged values in the first place — use this as
    a defense-in-depth backstop in maintainer tooling (5B/5C/5D), where
    connection strings and signed download URLs are more likely to appear
    in error messages.
    """
    import re

    # postgresql://user:PASSWORD@host -> postgresql://user:***@host
    text = re.sub(r"(://[^:/@]+:)[^@]+(@)", r"\1***\2", text)
    # query params like ?token=... or &signature=... -> redacted value
    text = re.sub(
        r"([?&](?:" + "|".join(_REDACT_KEYWORDS) + r")[^=]*=)[^&\s]+",
        r"\1***",
        text,
        flags=re.IGNORECASE,
    )
    return text


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
