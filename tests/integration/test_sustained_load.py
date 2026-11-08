"""Stage 6C.4: sustained capture and upload, successive sessions, and a network outage, checked
against the budgets agreed for the 5F pilot.

The app runs on the local stack, but every cloud request passes through ``FaultProxy`` with
``LATENCY`` seconds added before forwarding and again before answering, about what a new HTTPS
request from Manila to the Tokyo pilot costs (three ~104 ms round trips plus the database). With
it, one request at a time drains a 300-event burst at roughly the pilot's 65 events/s (DEF-10).

Two parts:

* DEF-10: one 300-event capture at maximum speed, uploaded one request at a time and then four at
  once, measured as the defect was (cloud receipt minus event time, p95).
* Sustained phase: ``STREAM_RATE`` events/s handed to the upload queue as a capture would, while
  successive paced captures start and stop alongside, and the cloud stops answering for a while.

``SUSTAINED_SECONDS`` sets the length of the sustained phase (90 s in the integration lane) and
``SUSTAINED_REPORT`` names a JSON file for the measurements (none by default).
"""

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

import psutil
import pytest

from cloud_app import _owner
from cloud_connections import REGISTRY
from cloud_monitor import flow_event
from tests.cloud_support import attack_rows, post_json, wait_for

from .app_fixtures import identity_for, make_account, sign_in, start_app
from .fault_proxy import FaultProxy, route_through

pytestmark = pytest.mark.integration
LATENCY = 0.2
CAP = 300
STREAM_RATE = 25.0
SESSION_SECONDS = 10.0
POLL_SECONDS = 0.7  # templates/monitor.html POLL_INTERVAL_MS

# Budgets agreed for the pilot on 2026-10-08 (docs/migration/05f-pilot.md section 2).
ACK_P95_SECONDS = 5.0
SUSTAINED_EVENTS_PER_SECOND = 20.0
RECOVERY_SECONDS = 120.0
RAM_BYTES = 1024 ** 3
STATE_BYTES = 50 * 1024 * 1024


def nearest_rank(values, fraction):
    ordered = sorted(values)
    return ordered[max(0, min(len(ordered) - 1, int(-(-fraction * len(ordered) // 1)) - 1))]


def folder_bytes(path):
    total = 0
    for item in Path(path).rglob("*"):
        try:
            total += item.stat().st_size if item.is_file() else 0
        except FileNotFoundError:
            continue  # a rollback journal that was just removed
    return total


def capture_once(client, deadline, speed="max", seconds=None):
    """One capture: at maximum speed until its 300 stored events are queued, or paced for a
    number of seconds. Returns its capture id, or None when it could not start."""
    response = post_json(client, "/monitor/start", {"source_type": "csv", "speed": speed,
                                                    "persist": "all"})
    if response.status_code != 200:
        return None
    until = time.monotonic() + seconds if seconds else None

    def check():
        data = client.get("/monitor/status").get_json()
        return data if (
            data["session"]["state"] in ("completed", "error", "stopped")
            or (until is None and data["session"]["totals"]["queued"] >= CAP)
            or (until is not None and time.monotonic() >= until)) else None

    try:
        # Polled every 0.7 s, as the Live Monitor page does.
        session = wait_for(check, timeout=max(5.0, deadline - time.monotonic()),
                           interval=POLL_SECONDS)["session"]
    except AssertionError:
        session = None
    stopped = post_json(client, "/monitor/stop").get_json() or {}
    return (stopped.get("session") or session or {}).get("capture_id")


def settled(env, owner):
    counts = env.services.outbox.counts(owner)
    return not counts["pending"] and not counts["in_memory"]


def delays_of(rows):
    return [float(row[2]) - float(row[1]) for row in rows]


def summary(values):
    return {"n": len(values), "p50_s": round(nearest_rank(values, .5), 2),
            "p95_s": round(nearest_rank(values, .95), 2), "max_s": round(max(values), 2)}


def burst(cloud, env, account, client, parallelism):
    """DEF-10's measurement: one 300-event capture at maximum speed, uploaded as it runs."""
    owner = identity_for(env.app, account)
    wait_for(lambda: settled(env, owner), timeout=120, interval=0.1)
    env.services.sync.parallelism = parallelism
    capture = None
    while capture is None:
        capture = capture_once(client, time.monotonic() + 60)
    wait_for(lambda: settled(env, owner), timeout=120, interval=0.1)
    rows = cloud["sql"]("select capture_id,extract(epoch from event_time),"
                        "extract(epoch from received_at) from public.ingest_event "
                        "where capture_id=%s", (capture,))
    return {**summary(delays_of(rows)), "events": len(rows),
            "produced_s": round(max(float(row[1]) for row in rows)
                                - min(float(row[1]) for row in rows), 2),
            "drain_s": round(max(float(row[2]) for row in rows)
                             - min(float(row[1]) for row in rows), 2)}


def test_sustained_sessions_and_an_outage_stay_within_the_pilot_budgets(
        cloud, local_stack, local_http, published, tmp_path):
    duration = float(os.environ.get("SUSTAINED_SECONDS", "90"))
    outage = min(30.0, duration / 3)
    analyst = make_account(cloud, local_stack, local_http, "sustained")
    proxy = FaultProxy(local_stack["api_url"], latency=LATENCY)
    state = tmp_path / "pc"
    env = start_app(cloud, local_stack, state, [analyst])
    route_through(env.services.auth, proxy)
    process = psutil.Process()
    samples, sampling, streaming = [], threading.Event(), threading.Event()
    streamed = []

    def sample():
        while not sampling.wait(2.0):
            samples.append({"t": time.monotonic(), "rss": process.memory_info().rss,
                            "spool": folder_bytes(state / "outbox"),
                            "state": folder_bytes(state), "owned": REGISTRY.stats(),
                            "threads": threading.active_count()})

    def stream(owner, deployment, features):
        due = time.monotonic()
        while not streaming.is_set():
            event = flow_event(deployment, {"prediction": "Normal", "confidence": 97.0,
                                            "latency_ms": 1.0}, dict(features), "live_monitor",
                               None)
            if env.services.outbox.submit(owner, "steady", event,
                                          capped=False)["persistence"] == "in_memory":
                streamed.append(event["event_uuid"])
            due += 1 / STREAM_RATE
            time.sleep(max(0.0, due - time.monotonic()))

    sampler = threading.Thread(target=sample, daemon=True)
    report: dict[str, Any] = {
        "latency_each_way_s": LATENCY, "duration_s": duration, "outage_s": outage,
        "stream_events_per_s": STREAM_RATE, "session_seconds": SESSION_SECONDS}
    try:
        client = sign_in(env.app, analyst)
        sampler.start()
        # DEF-10: the same 300-event burst, one request at a time and then four at once.
        one = burst(cloud, env, analyst, client, 1)
        four = burst(cloud, env, analyst, client, 4)
        report["def10"] = {"one_at_a_time": one, "four_at_once": four}
        assert one["events"] == four["events"] == CAP
        assert four["p95_s"] < one["p95_s"] and four["p95_s"] <= ACK_P95_SECONDS

        # Sustained phase: a steady stream plus successive sessions, then an outage.
        owner = _owner(identity_for(env.app, analyst))
        manifest = env.services.models.current.manifest
        deployment = {"model_id": manifest["model_id"], "deployment_id":
                      manifest["deployment_id"], "model_name": "Stacking Ensemble"}
        feeder = threading.Thread(target=stream, args=(owner, deployment, attack_rows(1)[0]),
                                  daemon=True)
        captures, began = [], time.monotonic()
        report["phase_started"] = time.time()
        feeder.start()
        outage_start, outage_end = began + duration / 3, began + duration / 3 + outage
        in_outage = False
        while time.monotonic() - began < duration:
            now = time.monotonic()
            if not in_outage and outage_start <= now < outage_end:
                proxy.rule("/", "stall", times=1_000_000)
                in_outage, report["outage_started"] = True, time.time()
            if in_outage and now >= outage_end:
                proxy.clear()
                proxy.release()
                in_outage, report["outage_ended"] = False, time.time()
            seconds = min(SESSION_SECONDS, max(1.0, began + duration - now))
            capture = capture_once(client, now + seconds + 30, speed="fast", seconds=seconds)
            if capture:
                captures.append(capture)
            else:
                time.sleep(1.0)  # a new capture needs the cloud; the outage refuses its start
        streaming.set()
        feeder.join(timeout=5)
        if in_outage:
            proxy.clear()
            proxy.release()
            report["outage_ended"] = time.time()
        report["phase_ended"] = time.time()
        reconnected = time.monotonic()
        wait_for(lambda: settled(env, owner), timeout=RECOVERY_SECONDS + 60, interval=0.5)
        report["drained_after_phase_s"] = round(time.monotonic() - reconnected, 1)
        sampling.set()
        sampler.join(timeout=5)

        rows = cloud["sql"](
            "select capture_id,extract(epoch from event_time),extract(epoch from received_at),"
            "event_uuid::text from public.ingest_event where owner_profile_id=%s",
            (analyst.profile,))
        counts = env.services.outbox.counts(owner)
        totals = cloud["sql"](
            "select count(*)-count(distinct event_uuid),"
            "(select count(*) from public.network_traffic where owner_profile_id=%s),"
            "(select count(*) from public.prediction where owner_profile_id=%s) "
            "from public.ingest_event where owner_profile_id=%s",
            (analyst.profile, analyst.profile, analyst.profile))[0]
        lost = [reason for reason in env.services.outbox.lost.values()
                if reason != "session_cap"]
        wanted, ids = set(captures), set(streamed)
        phase = [row for row in rows if row[0] in wanted or row[3] in ids]
        # Online: events made while the cloud answered and no outage backlog was ahead of them.
        # Events made during the outage (or pending when it began) form the backlog, and those
        # made after reconnection wait behind it until it has drained; both are reported apart.
        start: float = report.get("outage_started") or 0.0
        end: float = report.get("outage_ended") or 0.0
        caught = [row for row in phase if start and start - 15 <= float(row[1]) <= end]
        drained = max((float(row[2]) for row in caught), default=end)
        online = [row for row in phase
                  if not start or float(row[1]) < start - 15 or float(row[1]) > drained]
        behind = [row for row in phase if start and end < float(row[1]) <= drained]
        backlog = [float(row[2]) - end for row in caught]
        report["after_reconnect_behind_backlog"] = summary(delays_of(behind)) if behind else None
        rss = [item["rss"] for item in samples]
        third = max(1, len(rss) // 3)
        report.update({
            "captures": len(captures), "streamed": len(streamed),
            "events_acknowledged": len(phase),
            "stream_missing_on_server": len(ids - {row[3] for row in rows}),
            "sustained_events_per_s": round(len(phase) / (report["phase_ended"]
                                                          - report["phase_started"]), 1),
            "ack_delay_online": summary(delays_of(online)),
            "ack_delay_all": summary(delays_of(phase)),
            "outage_backlog": {"events": len(backlog),
                               "last_synced_s_after_reconnect":
                                   round(max(backlog), 1) if backlog else None},
            "local": {"synced": counts["synced"], "pending": counts["pending"],
                      "rejected": counts["rejected"], "lost_other_than_cap": len(lost)},
            "server": {"duplicate_events": totals[0], "traffic_rows": totals[1],
                       "prediction_rows": totals[2], "events": len(rows)},
            "memory_mib": {"start": round(rss[0] / 2**20, 1), "peak": round(max(rss) / 2**20, 1),
                           "first_third_mean": round(sum(rss[:third]) / third / 2**20, 1),
                           "last_third_mean": round(sum(rss[-third:]) / third / 2**20, 1)},
            "threads": {"start": samples[0]["threads"], "end": samples[-1]["threads"],
                        "peak": max(item["threads"] for item in samples)},
            "storage_mib": {"spool_peak": round(max(item["spool"] for item in samples) / 2**20,
                                                2),
                            "spool_by_third": [round(samples[min(len(samples) - 1, index)]
                                                     ["spool"] / 2**20, 2)
                                               for index in (len(samples) // 3,
                                                             2 * len(samples) // 3,
                                                             len(samples) - 1)],
                            "state_peak": round(max(item["state"] for item in samples) / 2**20,
                                                2)},
            "owned_connections_end": samples[-1]["owned"],
        })
        if os.environ.get("SUSTAINED_REPORT"):
            Path(os.environ["SUSTAINED_REPORT"]).write_text(json.dumps(report, indent=2),
                                                            encoding="utf-8")
        print(json.dumps(report, indent=2))

        assert report["ack_delay_online"]["p95_s"] <= ACK_P95_SECONDS
        assert report["sustained_events_per_s"] >= SUSTAINED_EVENTS_PER_SECOND
        if backlog:
            assert report["outage_backlog"]["last_synced_s_after_reconnect"] <= RECOVERY_SECONDS
        assert report["stream_missing_on_server"] == 0
        assert counts["pending"] == 0 and counts["rejected"] == 0 and not lost
        assert totals[0] == 0 and totals[1] == totals[2] == len(rows)
        assert max(rss) <= RAM_BYTES and max(item["state"] for item in samples) <= STATE_BYTES
        # At most SYNCED_KEEP receipts (about 1.2 KiB each) plus what is still pending.
        assert samples[-1]["spool"] <= 20 * 2**20
        assert samples[-1]["owned"]["evicted"] == 0
    finally:
        sampling.set()
        streaming.set()
        proxy.close()
        env.services.close()
