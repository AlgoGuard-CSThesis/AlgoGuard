"""Live traffic monitoring for AlgoGuard.

Streams network flows through the deployed Stacking Ensemble one at a time, so
an analyst can watch anomaly detection happen instead of submitting a single
record by hand. One monitoring session runs per process; the browser polls
:func:`get_status` for new classification events.

Three traffic sources feed the same classification loop (see
``services.traffic_source_service``):

- ``csv``: replays labelled UNSW-NB15 flow samples — the original behaviour,
  with ground-truth labels so the feed can show verdict accuracy;
- ``pcap``: aggregates a recorded packet capture into flows and replays them;
- ``live``: captures data packets from a network interface in real time and
  classifies each flow the moment it ends. Requires a capture driver (Npcap on
  Windows); when one is missing, replay modes keep working and the monitor
  says why live mode is unavailable.
"""

import math
import os
import threading
import time
from collections import deque
from contextlib import ExitStack
from uuid import uuid4

import numpy as np
import pandas as pd

from config import get_config
from services.database_service import (
    finalize_capture_session,
    insert_capture_session,
    log_system_event,
    store_classified_flow,
    utc_now,
)
from services.deployment_service import DeploymentError, load_active_artifact
from services.traffic_source_service import (
    CsvReplaySource,
    LiveCaptureSource,
    PcapReplaySource,
    TrafficSourceCancelled,
    TrafficSourceError,
    list_capture_files,
    list_capture_interfaces,
    live_capture_available,
)

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATASET_FOLDER = os.path.join(BASE_DIR, "datasets")

SOURCE_CHOICES = {
    "csv": "Sample replay (labelled flows)",
    "pcap": "Recorded packets (PCAP)",
    "live": "Live interface capture",
}
DATASET_CHOICES = {
    "algoguard_big.csv": "Sample traffic - 5,000 flows",
    "algoguard_bigger.csv": "Sample traffic - 10,000 flows",
    "algoguard_biggest.csv": "Sample traffic - 20,000 flows",
}
SPEED_CHOICES = {"slow": 2.0, "normal": 1.0, "fast": 0.35}
ORDER_CHOICES = {"sequential": "Sequential", "random": "Randomised"}
PERSIST_CHOICES = {
    "none": "Session only",
    "attacks": "Save attacks",
    "all": "Save every flow",
}

DEFAULT_SOURCE_TYPE = "csv"
DEFAULT_DATASET = "algoguard_big.csv"
DEFAULT_SPEED = "normal"
DEFAULT_ORDER = "sequential"
DEFAULT_PERSIST = "attacks"

FEED_MAXLEN = 250
MAX_EVENTS_PER_POLL = 100
MAX_PERSISTED_PER_SESSION = 300
ACTIVE_STATES = ("starting", "running", "paused", "stopping")
TERMINAL_STATES = ("idle", "stopped", "completed", "error")

_LOCK = threading.RLock()
_SESSION = None
_THREAD = None
_STOP_EVENT = None
_PAUSE_EVENT = None
_EVENTS = deque(maxlen=FEED_MAXLEN)


class LiveMonitorError(RuntimeError):
    """Raised when a monitoring session cannot be started or controlled."""


def _new_session(source_type, source_name, dataset, speed, order, persist, admin_id):
    return {
        "session_id": uuid4().hex,
        "state": "starting",
        "source_type": source_type,
        "source_name": source_name,
        "dataset": dataset,
        "dataset_label": _source_label(source_type, source_name, dataset),
        "speed": speed,
        "order": order,
        "persist": persist,
        "admin_id": admin_id,
        "started_at": utc_now(),
        "error_message": None,
        "capped": False,
        "seq": 0,
        "row_total": 0,
        "deployment": None,
        "totals": {
            "flows": 0,
            "attacks": 0,
            "normals": 0,
            "alerts": 0,
            "persisted": 0,
            "mismatches": 0,
            "labelled": 0,
        },
        "capture": None,
        "latency_total_ms": 0.0,
        "avg_latency_ms": 0.0,
        "lag_total_ms": 0.0,
        "avg_detection_lag_ms": 0.0,
    }


def _source_label(source_type, source_name, dataset):
    if source_type == "csv":
        return DATASET_CHOICES.get(dataset, dataset)
    if source_type == "pcap":
        return f"Capture file - {source_name}"
    return f"Live capture - {source_name or 'default interface'}"


def _public_session(session):
    """Copy session state without internal bookkeeping fields."""
    if not session:
        return {
            "session_id": None,
            "state": "idle",
            "source_type": DEFAULT_SOURCE_TYPE,
            "source_name": None,
            "dataset": DEFAULT_DATASET,
            "speed": DEFAULT_SPEED,
            "order": DEFAULT_ORDER,
            "persist": DEFAULT_PERSIST,
            "error_message": None,
            "capped": False,
            "row_total": 0,
            "deployment": None,
            "totals": {
                "flows": 0,
                "attacks": 0,
                "normals": 0,
                "alerts": 0,
                "persisted": 0,
                "mismatches": 0,
                "labelled": 0,
            },
            "capture": None,
            "avg_latency_ms": 0.0,
            "avg_detection_lag_ms": 0.0,
            "attack_ratio": 0.0,
            "started_at": None,
        }

    totals = dict(session["totals"])
    flows = totals["flows"]
    return {
        "session_id": session["session_id"],
        "state": session["state"],
        "source_type": session["source_type"],
        "source_name": session["source_name"],
        "dataset": session["dataset"],
        "dataset_label": session["dataset_label"],
        "speed": session["speed"],
        "order": session["order"],
        "persist": session["persist"],
        "error_message": session["error_message"],
        "capped": session["capped"],
        "row_total": session["row_total"],
        "deployment": session["deployment"],
        "totals": totals,
        "capture": dict(session["capture"]) if session.get("capture") else None,
        "avg_latency_ms": round(session["avg_latency_ms"], 3),
        "avg_detection_lag_ms": round(session["avg_detection_lag_ms"], 3),
        "attack_ratio": round((totals["attacks"] / flows) * 100, 2) if flows else 0.0,
        "started_at": session["started_at"],
    }


def _resolve_dataset_path(dataset):
    if dataset not in DATASET_CHOICES:
        raise LiveMonitorError("Select one of the bundled traffic samples.")
    path = os.path.join(DATASET_FOLDER, dataset)
    if not os.path.isfile(path):
        raise LiveMonitorError(f"The traffic sample {dataset} is missing from datasets/.")
    return path


def _resolve_capture_path(filename):
    name = os.path.basename(str(filename or ""))
    if not name or name != filename:
        raise LiveMonitorError("Select a capture file from the captures folder.")
    if not name.lower().endswith((".pcap", ".pcapng", ".cap")):
        raise LiveMonitorError("Capture files must be .pcap, .pcapng, or .cap recordings.")
    path = os.path.join(get_config().capture_folder, name)
    if not os.path.isfile(path):
        raise LiveMonitorError(f"The capture file {name} is missing from captures/.")
    return path


def _json_safe(value):
    """Return a value the browser's JSON parser will accept.

    NaN and infinity are valid Python floats but invalid JSON, and would make
    the page's fetch fail silently mid-stream.
    """
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _build_flow_row(record, feature_columns, numeric_columns, defaults):
    """Coerce one flow record into the deployed artifact's schema."""
    row = {}
    for column in feature_columns:
        value = record.get(column, defaults.get(column))
        if column in numeric_columns:
            if value in (None, ""):
                row[column] = np.nan
            else:
                try:
                    row[column] = float(value)
                except (TypeError, ValueError, OverflowError):
                    row[column] = np.nan
                if not np.isfinite(row[column]):
                    row[column] = np.nan
        else:
            row[column] = np.nan if pd.isna(value) or value == "" else str(value)
    return row


def _should_persist(mode, prediction):
    if mode == "none":
        return False
    return mode == "all" or prediction == "Attack"


def _store_flow(deployment, flow_data, result, source_tag):
    """Write one classified flow to SQLite. Called without the session lock held.

    Only the worker thread stores flows, so this needs no lock of its own, and
    keeping the database writes outside the lock means a slow write can never
    stall the page's status polling.
    """
    is_attack = result["prediction"] == "Attack"
    _, prediction_id, alert_id = store_classified_flow(
        flow_data,
        source_tag,
        deployment.get("model_id"),
        result["prediction"],
        result["confidence"],
        deployment_id=deployment.get("deployment_id"),
        model_name=deployment.get("model_name"),
        latency_ms=result["latency_ms"],
        create_alert=is_attack,
        alert_description=(
            f"Attack detected by {deployment.get('model_name')} during live monitoring."
        ),
    )
    return prediction_id, alert_id


def _make_source(session, cancel_event=None):
    source_type = session["source_type"]
    if source_type == "csv":
        return CsvReplaySource(
            _resolve_dataset_path(session["dataset"]),
            order=session["order"],
        )
    if source_type == "pcap":
        return PcapReplaySource(
            _resolve_capture_path(session["source_name"]),
            order=session["order"],
            cancel_event=cancel_event,
        )
    return LiveCaptureSource(session["source_name"])


def _record_worker_failure(session, error):
    with _LOCK:
        session["error_message"] = session["error_message"] or str(error)
    try:
        log_system_event(
            session["admin_id"], "Live Monitor", "monitor_failed", "Failed", message=str(error)
        )
    except Exception:
        # The database may be the cause of the failure. The in-memory status
        # still reports it, and logging must never prevent capture cleanup.
        pass


def _worker(session, stop_event, pause_event):
    """Own capture resources even when startup, persistence, or logging fails."""
    global _THREAD

    resources = {"source": None, "capture_id": None}
    final_state = "error"
    try:
        with ExitStack() as cleanup:
            final_state = _run_worker(session, stop_event, pause_event, cleanup, resources)
    except Exception as error:
        final_state = "error"
        _record_worker_failure(session, error)
    finally:
        if resources["capture_id"] is not None:
            try:
                stats = resources["source"].stats()
                with _LOCK:
                    session["capture"] = stats
                finalize_capture_session(
                    resources["capture_id"], stats.get("packets", 0),
                    stats.get("dropped", 0), stats.get("flows", 0), final_state,
                )
            except Exception as error:
                final_state = "error"
                _record_worker_failure(session, error)
        with _LOCK:
            # Publish completion only after capture cleanup and all database
            # writes finish, so a visible terminal state is safe to restart.
            session["state"] = final_state
            if _THREAD is threading.current_thread():
                _THREAD = None


def _run_worker(session, stop_event, pause_event, cleanup, resources):
    """Stream the selected source through the deployed model until stopped."""
    admin_id = session["admin_id"]
    paced_interval = SPEED_CHOICES[session["speed"]]
    source_type = session["source_type"]
    source_tag = "live_capture" if source_type == "live" else "live_monitor"

    try:
        # The 33 MB artifact is loaded once per session; a redeploy in the middle
        # of a session is picked up the next time the monitor is started.
        artifact, deployment = load_active_artifact()
    except DeploymentError as error:
        with _LOCK:
            session["error_message"] = str(error)
        log_system_event(admin_id, "Live Monitor", "monitor_failed", "Failed", message=str(error))
        return "error"

    if stop_event.is_set():
        return "stopped"

    feature_columns = list(artifact.get("feature_columns") or [])
    numeric_columns = set(artifact.get("numeric_columns") or [])
    defaults = artifact.get("feature_defaults") or {}
    pipeline = artifact.get("pipeline")
    deployment_summary = {
        "model_name": deployment.get("model_name"),
        "model_id": deployment.get("model_id"),
        "deployment_id": deployment.get("deployment_id"),
        "run_id": deployment.get("run_id"),
    }

    source = None
    try:
        source = _make_source(session, cancel_event=stop_event)
        resources["source"] = source
        cleanup.callback(source.close)
        # CSV columns are known after reading its header. Packet sources have
        # a fixed schema, which we can check before opening a capture handle.
        if source_type == "csv":
            source.prepare()
        missing = [column for column in feature_columns if column not in source.columns]
        if missing:
            if source_type == "csv":
                message = f"The traffic sample is missing required columns: {', '.join(missing)}."
            else:
                message = (
                    "The deployed model requires features unavailable from packet capture: "
                    f"{', '.join(missing)}. Train and deploy using the bundled traffic "
                    "features before starting packet monitoring."
                )
            raise LiveMonitorError(message)
        if source_type != "csv":
            source.prepare()
    except TrafficSourceCancelled:
        if source is not None:
            cleanup.close()
        return "stopped"
    except (LiveMonitorError, TrafficSourceError) as error:
        with _LOCK:
            session["error_message"] = str(error)
        log_system_event(admin_id, "Live Monitor", "monitor_failed", "Failed", message=str(error))
        if source is not None:
            cleanup.close()
        return "error"
    except Exception as error:
        with _LOCK:
            session["error_message"] = str(error)
        log_system_event(admin_id, "Live Monitor", "monitor_failed", "Failed", message=str(error))
        if source is not None:
            cleanup.close()
        return "error"

    # The stack's first prediction pays a one-off warm-up cost. Spend it here,
    # while the page still shows "starting", so the feed opens at full speed and
    # the reported average latency reflects steady-state inference.
    try:
        warmup_row = _build_flow_row(dict(defaults), feature_columns, numeric_columns, defaults)
        pipeline.predict(pd.DataFrame([warmup_row], columns=feature_columns))
    except Exception:
        pass

    if stop_event.is_set():
        cleanup.close()
        return "stopped"

    if source_type == "live":
        resources["capture_id"] = insert_capture_session(
            admin_id, session["source_name"], getattr(source, "bpf_filter", "")
        )

    with _LOCK:
        session["deployment"] = deployment_summary
        session["row_total"] = int(source.row_total or 0)
        session["state"] = "running"

    log_system_event(
        admin_id,
        "Live Monitor",
        "monitor_started",
        "Success",
        message=(
            f"Live monitoring started on {session['dataset_label']} "
            + (
                f"({source.row_total:,} flows, {session['speed']} speed)."
                if source.row_total
                else (
                    "(real-time capture)."
                    if source_type == "live"
                    else f"(streaming recorded capture, {session['speed']} speed)."
                )
            )
        ),
        run_id=deployment_summary["run_id"],
        model_name=deployment_summary["model_name"],
    )

    final_state = "completed"
    row_index = -1
    last_capture_refresh = 0.0
    try:
        while True:
            if stop_event.is_set():
                final_state = "stopped"
                break
            while pause_event.is_set():
                if stop_event.wait(0.2):
                    break
            if stop_event.is_set():
                final_state = "stopped"
                break

            try:
                event_data = source.next_event(timeout=0.2)
            except TrafficSourceCancelled:
                final_state = "stopped"
                break
            except StopIteration:
                break
            if event_data is None:
                # A live interface can be quiet, or carry only excluded traffic,
                # for a long time. Refresh the capture counters anyway so the
                # page shows that capture is running before the first verdict.
                if source_type == "live" and (time.time() - last_capture_refresh) >= 1.0:
                    last_capture_refresh = time.time()
                    stats_snapshot = source.stats()
                    with _LOCK:
                        session["capture"] = stats_snapshot
                continue
            row_index += 1

            record = event_data["record"]
            actual = event_data["actual"]
            flow_row = _build_flow_row(record, feature_columns, numeric_columns, defaults)
            input_frame = pd.DataFrame([flow_row], columns=feature_columns)

            start = time.perf_counter()
            predicted_value = int(pipeline.predict(input_frame)[0])
            probabilities = np.asarray(pipeline.predict_proba(input_frame))[0]
            classes = list(pipeline.classes_)
            confidence = (
                float(probabilities[classes.index(predicted_value)])
                if predicted_value in classes
                else 0.0
            )
            latency_ms = (time.perf_counter() - start) * 1000

            prediction = "Attack" if predicted_value == 1 else "Normal"
            detection_lag_ms = None
            if source_type == "live" and event_data.get("flow_last_ts"):
                detection_lag_ms = max((time.time() - event_data["flow_last_ts"]) * 1000, 0.0)

            flow_data = {column: _json_safe(value) for column, value in flow_row.items()}
            flow_data["source_ip"] = event_data["source_ip"]
            flow_data["destination_ip"] = event_data["destination_ip"]
            flow_data["source_port"] = event_data["source_port"]
            flow_data["destination_port"] = event_data["destination_port"]
            flow_data["protocol"] = event_data["protocol"]
            flow_data["timestamp"] = utc_now()

            result = {
                "prediction": prediction,
                "confidence": round(confidence * 100, 2),
                "latency_ms": round(latency_ms, 3),
            }

            # Decide under the lock, write outside it, then record the outcome.
            with _LOCK:
                persist_mode = session["persist"]
                deployment_target = session["deployment"] or {}
                at_cap = session["totals"]["persisted"] >= MAX_PERSISTED_PER_SESSION

            store = _should_persist(persist_mode, prediction)
            hit_cap = store and at_cap
            prediction_id = alert_id = None
            if store and not at_cap:
                prediction_id, alert_id = _store_flow(
                    deployment_target, flow_data, result, source_tag
                )

            with _LOCK:
                if hit_cap:
                    session["capped"] = True
                if prediction_id is not None:
                    session["totals"]["persisted"] += 1
                if alert_id is not None:
                    session["totals"]["alerts"] += 1
                totals = session["totals"]
                totals["flows"] += 1
                if prediction == "Attack":
                    totals["attacks"] += 1
                else:
                    totals["normals"] += 1
                if actual is not None:
                    totals["labelled"] += 1
                    if actual != prediction:
                        totals["mismatches"] += 1
                session["latency_total_ms"] += latency_ms
                session["avg_latency_ms"] = session["latency_total_ms"] / totals["flows"]
                if detection_lag_ms is not None:
                    session["lag_total_ms"] += detection_lag_ms
                    session["avg_detection_lag_ms"] = session["lag_total_ms"] / totals["flows"]
                if source_type == "live":
                    session["capture"] = source.stats()
                session["seq"] += 1
                _EVENTS.append(
                    {
                        "seq": session["seq"],
                        "timestamp": flow_data["timestamp"],
                        "row_index": row_index,
                        "prediction": prediction,
                        "confidence": result["confidence"],
                        "latency_ms": result["latency_ms"],
                        "detection_lag_ms": (
                            round(detection_lag_ms, 1) if detection_lag_ms is not None else None
                        ),
                        "actual": actual,
                        "match": None if actual is None else actual == prediction,
                        "proto": str(record.get("proto", "")),
                        "service": str(record.get("service", "")),
                        "state": str(record.get("state", "")),
                        "sbytes": _json_safe(record.get("sbytes")),
                        "dbytes": _json_safe(record.get("dbytes")),
                        "source_ip": event_data["source_ip"],
                        "destination_ip": event_data["destination_ip"],
                        "source_port": event_data["source_port"],
                        "destination_port": event_data["destination_port"],
                        "end_reason": event_data.get("end_reason"),
                        "prediction_id": prediction_id,
                        "alert_id": alert_id,
                    }
                )

            if source.paced and stop_event.wait(paced_interval):
                final_state = "stopped"
                break
    except Exception as error:
        with _LOCK:
            session["error_message"] = str(error)
        log_system_event(
            admin_id,
            "Live Monitor",
            "monitor_failed",
            "Failed",
            message=f"Live monitoring stopped after an error: {error}",
            model_name=deployment_summary["model_name"],
        )
        cleanup.close()
        return "error"

    cleanup.close()
    with _LOCK:
        if source_type == "pcap" and final_state == "completed":
            session["row_total"] = session["totals"]["flows"]
        if source_type == "live":
            session["capture"] = source.stats()
        totals = dict(session["totals"])

    capture_stats = source.stats()
    capture_note = (
        f" Capture: {capture_stats.get('packets', 0):,} packets, "
        f"{capture_stats.get('dropped', 0):,} dropped."
        if capture_stats
        else ""
    )
    log_system_event(
        admin_id,
        "Live Monitor",
        "monitor_completed" if final_state == "completed" else "monitor_stopped",
        "Success",
        message=(
            f"Live monitoring {final_state}: {totals['flows']:,} flows analysed, "
            f"{totals['attacks']:,} attacks detected, {totals['alerts']:,} alerts raised."
            + capture_note
        ),
        run_id=deployment_summary["run_id"],
        model_name=deployment_summary["model_name"],
    )
    return final_state


def start_session(
    admin_id,
    source_type=DEFAULT_SOURCE_TYPE,
    dataset=DEFAULT_DATASET,
    capture_file=None,
    interface=None,
    speed=DEFAULT_SPEED,
    order=DEFAULT_ORDER,
    persist=DEFAULT_PERSIST,
):
    """Start one monitoring session, refusing to run two at the same time."""
    global _SESSION, _THREAD, _STOP_EVENT, _PAUSE_EVENT

    source_type = str(source_type or DEFAULT_SOURCE_TYPE)
    dataset = str(dataset or DEFAULT_DATASET)
    speed = str(speed or DEFAULT_SPEED)
    order = str(order or DEFAULT_ORDER)
    persist = str(persist or DEFAULT_PERSIST)

    if source_type not in SOURCE_CHOICES:
        raise LiveMonitorError("Select a valid traffic source type.")
    if speed not in SPEED_CHOICES:
        raise LiveMonitorError("Select a valid replay speed.")
    if order not in ORDER_CHOICES:
        raise LiveMonitorError("Select a valid replay order.")
    if persist not in PERSIST_CHOICES:
        raise LiveMonitorError("Select a valid storage option.")

    source_name = None
    if source_type == "csv":
        _resolve_dataset_path(dataset)
    elif source_type == "pcap":
        source_name = str(capture_file or "")
        _resolve_capture_path(source_name)
    else:
        available, reason = live_capture_available()
        if not available:
            raise LiveMonitorError(reason)
        source_name = str(interface).strip() if interface else None

    with _LOCK:
        if (_THREAD and _THREAD.is_alive()) or (_SESSION and _SESSION["state"] in ACTIVE_STATES):
            raise LiveMonitorError("A monitoring session is already running. Stop it first.")
        _EVENTS.clear()
        _SESSION = _new_session(source_type, source_name, dataset, speed, order, persist, admin_id)
        _STOP_EVENT = threading.Event()
        _PAUSE_EVENT = threading.Event()
        _THREAD = threading.Thread(
            target=_worker,
            args=(_SESSION, _STOP_EVENT, _PAUSE_EVENT),
            name="algoguard-live-monitor",
            daemon=True,
        )
        _THREAD.start()
        return _public_session(_SESSION)


def pause_session():
    with _LOCK:
        if not _SESSION or _SESSION["state"] != "running":
            raise LiveMonitorError("No running monitoring session to pause.")
        _PAUSE_EVENT.set()
        _SESSION["state"] = "paused"
        return _public_session(_SESSION)


def resume_session():
    with _LOCK:
        if not _SESSION or _SESSION["state"] != "paused":
            raise LiveMonitorError("No paused monitoring session to resume.")
        _PAUSE_EVENT.clear()
        _SESSION["state"] = "running"
        return _public_session(_SESSION)


def stop_session():
    """Signal the worker to stop and wait briefly for it to finish."""
    with _LOCK:
        session = _SESSION
        stop_event = _STOP_EVENT
        pause_event = _PAUSE_EVENT
        thread = _THREAD
        if not session or session["state"] in TERMINAL_STATES:
            raise LiveMonitorError("No monitoring session is running.")
        stop_event.set()
        pause_event.clear()

    if thread and thread.is_alive():
        thread.join(timeout=5)
    with _LOCK:
        if session["state"] in ACTIVE_STATES:
            session["state"] = "stopping" if thread and thread.is_alive() else "stopped"
        return _public_session(session)


def get_status(since_seq=0, session_id=None):
    """Return the session snapshot plus classification events newer than ``since_seq``."""
    try:
        since_seq = int(since_seq or 0)
    except (TypeError, ValueError):
        since_seq = 0

    with _LOCK:
        session = _public_session(_SESSION)
        last_seq = _SESSION["seq"] if _SESSION else 0
        if (session_id is not None and session_id != session["session_id"]) or since_seq > last_seq:
            since_seq = 0
        events = [event for event in _EVENTS if event["seq"] > since_seq]
        if len(events) > MAX_EVENTS_PER_POLL:
            events = events[-MAX_EVENTS_PER_POLL:]
    return {"session": session, "events": events, "last_seq": last_seq}


def get_options():
    """Return the control choices the monitor page renders."""
    live_ok, live_reason = live_capture_available()
    return {
        "sources": [{"value": value, "label": label} for value, label in SOURCE_CHOICES.items()],
        "datasets": [{"value": value, "label": label} for value, label in DATASET_CHOICES.items()],
        "captures": list_capture_files(),
        "interfaces": list_capture_interfaces() if live_ok else [],
        "live_capture": {"available": live_ok, "reason": live_reason},
        "speeds": [
            {"value": "slow", "label": "Slow - 1 flow every 2s"},
            {"value": "normal", "label": "Normal - 1 flow per second"},
            {"value": "fast", "label": "Fast - about 3 flows per second"},
        ],
        "orders": [{"value": value, "label": label} for value, label in ORDER_CHOICES.items()],
        "persistence": [
            {"value": value, "label": label} for value, label in PERSIST_CHOICES.items()
        ],
        "defaults": {
            "source_type": DEFAULT_SOURCE_TYPE,
            "dataset": DEFAULT_DATASET,
            "speed": DEFAULT_SPEED,
            "order": DEFAULT_ORDER,
            "persist": DEFAULT_PERSIST,
        },
    }


def reset_for_tests():
    """Stop any running session and clear state. Intended for the test suite."""
    global _SESSION, _THREAD, _STOP_EVENT, _PAUSE_EVENT

    with _LOCK:
        stop_event = _STOP_EVENT
        pause_event = _PAUSE_EVENT
        thread = _THREAD
    if stop_event:
        stop_event.set()
    if pause_event:
        pause_event.clear()
    if thread and thread.is_alive():
        thread.join(timeout=5)
    with _LOCK:
        _SESSION = None
        _THREAD = None
        _STOP_EVENT = None
        _PAUSE_EVENT = None
        _EVENTS.clear()
