"""30-minute soak (C2-PRF-01): CPU and memory of the running AlgoGuard, sampled from outside it.

Finds the AlgoGuard web console (python ... app.py) and its child processes (capture and
scoring workers), then every --every seconds records their combined CPU, as a share of the
whole machine, and their combined resident memory. Budgets (Master Test Plan 3.4): CPU at most
80%, RAM at most 1 GiB, no steady growth. Start it when the live capture starts.

    venv\\Scripts\\python.exe test-results\\cycle2_qa_20261111\\soak_sampler.py --minutes 30

Writes soak_<date>_<time>.json next to this script. Records process figures only.
"""
import argparse
import json
import statistics
import time
from datetime import datetime
from pathlib import Path

import psutil

HERE = Path(__file__).resolve().parent
CPU_BUDGET, RAM_BUDGET = 80.0, 1024 ** 3


def algoguard_processes():
    roots = [p for p in psutil.process_iter(["name", "cmdline"])
             if "python" in (p.info["name"] or "").lower()
             and any(part.endswith("app.py") for part in (p.info["cmdline"] or []))]
    found = {}
    for root in roots:
        for proc in [root, *root.children(recursive=True)]:
            found[proc.pid] = proc
    return list(found.values())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--minutes", type=float, default=30.0)
    parser.add_argument("--every", type=float, default=30.0)
    args = parser.parse_args()
    if not algoguard_processes():
        raise SystemExit("AlgoGuard is not running: start it first (START_ALGOGUARD.cmd).")
    cores = psutil.cpu_count() or 1
    started, end = datetime.now(), time.monotonic() + args.minutes * 60
    samples = []
    print(f"Sampling AlgoGuard every {args.every:.0f} s for {args.minutes:g} minutes...",
          flush=True)
    while True:
        procs = algoguard_processes()
        for proc in procs:
            try:
                proc.cpu_percent(None)
            except psutil.Error:
                pass
        time.sleep(min(args.every, max(1.0, end - time.monotonic())))
        cpu = rss = 0.0
        for proc in procs:
            try:
                cpu += proc.cpu_percent(None)
                rss += proc.memory_info().rss
            except psutil.Error:
                continue
        samples.append({"time": datetime.now().strftime("%H:%M:%S"), "processes": len(procs),
                        "cpu_percent_of_machine": round(cpu / cores, 2),
                        "rss_mb": round(rss / 1024 / 1024, 1)})
        print(samples[-1], flush=True)
        if time.monotonic() >= end:
            break
    cpu = [s["cpu_percent_of_machine"] for s in samples]
    ram = [s["rss_mb"] for s in samples]
    half = max(1, len(ram) // 2)
    summary = {
        "started": started.strftime("%Y-%m-%d %H:%M:%S"), "minutes": args.minutes,
        "samples": len(samples), "cores": cores,
        "cpu_max_percent": max(cpu), "cpu_mean_percent": round(statistics.mean(cpu), 2),
        "ram_max_mb": max(ram), "ram_end_mb": ram[-1],
        "ram_mean_first_half_mb": round(statistics.mean(ram[:half]), 1),
        "ram_mean_second_half_mb": round(statistics.mean(ram[half:] or ram), 1),
        "cpu_budget_met": max(cpu) <= CPU_BUDGET,
        "ram_budget_met": max(ram) * 1024 * 1024 <= RAM_BUDGET,
    }
    out = HERE / f"soak_{started:%Y%m%d_%H%M%S}.json"
    out.write_text(json.dumps({"summary": summary, "samples": samples}, indent=1) + "\n",
                   encoding="utf-8")
    print(json.dumps(summary, indent=1))
    print("written", out)


if __name__ == "__main__":
    main()
