"""Build the analyst distribution of AlgoGuard as a zip (Stage 6D.1). Maintainer tool.

    python scripts\\build_analyst_package.py              # from the last commit (a release)
    python scripts\\build_analyst_package.py --worktree   # from the files as they are now

The package holds only what an analyst installation runs: the cloud application and the
modules it imports, its pages, the sample replays, the setup and start scripts, the analyst
settings template and the analyst guide. Everything else stays out by construction, because
files are chosen from an allow list: training and publishing tools, migrations, recovery and
import tools, maintainer settings, tests and test records, research notebooks, databases,
models, captures and every `.env` file.

The build then checks the zip and refuses to write it when any check fails: every file is on
the allow list, every project module it imports is inside it, no file name is a database,
model, capture or settings file, and no file contains a secret key, database connection
string, private key, signed token or an e-mail address other than documented placeholders.
"""

from __future__ import annotations

import argparse
import ast
import io
import re
import subprocess
import sys
import tarfile
import zipfile
from fnmatch import fnmatch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app_version import RELEASE  # noqa: E402

# Entry points of the analyst application: the web console and its two child processes.
ENTRY_MODULES = ("app.py", "services/capture_worker.py", "services/inference_worker.py")
# Everything else an installation needs besides Python modules.
SUPPORT_FILES = (
    ".env.example",
    "SETUP_ALGOGUARD.cmd",
    "START_ALGOGUARD.cmd",
    "requirements.txt",
    "requirements-model.lock",
    "docs/ANALYST_GUIDE.md",
    "captures/.gitkeep",
)
SUPPORT_PATTERNS = ("templates/*", "static/*", "datasets/*.csv")
FORBIDDEN_NAMES = (
    ".env", ".env.*", "*.sqlite3", "*.sqlite", "*.db", "*.joblib", "*.pkl", "*.pcap", "*.pcapng",
    "*.cap", "signing_keys.json", "*.pem", "*.key",
)
ALLOWED_EMAIL_DOMAINS = ("example.com", "example.org", "example.invalid", "algoguard.local")
SECRET_PATTERNS = {
    "secret key": re.compile(r"sb_secret_[A-Za-z0-9_-]{8,}"),
    "service role key": re.compile(r"service_role\W{0,4}[:=]\W{0,4}eyJ"),
    "signed token": re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    # A password in a connection string; documented placeholders (PASSWORD, ***, <...>) are fine.
    "database connection string": re.compile(
        r"postgres(?:ql)?://[^\s:/@]+:(?!(?:PASSWORD|password|\*+|<[^>@]*>|\{[^}@]*\})@)[^\s@]+@"),
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "secret setting": re.compile(
        r"^\s*(?:SUPABASE_SECRET_KEY|SUPABASE_SERVICE_ROLE_KEY|DATABASE_URL|"
        r"SUPABASE_DB_PASSWORD)\s*=\s*\S", re.MULTILINE),
}
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@((?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,})\b")
# Asset names such as icon@2x.png look like addresses; real top-level domains are not these.
NOT_DOMAINS = (".png", ".svg", ".css", ".js", ".woff", ".woff2", ".map")


class PackageError(RuntimeError):
    """The package would contain something an analyst installation must not have."""


def _git(*args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, check=True).stdout


def _imports(source: str, path: str) -> set[str]:
    """Every module name a file imports, including imports inside functions."""
    package = ".".join(Path(path).with_suffix("").parts[:-1])
    names = set()
    for node in ast.walk(ast.parse(source, filename=path)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                base = ".".join(filter(None, [package, base]))
            names.add(base)
            names.update(f"{base}.{alias.name}" for alias in node.names)
    return names


def _module_path(name: str, available: set[str]) -> str | None:
    parts = name.split(".")
    for candidate in ("/".join(parts) + ".py", "/".join(parts) + "/__init__.py"):
        if candidate in available:
            return candidate
    return None


def module_closure(read, available: set[str]) -> set[str]:
    """The project modules reachable from the analyst entry points."""
    seen: set[str] = set()
    queue: list[str] = list(ENTRY_MODULES)
    while queue:
        path = queue.pop()
        if path in seen:
            continue
        seen.add(path)
        for name in _imports(read(path).decode("utf-8"), path):
            found = _module_path(name, available)
            if found and found not in seen:
                queue.append(found)
    return seen


def select_files(available: set[str], read) -> list[str]:
    """The allow-listed files of the analyst package."""
    missing = [name for name in (*ENTRY_MODULES, *SUPPORT_FILES) if name not in available]
    if missing:
        raise PackageError(f"Required files are missing: {', '.join(missing)}")
    chosen = module_closure(read, available) | set(SUPPORT_FILES)
    chosen |= {name for name in available
               if any(fnmatch(name, pattern) for pattern in SUPPORT_PATTERNS)}
    return sorted(chosen)


def check_contents(files: dict[str, bytes], project_modules: set[str]) -> None:
    """Refuse forbidden file names, secrets, real e-mail addresses and missing imports."""
    problems = []
    for name, content in files.items():
        base = name.rsplit("/", 1)[-1]
        if base != ".env.example" and any(fnmatch(base, pattern) for pattern in FORBIDDEN_NAMES):
            problems.append(f"{name}: file type not allowed in an analyst package")
            continue
        text = content.decode("utf-8", errors="replace")
        for label, pattern in SECRET_PATTERNS.items():
            if pattern.search(text):
                problems.append(f"{name}: contains a {label}")
        for match in EMAIL.finditer(text):
            domain = match.group(1).lower()
            if not domain.endswith(ALLOWED_EMAIL_DOMAINS + NOT_DOMAINS):
                problems.append(f"{name}: contains an e-mail address at {domain}")
    packaged = {name for name in files if name.endswith(".py")}
    for name in sorted(packaged):
        for imported in _imports(files[name].decode("utf-8"), name):
            if _module_path(imported, project_modules) and not _module_path(imported, packaged):
                problems.append(f"{name}: imports {imported}, which is not in the package")
    if problems:
        raise PackageError("The analyst package failed its checks:\n  " + "\n  ".join(problems))


def _normalise(name: str, content: bytes) -> bytes:
    if name.endswith(".cmd"):  # cmd.exe needs Windows line endings
        return content.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    return content


def build(output: Path, *, worktree: bool = False) -> Path:
    """Write the package zip and return its path."""
    blobs: dict[str, bytes] = {}
    if worktree:
        listed = _git("ls-files", "--cached", "--others", "--exclude-standard").decode()
        available = {name for name in listed.splitlines() if (ROOT / name).is_file()}
        build_id = f"{RELEASE}+worktree"
    else:
        listed = _git("ls-tree", "-r", "--name-only", "HEAD").decode()
        available = set(listed.splitlines())
        with tarfile.open(fileobj=io.BytesIO(_git("archive", "--format=tar", "HEAD"))) as archive:
            for member in archive.getmembers():
                stream = archive.extractfile(member) if member.isfile() else None
                if stream is not None:
                    blobs[member.name] = stream.read()
        build_id = f"{RELEASE}+{_git('rev-parse', '--short', 'HEAD').decode().strip()}"

    def read(name: str) -> bytes:
        return (ROOT / name).read_bytes() if worktree else blobs[name]

    files = {name: _normalise(name, read(name)) for name in select_files(available, read)}
    files["BUILD"] = (build_id + "\n").encode()
    check_contents(files, {name for name in available if name.endswith(".py")})
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".part")
    with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as package:
        for name, content in sorted(files.items()):
            package.writestr(f"AlgoGuard/{name}", content)
    temporary.replace(output)
    return output


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--worktree", action="store_true",
                        help="package the files as they are now instead of the last commit")
    parser.add_argument("--output", type=Path, help="zip path (default: dist/...)")
    args = parser.parse_args(argv)
    if not args.worktree and _git("status", "--porcelain").strip():
        print("Note: uncommitted changes are not packaged; the package is built from the last "
              "commit. Use --worktree to package the files as they are now.")
    output = args.output or ROOT / "dist" / f"AlgoGuard-analyst-{RELEASE}.zip"
    try:
        path = build(output, worktree=args.worktree)
    except PackageError as error:
        print(error)
        return 1
    with zipfile.ZipFile(path) as package:
        count = len(package.namelist())
        build_id = package.read("AlgoGuard/BUILD").decode().strip()
    print(f"Wrote {path} ({count} files, build {build_id}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
