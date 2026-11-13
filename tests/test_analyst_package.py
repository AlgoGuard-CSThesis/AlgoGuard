"""Stage 6D.1: cloud mode by default, setup help, and the analyst package's contents."""

import os
import subprocess
import sys
import zipfile

import pytest

import config
from scripts import build_analyst_package as package

MAINTAINER_ONLY = (
    "train.py", "migrate.py", "publish_model.py", "cloud_migrate.py", "bootstrap_admin.py",
    "cloud_recovery.py", "cleanup_model_objects.py", "legacy_backup.py", "legacy_import.py",
    "maintainer_env.py", "check_db_connection.py", "reset_password.py",
    ".env.maintainer.example", "requirements-maintainer.txt",
    "services/training_service.py", "services/evaluation_service.py",
)


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    folder = tmp_path_factory.mktemp("package")
    path = package.build(folder / "AlgoGuard-analyst.zip", worktree=True)
    with zipfile.ZipFile(path) as archive:
        archive.extractall(folder)
        names = {name.removeprefix("AlgoGuard/") for name in archive.namelist()}
    return folder / "AlgoGuard", names


def _clean_env(**extra):
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("ALGOGUARD_", "NEXT_PUBLIC_"))}
    return {**env, **extra}


def test_cloud_mode_is_the_default_and_sqlite_needs_asking_for():
    unset = config.load_config({})
    assert unset.db_mode == "supabase" and not unset.cloud_configured
    configured = config.load_config({
        "NEXT_PUBLIC_SUPABASE_URL": "https://project.supabase.co",
        "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY": "sb_publishable_abc",
    })
    assert configured.db_mode == "supabase" and configured.cloud_configured
    assert config.load_config({"ALGOGUARD_DB_MODE": "sqlite"}).db_mode == "sqlite"
    # Half a cloud configuration is a mistake to report, not an unconfigured installation.
    with pytest.raises(config.ConfigError, match="requires both"):
        config.load_config({"NEXT_PUBLIC_SUPABASE_URL": "https://project.supabase.co"})
    with pytest.raises(config.ConfigError, match="publishable key"):
        config.load_config({"NEXT_PUBLIC_SUPABASE_URL": "https://project.supabase.co",
                            "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY": "sb_secret_abc"})


def test_the_package_holds_the_analyst_application_only(built):
    folder, names = built
    for required in ("app.py", "cloud_app.py", "config.py", "services/capture_worker.py",
                     "services/inference_worker.py", "templates/monitor.html",
                     "static/css/style.css", "datasets/algoguard_big.csv", ".env.example",
                     "SETUP_ALGOGUARD.cmd", "START_ALGOGUARD.cmd", "requirements.txt",
                     "requirements-model.lock", "docs/ANALYST_GUIDE.md",
                     "docs/MODEL_TRAINING.md", "ALGOGUARD_USER_MANUAL.txt", "BUILD"):
        assert required in names, required
    assert not names & set(MAINTAINER_ONLY)
    assert not [name for name in names if name.startswith(
        ("tests/", "test-results/", "supabase/", "research/", "scripts/", "docs/migration/",
         "docs/testing/", "database/", "saved_models/", "reports/", ".github/"))]
    assert (folder / "BUILD").read_text().startswith("0.6.0+")
    for script in ("SETUP_ALGOGUARD.cmd", "START_ALGOGUARD.cmd"):
        content = (folder / script).read_bytes()
        assert b"\r\n" in content and b"\n" not in content.replace(b"\r\n", b"")


def test_the_package_starts_only_once_it_is_set_up(built, tmp_path):
    folder, _ = built
    check = subprocess.run([sys.executable, "config.py"], cwd=folder, env=_clean_env(),
                           capture_output=True, text=True, timeout=60)
    assert check.returncode == 3 and "Cloud settings missing" in check.stdout
    start = subprocess.run([sys.executable, "app.py"], cwd=folder, env=_clean_env(),
                           capture_output=True, text=True, timeout=120)
    assert start.returncode == 1
    assert "AlgoGuard cannot start" in start.stderr and "SETUP_ALGOGUARD.cmd" in start.stderr
    assert "Traceback" not in start.stderr

    (folder / ".env").write_text(
        "NEXT_PUBLIC_SUPABASE_URL=http://127.0.0.1:9\n"
        "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=sb_publishable_test\n"
        f"ALGOGUARD_STATE_DIR={tmp_path / 'state'}\n", encoding="utf-8")
    try:
        script = ("import app, app_version, services.capture_worker, "
                  "services.inference_worker; print(app.app.config['CLOUD_MODE']); "
                  "print(app_version.app_version())")
        started = subprocess.run([sys.executable, "-c", script], cwd=folder, env=_clean_env(),
                                 capture_output=True, text=True, timeout=120)
        assert started.returncode == 0, started.stderr[-2000:]
        cloud_mode, version = started.stdout.split()
        assert cloud_mode == "True" and version == (folder / "BUILD").read_text().strip()
        assert not (folder / "database").exists()
    finally:
        (folder / ".env").unlink()


def test_the_package_check_refuses_secrets_addresses_and_missing_modules():
    modules = {"app.py", "train.py"}
    clean = {"app.py": b"import os\n", "README.md": b"Write to admin@example.com.\n"}
    package.check_contents(clean, modules)
    for name, content, problem in (
        (".env", b"X=1\n", "file type"),
        ("database/algoguard.sqlite3", b"", "file type"),
        ("notes.md", b"key sb_secret_abcdefghijkl\n", "secret key"),
        ("notes.md", b"postgresql://postgres:hunter2@db.example.com:5432/postgres\n",
         "connection string"),
        ("notes.md", b"DATABASE_URL=postgresql://localhost\n", "secret setting"),
        ("notes.md", b"-----BEGIN EC PRIVATE KEY-----\n", "private key"),
        ("notes.md", b"Ask person@gmail.com\n", "e-mail address"),
        ("app.py", b"import train\n", "not in the package"),
    ):
        with pytest.raises(package.PackageError, match=problem):
            package.check_contents({**clean, name: content}, modules)
    # Documented placeholders are not secrets.
    package.check_contents(
        {**clean, "notes.md": b"postgresql://user:PASSWORD@host and icon@2x.png\n"}, modules)
