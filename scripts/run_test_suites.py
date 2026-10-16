"""Run every automated AlgoGuard check and save the evidence (cards UNT-01, ENV-02, INT-01, SEC-01).

Runs, in order, and records each tool's full output, exit code and duration:
  1. Offline Python tests        python -m pytest -q
  2. Lint                         python -m ruff check .
  3. Type check                   npm run typecheck   (Pyright)
  4. Browser polling tests        node --test tests/monitor_poll.test.cjs
  5. Integration tests (optional) python -m pytest -q -m integration   (needs `supabase start`)
  6. Dependency scan (optional)   python -m pip_audit -r requirements.txt
  7. Static security scan (opt.)  python -m bandit on the application code only

Usage (from the repository folder, inside the venv):
    python scripts/run_test_suites.py                  # 1-4
    python scripts/run_test_suites.py --integration    # also 5 (local Supabase stack running)
    python scripts/run_test_suites.py --security       # also 6-7 (pip install pip-audit bandit)
    python scripts/run_test_suites.py --all

Set RUN_WINDOWS_CAPTURE_TESTS=1 beforehand to include the Npcap capture tests.
Results go to test-results/suites_<timestamp>/ (summary.md plus one log per step).
"""
from __future__ import annotations

import argparse
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
SECRET = re.compile(
    r"(sb_secret_[A-Za-z0-9_-]+|service_role[^\s\"']*"
    r"|eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+|postgres(ql)?://[^\s]+)")
MISSING_TOOL = re.compile(r"No module named (pytest|ruff|pip_audit|bandit)\b")
KEY_LINES = {
    "pytest": r"(\d+ (passed|failed|error)|no tests ran)",
    "ruff": r"(All checks passed|Found \d+ error)",
    "pyright": r"\d+ errors?, \d+ warnings?",
    "node": r"^# (pass|fail) ",
    "pip-audit": r"(No known vulnerabilities|Found \d+ known vulnerabilit)",
    "bandit": r"^(Low|Medium|High): \d+",
}


def bandit_targets() -> list[str]:
    """Application code only: top-level modules, services and scripts (no tests or backups)."""
    files = sorted(p.name for p in ROOT.glob("*.py"))
    return files + [d for d in ("services", "scripts") if (ROOT / d).is_dir()]


def run(cmd: list[str], timeout: int) -> tuple[int, str, float]:
    start = time.perf_counter()
    exe = shutil.which(cmd[0]) or cmd[0]
    try:
        proc = subprocess.run([exe, *cmd[1:]], cwd=ROOT, capture_output=True, text=True,
                              timeout=timeout)
        code, out = proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except FileNotFoundError:
        code, out = 127, f"Command not found: {cmd[0]}"
    except subprocess.TimeoutExpired as error:
        code, out = 124, f"Timed out after {timeout}s\n{error.stdout or ''}{error.stderr or ''}"
    return code, SECRET.sub("<redacted>", out), time.perf_counter() - start


def key_line(name: str, out: str) -> str:
    """Pull the line(s) that tell the reader the result."""
    lines = [line.strip() for line in out.strip().splitlines() if line.strip()]
    for key, pattern in KEY_LINES.items():
        if key in name:
            hits = [line for line in lines if re.search(pattern, line)]
            if hits:
                limit = 3 if key == "bandit" else 2
                return " / ".join(hits[-limit:])[:200]
    return (lines[-1] if lines else "(no output)")[:200]


def build_steps(args: argparse.Namespace) -> list[tuple[str, list[str], int]]:
    steps = [
        ("pytest-offline", [PY, "-m", "pytest", "-q"], 1800),
        ("ruff-lint", [PY, "-m", "ruff", "check", "."], 300),
        ("pyright-typecheck", ["npm", "run", "typecheck"], 600),
        ("node-monitor-poll", ["node", "--test", "tests/monitor_poll.test.cjs"], 300),
    ]
    if args.integration or args.all:
        steps.append(("pytest-integration", [PY, "-m", "pytest", "-q", "-m", "integration"], 3600))
    if args.security or args.all:
        steps.append(("pip-audit", [PY, "-m", "pip_audit", "-r", "requirements.txt"], 900))
        steps.append(("bandit", [PY, "-m", "bandit", "-r", *bandit_targets()], 900))
    return steps


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--integration", action="store_true")
    parser.add_argument("--security", action="store_true")
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = ROOT / "test-results" / f"suites_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    _, commit, _ = run(["git", "rev-parse", "--short", "HEAD"], 30)
    _, dirty, _ = run(["git", "status", "--porcelain"], 30)
    suffix = " (with uncommitted changes)" if dirty.strip() else ""
    report = [
        f"# Automated test run — {datetime.now().isoformat(timespec='seconds')}", "",
        f"- Commit: `{commit.strip()}`{suffix}",
        f"- Machine: {platform.node()} · {platform.platform()} · "
        f"Python {platform.python_version()}",
        f"- RUN_WINDOWS_CAPTURE_TESTS={os.environ.get('RUN_WINDOWS_CAPTURE_TESTS', '0')}",
        "", "| Step | Result | Exit code | Duration | Key line |", "|---|---|---|---|---|",
    ]

    overall = 0
    for name, cmd, timeout in build_steps(args):
        print(f"Running {name} ...", flush=True)
        code, out, secs = run(cmd, timeout)
        if code != 0 and MISSING_TOOL.search(out):
            code = 127
        (out_dir / f"{name}.log").write_text(f"$ {' '.join(cmd)}\n\n{out}", encoding="utf-8")
        result = "PASS" if code == 0 else ("NOT RUN" if code == 127 else "FAIL")
        if result == "FAIL":
            overall = 1
        key = key_line(name, out).replace("|", "/")
        report.append(f"| {name} | {result} | {code} | {secs:.1f} s | {key} |")
        print(f"  {result} ({secs:.1f}s) {key}", flush=True)

    report += ["", "Full output for each step is in the matching .log file in this folder.",
               "NOT RUN means the tool is missing on this machine; install it or record the step "
               "as not executed."]
    (out_dir / "summary.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print("\n" + "\n".join(report))
    print(f"\nSaved to {out_dir}")
    return overall


if __name__ == "__main__":
    sys.exit(main())
