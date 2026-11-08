"""The installation's version, reported with node health (Stage 6B.2)."""

import subprocess
from functools import lru_cache
from pathlib import Path

RELEASE = "0.6.0"
ROOT = Path(__file__).resolve().parent
# Written by scripts/build_analyst_package.py into an analyst package, which has no git history.
BUILD_FILE = ROOT / "BUILD"


@lru_cache(maxsize=1)
def app_version():
    """A package's BUILD, else ``RELEASE`` plus a clone's git commit, e.g. ``0.6.0+9cda196``."""
    try:
        build = BUILD_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        build = ""
    if build:
        return build[:40]
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True,
            timeout=5, check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return RELEASE
    return f"{RELEASE}+{commit}" if commit else RELEASE
