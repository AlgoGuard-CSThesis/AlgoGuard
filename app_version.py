"""The installation's version, reported with node health (Stage 6B.2)."""

import subprocess
from functools import lru_cache
from pathlib import Path

RELEASE = "0.6.0"
ROOT = Path(__file__).resolve().parent


@lru_cache(maxsize=1)
def app_version():
    """``RELEASE`` plus the git commit when the installation is a clone, e.g. ``0.6.0+9cda196``."""
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True,
            timeout=5, check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return RELEASE
    return f"{RELEASE}+{commit}" if commit else RELEASE
