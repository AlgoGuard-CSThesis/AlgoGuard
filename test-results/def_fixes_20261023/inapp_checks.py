"""In-app re-tests on the fix build, driven in a Chrome window the tester signed in to.

Attaches to Chrome over the DevTools protocol (Chrome started with
--remote-debugging-port=9222 and a separate test profile; the tester signs in there, this script
never sees a password) and uses the real pages:

  smoke   SMK-01 / REG-01.2: Manual Prediction with the Normal row and the Attack row printed in
          the user manual (datasets/algoguard_big.csv file lines 1005 and 4150, the header being
          line 1), then a 120 s replay of algoguard_big.csv at Fast with Save attacks.
  live    TC-11 / DEF-17: live capture on the Wi-Fi adapter with Save attacks for --seconds while
          traffic_gen.py runs; every flow's detection_lag_ms and end_reason are collected by
          polling /monitor/status, so none is lost to the feed's 100-event page.
  max     DEF-11: replay of algoguard_biggest.csv (20,000 flows) at Maximum with Save every flow;
          flows per second from the moment the session runs until it completes.

Each mode writes <mode>.json and screenshots to --out. Run with a Python that has playwright:
    python inapp_checks.py smoke|live|max --out test-results/def_fixes_20261023/inapp
"""
import argparse
import csv
import json
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
BASE = "http://127.0.0.1:5000"
STATS = ["statFlows", "statAttacks", "statNormals", "statAlerts", "statLatency", "statDeployment",
         "statPersisted", "statSynced", "statPending", "statDropped", "statUpload", "statLag",
         "statCapture", "monitorMessage"]


def dataset_row(line_number):
    with open(ROOT / "datasets" / "algoguard_big.csv", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    return dict(zip(rows[0], rows[line_number - 1]))


def nearest_rank(values, share):
    ordered = sorted(values)
    return round(ordered[max(int(round(share * len(ordered) + 0.5)) - 1, 0)], 1) if values else None


def status(page, since=0):
    return page.evaluate(
        "async s => (await fetch('/monitor/status?since=' + s, {cache: 'no-store'})).json()", since)


def shot_page(page, path):
    """Screenshot evidence; Chrome only paints a window that is in front."""
    page.bring_to_front()
    try:
        page.screenshot(path=str(path), full_page=True, timeout=15000)
    except Exception as error:  # evidence is the JSON; a missing picture is noted, not fatal
        print(f"screenshot {path.name} skipped: {error.__class__.__name__}")


def screen(page):
    return {key: page.eval_on_selector(f"#{key}", "e => e.innerText.trim()")
            for key in STATS if page.query_selector(f"#{key}")}


def predict(page, values, shot):
    page.goto(f"{BASE}/simulation")
    for name, value in values.items():
        if name != "label":
            page.fill(f"input[name='{name}']", value)
    page.click("form.simulation-form button[type=submit]")
    page.wait_for_selector(".result-card")
    shot_page(page, shot)
    card = page.inner_text(".result-card").split()
    details = page.inner_text(".simulation-result-panel")
    return {"verdict": card[0], "confidence": card[1], "panel": " | ".join(details.split("\n"))}


def start_monitor(page, source, persist, dataset=None, speed=None, interface_match=None):
    page.goto(f"{BASE}/monitor")
    page.select_option("#monitorSource", source)
    if dataset:
        page.select_option("#monitorDataset", dataset)
    if speed:
        page.select_option("#monitorSpeed", speed)
    if interface_match:
        value = page.eval_on_selector(
            "#monitorInterface", "(e, m) => [...e.options].find(o => o.text.includes(m)).value",
            interface_match)
        page.select_option("#monitorInterface", value)
    page.select_option("#monitorPersist", persist)
    page.click("#monitorStart")


def collect(page, until, poll=1.0):
    """Poll until ``until(session)`` is true; return every event and the last status."""
    events, last_seq, session_id, current = {}, 0, None, {}
    while True:
        current = status(page, last_seq)
        session = current["session"]
        if session_id and session.get("session_id") != session_id:
            raise SystemExit("Another session started while collecting.")
        session_id = session.get("session_id")
        for event in current["events"]:
            events[event["seq"]] = event
        last_seq = max([last_seq] + list(events))
        if until(session):
            return [events[k] for k in sorted(events)], current
        time.sleep(poll)


def run_smoke(page, out):
    normal = predict(page, dataset_row(1005), out / "smoke_tc01_normal.png")
    attack = predict(page, dataset_row(4150), out / "smoke_tc02_attack.png")
    start_monitor(page, "csv", "attacks", dataset="algoguard_big.csv", speed="fast")
    started = time.strftime("%Y-%m-%d %H:%M:%S")
    end = time.time() + 120
    events, _ = collect(page, lambda s: time.time() >= end)
    page.click("#monitorStop")
    time.sleep(8)  # let uploads finish before reading the counters
    shot_page(page, out / "smoke_replay.png")
    return {"tc01_normal_line_1005": normal, "tc02_attack_line_4150": attack,
            "replay": {"started": started, "stopped": time.strftime("%Y-%m-%d %H:%M:%S"),
                       "events_seen": len(events), "screen": screen(page),
                       "final": status(page)["session"]}}


def run_live(page, out, seconds):
    start_monitor(page, "live", "attacks", interface_match="Wi-Fi 6")
    generator = subprocess.Popen([str(ROOT / "venv" / "Scripts" / "python.exe"),
                                  str(Path(__file__).with_name("traffic_gen.py")),
                                  "--seconds", str(seconds + 5)])
    started = time.strftime("%Y-%m-%d %H:%M:%S")
    end = time.time() + seconds
    events, current = collect(page, lambda s: time.time() >= end)
    page.click("#monitorStop")
    generator.wait()
    time.sleep(8)
    shot_page(page, out / "live_capture.png")
    teardown = [e["detection_lag_ms"] for e in events
                if e.get("end_reason") in ("fin", "rst") and e.get("detection_lag_ms") is not None]
    reasons = {}
    for event in events:
        reasons[event.get("end_reason")] = reasons.get(event.get("end_reason"), 0) + 1
    final = status(page)["session"]
    return {"started": started, "stopped": time.strftime("%Y-%m-%d %H:%M:%S"),
            "seconds": seconds, "flows": len(events), "end_reasons": reasons,
            "lag_teardown_ms (fin/rst, excludes timeouts)": {
                "n": len(teardown), "p50": nearest_rank(teardown, 0.5),
                "p95": nearest_rank(teardown, 0.95), "max": max(teardown) if teardown else None},
            "model_latency_ms": {
                "p50": nearest_rank([e["latency_ms"] for e in events], 0.5),
                "p95": nearest_rank([e["latency_ms"] for e in events], 0.95)},
            "attack_share": round(sum(e["prediction"] == "Attack" for e in events) / len(events), 4)
            if events else None,
            "screen": screen(page), "final": final,
            "events": [{k: e.get(k) for k in ("seq", "prediction", "confidence", "latency_ms",
                                              "detection_lag_ms", "end_reason", "service",
                                              "persistence")} for e in events]}


def run_max(page, out):
    start_monitor(page, "csv", "all", dataset="algoguard_biggest.csv", speed="max")
    clicked = time.time()
    running_at = None

    def done(session):
        nonlocal running_at
        if running_at is None and session.get("state") == "running":
            running_at = time.time()
        return session.get("state") in ("completed", "stopped", "error")

    events, current = collect(page, done, poll=0.25)
    finished = time.time()
    time.sleep(10)
    shot_page(page, out / "max_replay.png")
    final = status(page)["session"]
    flows = final["totals"]["flows"]
    running_at = running_at or clicked
    return {"clicked": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(clicked)),
            "state": final["state"], "flows": flows, "row_total": final.get("row_total"),
            "seconds_running_to_done": round(finished - running_at, 2),
            "flows_per_second": round(flows / (finished - running_at), 1),
            "seconds_click_to_done": round(finished - clicked, 2),
            "screen": screen(page), "final": final}


def run_soak(page, out, minutes):
    """PRF-01.4: replay algoguard_biggest.csv at Fast with Save attacks; read the page every 5 min.

    Run test-results/prf01/soak_sampler.py alongside for the process CPU and RAM samples.
    """
    start_monitor(page, "csv", "attacks", dataset="algoguard_biggest.csv", speed="fast")
    started = time.time()
    readings = []
    for step in range(1, int(minutes // 5) + 1):
        time.sleep(max(started + step * 300 - time.time(), 0))
        page.goto(f"{BASE}/monitor")
        time.sleep(3)
        shown = screen(page)
        readings.append({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "elapsed_s": step * 300,
                         "flows": shown.get("statFlows"), "persisted": shown.get("statPersisted"),
                         "pending": shown.get("statPending"), "latency": shown.get("statLatency"),
                         "message": shown.get("monitorMessage")})
        print(readings[-1], flush=True)
    page.click("#monitorStop")
    time.sleep(5)
    shot_page(page, out / "soak_end.png")
    return {"started": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(started)),
            "stopped": time.strftime("%Y-%m-%d %H:%M:%S"), "readings": readings,
            "final": status(page)["session"]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["smoke", "live", "max", "soak"])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--seconds", type=int, default=90)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp("http://127.0.0.1:9222")
        page = browser.contexts[0].pages[0]
        if "/login" in page.url:
            raise SystemExit("Sign in first in the test Chrome window.")
        result = {"smoke": lambda: run_smoke(page, args.out),
                  "live": lambda: run_live(page, args.out, args.seconds),
                  "max": lambda: run_max(page, args.out),
                  "soak": lambda: run_soak(page, args.out, 30)}[args.mode]()
    (args.out / f"{args.mode}.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    summary = {k: v for k, v in result.items() if k not in ("events", "final")}
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
