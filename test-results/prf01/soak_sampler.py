"""PRF-01 soak sampler: AlgoGuard process CPU % and RAM every 30 s -> test-results/soak_<time>.csv.

Run from the repository folder while the app is running:
    venv\\Scripts\\python.exe test-results\\prf01\\soak_sampler.py            (30 minutes)
    venv\\Scripts\\python.exe test-results\\prf01\\soak_sampler.py --minutes 5

It finds the app by its command line (python ... app.py). If Windows hides that because the app
runs as administrator, it falls back to the largest python.exe process (the app's interpreter;
the venv launcher is a few MB). CPU % is of the whole machine (all cores = 100%).
"""
import argparse
import csv
import statistics
import time
from datetime import datetime
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parents[2]


def app_processes():
    found = []
    for proc in psutil.process_iter(["pid", "name", "cmdline", "memory_info"]):
        try:
            if (proc.info["name"] or "").lower() != "python.exe":
                continue
            cmd = " ".join(proc.info["cmdline"] or [])
            if "app.py" in cmd:
                found.append(proc)
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            continue
    if found:
        return found, "command line"
    big = [p for p in psutil.process_iter(["pid", "name", "memory_info"])
           if (p.info["name"] or "").lower() == "python.exe" and p.info["memory_info"]
           and p.info["memory_info"].rss > 100 * 1024 * 1024]
    return sorted(big, key=lambda p: p.info["memory_info"].rss)[-1:], "largest python.exe"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--minutes", type=float, default=30)
    parser.add_argument("--interval", type=float, default=30)
    args = parser.parse_args()
    procs, how = app_processes()
    if not procs:
        raise SystemExit("AlgoGuard app process not found - start the app first.")
    cores = psutil.cpu_count() or 1
    for p in procs:
        p.cpu_percent(None)  # prime the counters
    out = ROOT / "test-results" / f"soak_{datetime.now():%Y%m%d_%H%M%S}.csv"
    print(f"Sampling PIDs {[p.pid for p in procs]} (found by {how}) every {args.interval:g} s "
          f"for {args.minutes:g} min -> {out.name}")
    rows = []
    end = time.time() + args.minutes * 60
    with open(out, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["time", "pids", "cpu_percent_of_machine", "rss_mb", "threads"])
        while time.time() < end:
            time.sleep(args.interval)
            cpu = rss = threads = 0.0
            alive = []
            for p in procs:
                try:
                    cpu += p.cpu_percent(None) / cores
                    rss += p.memory_info().rss / (1024 * 1024)
                    threads += p.num_threads()
                    alive.append(p.pid)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            if not alive:
                print("App process ended - stopping.")
                break
            row = [datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "+".join(map(str, alive)),
                   round(cpu, 2), round(rss, 1), int(threads)]
            writer.writerow(row)
            handle.flush()
            rows.append(row)
            print(f"{row[0]}  CPU {row[2]:6.2f}%  RAM {row[3]:8.1f} MB")
    if rows:
        cpus, rams = [r[2] for r in rows], [r[3] for r in rows]
        half = max(len(rams) // 2, 1)
        print(f"\nSamples {len(rows)} | CPU max {max(cpus):.2f}% mean {statistics.mean(cpus):.2f}% | "
              f"RAM max {max(rams):.1f} MB, first {rams[0]:.1f}, last {rams[-1]:.1f}, "
              f"mean 1st half {statistics.mean(rams[:half]):.1f} vs 2nd half "
              f"{statistics.mean(rams[half:]) if rams[half:] else rams[-1]:.1f} MB")
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
