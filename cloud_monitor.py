"""Cloud-mode Live Monitor: local inference, pinned deployment, nonblocking persistence.

Stage 5D.3. The browser contract is the one ``templates/monitor.html`` already
uses with ``services.live_monitor_service`` (``session``/``events``/``last_seq``)
plus cloud fields, so the same page serves both modes:

* each persistent flow becomes one immutable event handed to the bounded
  outbox; cloud latency never blocks the classification loop;
* the verified deployment resolved at start is pinned for the whole capture;
* stop closes the capture at once, drains the local handoff for at most five
  seconds, and reports a terminal capture state independently of upload;
* the capture ends locally when its sign-in expires without refresh, when the
  user logs out, or when node access is revoked;
* a final capture summary is queued in reserved capacity and reconciled by the
  upload worker, which can never reopen a closed capture.
"""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from contextlib import ExitStack
from datetime import datetime, timezone
from uuid import UUID, uuid4

import numpy as np
import pandas as pd

from cloud_repository import RepositoryError
from cloud_sync import audit
from model_runtime import ModelDeliveryError
from services import live_monitor_service as legacy
from services.traffic_source_service import (
    LiveCaptureSource,
    TrafficSourceCancelled,
    TrafficSourceError,
    live_capture_available,
)

LiveMonitorError = legacy.LiveMonitorError
FINAL_PERSISTENCE = {"synced", "rejected", "dropped", "session_only"}
DRAIN_SECONDS = 5
TRACKED_UPDATES = 50


def _utc_iso():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _legacy_stamp(iso):
    return iso[:19].replace("T", " ")


def _port(value):
    try:
        port = int(value)
    except (TypeError, ValueError):
        return None
    return port if 0 <= port <= 65535 else None


def flow_event(deployment, result, flow_data, source_tag, capture_id, endpoints=None):
    """One immutable store_flow_batch event; its UUID is fixed before any upload."""
    endpoints = endpoints or {}
    event_time = _utc_iso()
    features = {key: legacy._json_safe(value) for key, value in flow_data.items()}
    body = {
        "event_uuid": str(uuid4()),
        "event_time": event_time,
        "model_id": int(deployment["model_id"]),
        "deployment_id": int(deployment["deployment_id"]),
        "capture_id": capture_id,
        "traffic": {
            "source_ip": endpoints.get("source_ip"),
            "destination_ip": endpoints.get("destination_ip"),
            "source_port": _port(endpoints.get("source_port")),
            "destination_port": _port(endpoints.get("destination_port")),
            "protocol": endpoints.get("protocol") or features.get("proto"),
            "dataset_source": source_tag,
            "feature_payload": json.dumps(features, allow_nan=False, sort_keys=True),
        },
        "prediction": {
            "predicted_label": result["prediction"],
            "confidence_score": result["confidence"],
            "latency_ms": result["latency_ms"],
            "model_name": deployment.get("model_name") or "Stacking Ensemble",
        },
    }
    if result["prediction"] == "Attack":
        body["alert"] = {
            "severity_level": "High",
            "alert_status": "Open",
            "description": (
                f"Attack detected by {body['prediction']['model_name']} "
                + ("during live monitoring." if capture_id else "in a manual prediction.")
            ),
        }
    return body


_RUN_IDS = {}


def deployment_summary(repo, pinned):
    """Display metadata for the pinned release; the manifest remains authoritative."""
    manifest = pinned.manifest
    summary = {
        "model_name": pinned.artifact.get("model_name") or "Stacking Ensemble",
        "model_id": manifest["model_id"],
        "deployment_id": manifest["deployment_id"],
        "manifest_id": manifest["manifest_id"],
        "object_sha256": manifest["object_sha256"],
        "run_id": None,
    }
    if manifest["manifest_id"] in _RUN_IDS:
        summary["run_id"] = _RUN_IDS[manifest["manifest_id"]]
        return summary
    try:
        rows = repo.list_records("model_summary", filters={"model_id": manifest["model_id"]}).rows
        summary["run_id"] = rows[0].get("run_id") if rows else None
        if len(_RUN_IDS) > 64:
            _RUN_IDS.clear()
        _RUN_IDS[manifest["manifest_id"]] = summary["run_id"]
    except RepositoryError:
        pass  # Display only; never blocks a capture that is otherwise authorized.
    return summary


class _AuthorizationEnded(Exception):
    pass


class CloudMonitor:
    def __init__(self, auth, outbox, sync, models, *, drain_seconds=DRAIN_SECONDS):
        self.auth = auth
        self.outbox = outbox
        self.sync = sync
        self.models = models
        self.drain_seconds = drain_seconds
        self.lock = threading.RLock()
        self.session = None
        self.thread = None
        self.stop_event = None
        self.pause_event = None
        self.source = None
        self.context = None
        self.events = deque(maxlen=legacy.FEED_MAXLEN)

    # -- control -------------------------------------------------------------

    def start(
        self,
        identity,
        source_type=legacy.DEFAULT_SOURCE_TYPE,
        dataset=legacy.DEFAULT_DATASET,
        capture_file=None,
        interface=None,
        speed=legacy.DEFAULT_SPEED,
        order=legacy.DEFAULT_ORDER,
        persist=legacy.DEFAULT_PERSIST,
    ):
        source_type = str(source_type or legacy.DEFAULT_SOURCE_TYPE)
        dataset = str(dataset or legacy.DEFAULT_DATASET)
        speed = str(speed or legacy.DEFAULT_SPEED)
        order = str(order or legacy.DEFAULT_ORDER)
        persist = str(persist or legacy.DEFAULT_PERSIST)
        if source_type not in legacy.SOURCE_CHOICES:
            raise LiveMonitorError("Select a valid traffic source type.")
        if speed not in legacy.SPEED_CHOICES:
            raise LiveMonitorError("Select a valid replay speed.")
        if order not in legacy.ORDER_CHOICES:
            raise LiveMonitorError("Select a valid replay order.")
        if persist not in legacy.PERSIST_CHOICES:
            raise LiveMonitorError("Select a valid storage option.")
        source_name = None
        if source_type == "csv":
            legacy._resolve_dataset_path(dataset)
        elif source_type == "pcap":
            source_name = str(capture_file or "")
            legacy._resolve_capture_path(source_name)
        else:
            available, reason = live_capture_available()
            if not available:
                raise LiveMonitorError(reason)
            source_name = str(interface).strip() if interface else None
        if not identity.active or time.time() >= identity.expires_at:
            raise LiveMonitorError("Log in online before starting a capture.")

        with self.lock:
            if self.session and self.session["state"] in legacy.ACTIVE_STATES:
                raise LiveMonitorError("A monitoring session is already running. Stop it first.")
            if self.thread and self.thread.is_alive():
                raise LiveMonitorError(
                    "The previous capture is still releasing its resources. Try again shortly."
                )
            self.events.clear()
            session = legacy._new_session(
                source_type, source_name, dataset, speed, order, persist, identity.profile_id
            )
            session.update(
                session_id=str(uuid4()),
                owner=str(identity.user_id),
                identity=identity,
                capture_id=None,
                storage_stopped=False,
                authorization_ends_at=identity.expires_at,
                stopped_at=None,
            )
            self.session = session
            self.context = None
            self.source = None
            self.stop_event = threading.Event()
            self.pause_event = threading.Event()
            self.sync.register(identity)
            self.thread = threading.Thread(
                target=self._worker,
                args=(session, self.stop_event, self.pause_event),
                name="algoguard-cloud-monitor",
                daemon=True,
            )
            self.thread.start()
            return self._public(session)

    def _owned(self, identity):
        session = self.session
        if not session or session["owner"] != str(identity.user_id):
            return None
        return session

    def pause(self, identity):
        with self.lock:
            session = self._owned(identity)
            if not session or session["state"] != "running" or self.pause_event is None:
                raise LiveMonitorError("No running monitoring session to pause.")
            self.pause_event.set()
            session["state"] = "paused"
            return self._public(session)

    def resume(self, identity):
        with self.lock:
            session = self._owned(identity)
            if not session or session["state"] != "paused" or self.pause_event is None:
                raise LiveMonitorError("No paused monitoring session to resume.")
            self.pause_event.clear()
            session["state"] = "running"
            return self._public(session)

    def stop(self, identity):
        """Close the capture now; drain the local handoff for at most five seconds."""
        with self.lock:
            session = self._owned(identity)
            if not session or session["state"] in legacy.TERMINAL_STATES:
                raise LiveMonitorError("No monitoring session is running.")
            self._halt_locked(session)
            source = self.source
        if isinstance(source, LiveCaptureSource):
            source.close(wait=False)
        drained = self.outbox.drain(self.drain_seconds)
        with self.lock:
            public = self._public(session)
        public["local_drain_complete"] = drained
        return public

    def _halt_locked(self, session, message=None):
        if self.stop_event is None or self.pause_event is None:
            raise LiveMonitorError("No monitoring session is running.")
        self.stop_event.set()
        self.pause_event.clear()
        if session["state"] in legacy.ACTIVE_STATES:
            # Capture status is terminal at once, independent of pending uploads.
            session["state"] = "stopped"
            session["stopped_at"] = _utc_iso()
        if message and not session["error_message"]:
            session["error_message"] = message

    def stop_for_logout(self, identity):
        with self.lock:
            session = self._owned(identity)
            if not session or session["state"] in legacy.TERMINAL_STATES:
                return False
            self._halt_locked(session, "Capture stopped because the account signed out.")
            source = self.source
        if isinstance(source, LiveCaptureSource):
            source.close(wait=False)
        self.outbox.drain(self.drain_seconds)
        return True

    def shutdown(self):
        with self.lock:
            session = self.session
            if session and session["state"] in legacy.ACTIVE_STATES:
                self._halt_locked(session, "Capture stopped because AlgoGuard is closing.")
            source = self.source
            thread = self.thread
        if isinstance(source, LiveCaptureSource):
            source.close(wait=False)
        if thread:
            thread.join(timeout=self.drain_seconds)
        self.outbox.drain(self.drain_seconds)

    # -- status --------------------------------------------------------------

    def _public(self, session):
        public = legacy._public_session(session)
        public["totals"]["queued"] = public["totals"]["persisted"]
        public.update(
            cloud=True,
            capture_id=session.get("capture_id"),
            storage_stopped=session.get("storage_stopped", False),
            stopped_at=session.get("stopped_at"),
            authorization_ends_at=session.get("authorization_ends_at"),
        )
        return public

    def status(self, identity, since_seq=0, session_id=None):
        try:
            since_seq = int(since_seq or 0)
        except (TypeError, ValueError):
            since_seq = 0
        with self.lock:
            session = self._owned(identity)
            if session is None:
                public = legacy._public_session(None)
                public["cloud"] = True
                other = self.session
                public["other_account_active"] = bool(
                    other and other["state"] in legacy.ACTIVE_STATES
                )
                return {"session": public, "events": [], "last_seq": 0, "updates": {}}
            public = self._public(session)
            last_seq = session["seq"]
            if (session_id is not None and session_id != session["session_id"]) or (
                since_seq > last_seq
            ):
                since_seq = 0
            events = [dict(event) for event in self.events if event["seq"] > since_seq]
            events = events[-legacy.MAX_EVENTS_PER_POLL:]
            tracked = [
                event["event_uuid"]
                for event in list(self.events)[-TRACKED_UPDATES:]
                if event.get("persistence") not in FINAL_PERSISTENCE
            ]
            context = self.context
        updates = {}
        if context is not None:
            ids = {event["event_uuid"] for event in events if event.get("persistence")
                   not in FINAL_PERSISTENCE} | set(tracked)
            updates = self.outbox.statuses(context, ids) if ids else {}
            with self.lock:
                for event in self.events:
                    state = updates.get(event.get("event_uuid"))
                    if state:
                        event.update(state)
            for event in events:
                event.update(updates.get(event.get("event_uuid"), {}))
            public["synchronization"] = self.outbox.session_counts(context, session["session_id"])
        public["upload"] = self.sync.status(identity)
        return {"session": public, "events": events, "last_seq": last_seq, "updates": updates}

    # -- worker --------------------------------------------------------------

    def _fail(self, session, message):
        with self.lock:
            if not session["error_message"]:
                session["error_message"] = message

    def _worker(self, session, stop_event, pause_event):
        final_state = "error"
        resources = {"source": None}
        try:
            with ExitStack() as cleanup:
                final_state = self._run(session, stop_event, pause_event, cleanup, resources)
        except LiveMonitorError as error:
            final_state = "error"
            self._fail(session, str(error))
        except Exception as error:  # Never let the capture thread die silently.
            final_state = "error"
            self._fail(session, f"Monitoring stopped after an error: {error}")
        finally:
            source = resources["source"]
            stats = {}
            if source is not None:
                try:
                    stats = source.stats()
                except Exception:
                    stats = {}
            with self.lock:
                if source is not None and session["source_type"] == "live":
                    session["capture"] = stats
                if session["state"] in legacy.ACTIVE_STATES:
                    session["state"] = final_state
                terminal = session["state"]
                context = self.context if self.session is session else None
                self.source = None
            if context is not None and session["capture_id"]:
                summary = {
                    "capture_id": session["capture_id"],
                    "status": terminal if terminal in ("stopped", "completed", "error")
                    else "stopped",
                    "flows_emitted": session["totals"]["flows"],
                    "packets_captured": int(stats.get("packets", 0) or 0),
                    "packets_dropped": int(stats.get("dropped", 0) or 0),
                }
                try:
                    self.outbox.save_lifecycle(context, session["session_id"], summary)
                except (OSError, ValueError):
                    self._fail(session, "Capture stopped; its final summary could not be saved.")
                totals = session["totals"]
                audit(
                    self.outbox, context, "Live Monitor",
                    "monitor_completed" if terminal == "completed" else f"monitor_{terminal}",
                    "Failed" if terminal == "error" else "Success",
                    f"Live monitoring {terminal}: {totals['flows']:,} flows analysed, "
                    f"{totals['attacks']:,} attacks detected, {totals['persisted']:,} queued "
                    f"for upload, {session.get('dropped', 0):,} not stored."
                    + (f" Excluded own cloud packets: {stats.get('excluded_cloud', 0):,}."
                       if stats else ""),
                    (session.get("deployment") or {}).get("model_name"),
                )

    def _authorize(self, session):
        identity = session["identity"]
        try:
            repo = self.auth.repository(identity)
            if not repo.node_access():
                raise RepositoryError("permission")
        except RepositoryError as error:
            raise LiveMonitorError({
                "authentication": "Online login is required before monitoring can start.",
                "permission": "This installation needs an administrator-approved membership "
                              "before it can monitor.",
                "transient": "The cloud is unreachable. Monitoring needs an online, "
                             "authorized start; pending records stay on this computer.",
            }.get(error.category, "The cloud response could not be verified.")) from None
        return repo

    def _run(self, session, stop_event, pause_event, cleanup, resources):
        repo = self._authorize(session)
        try:
            pinned = self.models.resolve(repo)
        except (ModelDeliveryError, RepositoryError) as error:
            message = str(error) if isinstance(error, ModelDeliveryError) else (
                "The active model could not be verified. Reconnect and retry.")
            raise LiveMonitorError(message) from None
        if stop_event.is_set():
            return "stopped"
        deployment = deployment_summary(repo, pinned)
        artifact = pinned.artifact
        feature_columns = list(artifact.get("feature_columns") or [])
        numeric_columns = set(artifact.get("numeric_columns") or [])
        defaults = artifact.get("feature_defaults") or {}
        pipeline = artifact.get("pipeline")
        source_type = session["source_type"]
        source_tag = "live_capture" if source_type == "live" else "live_monitor"

        try:
            source = legacy._make_source(session, cancel_event=stop_event)
            resources["source"] = source
            with self.lock:
                self.source = source
            cleanup.callback(
                lambda: source.close(wait=False)
                if isinstance(source, LiveCaptureSource) else source.close()
            )
            if source_type == "csv":
                source.prepare()
            missing = [column for column in feature_columns if column not in source.columns]
            if missing:
                raise LiveMonitorError(
                    "The traffic source is missing features the deployed model requires: "
                    + ", ".join(missing) + "."
                )
            if source_type != "csv":
                source.prepare()
        except TrafficSourceCancelled:
            return "stopped"
        except (LiveMonitorError, TrafficSourceError) as error:
            self._fail(session, str(error))
            return "error"

        try:
            warmup = legacy._build_flow_row(dict(defaults), feature_columns, numeric_columns,
                                            defaults)
            pipeline.predict(pd.DataFrame([warmup], columns=feature_columns))
        except Exception:
            pass
        if stop_event.is_set():
            return "stopped"

        try:
            capture_id = repo.open_capture(
                UUID(session["session_id"]), int(deployment["deployment_id"]), source_type,
                getattr(source, "bpf_filter", "") or "",
            )
        except RepositoryError as error:
            raise LiveMonitorError(
                "The capture could not be registered in the cloud "
                f"({error.category}). Reconnect and start again."
            ) from None

        with self.lock:
            # Recorded before the stop check so a stop during startup still
            # queues a terminal summary for the capture row just opened.
            self.context = repo.context
            session["capture_id"] = capture_id
            session["deployment"] = deployment
            session["dropped"] = 0
            if stop_event.is_set():
                return "stopped"
            session["row_total"] = int(source.row_total or 0)
            session["state"] = "running"
        self.outbox.recheck_capacity()
        audit(
            self.outbox, repo.context, "Live Monitor", "monitor_started", "Success",
            f"Live monitoring started on {session['dataset_label']} with pinned deployment "
            f"#{deployment['deployment_id']}.",
            deployment["model_name"],
        )
        return self._loop(session, stop_event, pause_event, repo.context, source, pipeline,
                          feature_columns, numeric_columns, defaults, deployment, source_tag)

    def _check_authorization(self, session):
        identity = session["identity"]
        if not identity.active:
            raise _AuthorizationEnded(
                "Your sign-in ended. Capture stopped locally; log in online to continue.")
        if time.time() >= identity.expires_at:
            # Refresh did not happen before expiry (the upload worker refreshes
            # ahead of time while online), so the sign-in ends here as well: the
            # operating table requires an online login before anything resumes.
            identity.clear()
            raise _AuthorizationEnded(
                "Your sign-in expired and could not be refreshed. Capture stopped locally; "
                "pending records stay on this computer. Log in online to continue.")
        if self.sync.blocked.get(session["owner"]) == "permission":
            raise _AuthorizationEnded(
                "Access to this node was revoked or is not approved. Capture stopped; pending "
                "records stay on this computer and can be exported.")

    def _loop(self, session, stop_event, pause_event, context, source, pipeline, feature_columns,
              numeric_columns, defaults, deployment, source_tag):
        source_type = session["source_type"]
        final_state = "completed"
        row_index = -1
        last_refresh = 0.0
        try:
            while True:
                if stop_event.is_set():
                    return "stopped"
                while pause_event.is_set():
                    if stop_event.wait(0.2):
                        return "stopped"
                    self._check_authorization(session)
                self._check_authorization(session)
                try:
                    event_data = source.next_event(timeout=0.2)
                except TrafficSourceCancelled:
                    return "stopped"
                except StopIteration:
                    break
                if event_data is None:
                    if source_type == "live" and time.time() - last_refresh >= 1.0:
                        last_refresh = time.time()
                        snapshot = source.stats()
                        with self.lock:
                            session["capture"] = snapshot
                    continue
                row_index += 1
                record = event_data["record"]
                actual = event_data["actual"]
                flow_row = legacy._build_flow_row(record, feature_columns, numeric_columns,
                                                  defaults)
                frame = pd.DataFrame([flow_row], columns=feature_columns)
                started = time.perf_counter()
                predicted = int(pipeline.predict(frame)[0])
                probabilities = np.asarray(pipeline.predict_proba(frame))[0]
                classes = list(pipeline.classes_)
                confidence = (
                    float(probabilities[classes.index(predicted)]) if predicted in classes else 0.0
                )
                latency_ms = (time.perf_counter() - started) * 1000
                prediction = "Attack" if predicted == 1 else "Normal"
                lag_ms = None
                if source_type == "live" and event_data.get("flow_last_ts"):
                    lag_ms = max((time.time() - event_data["flow_last_ts"]) * 1000, 0.0)
                result = {
                    "prediction": prediction,
                    "confidence": round(confidence * 100, 2),
                    "latency_ms": round(latency_ms, 3),
                }
                event = flow_event(deployment, result, flow_row, source_tag,
                                   session["capture_id"], event_data)
                if stop_event.is_set():
                    return "stopped"  # Never add work after the capture closed.
                if legacy._should_persist(session["persist"], prediction):
                    outcome = self.outbox.submit(context, session["session_id"], event)
                else:
                    outcome = {"event_uuid": event["event_uuid"], "persistence": "session_only"}
                self._record(session, event, event_data, record, row_index, result, actual,
                             lag_ms, outcome, source)
                if source.paced and stop_event.wait(legacy.SPEED_CHOICES[session["speed"]]):
                    return "stopped"
        except _AuthorizationEnded as ended:
            with self.lock:
                self._halt_locked(session, str(ended))
            return "stopped"
        with self.lock:
            if source_type == "pcap" and final_state == "completed":
                session["row_total"] = session["totals"]["flows"]
        return final_state

    def _record(self, session, event, event_data, record, row_index, result, actual, lag_ms,
                outcome, source):
        with self.lock:
            totals = session["totals"]
            persistence = outcome["persistence"]
            if persistence == "in_memory":
                totals["persisted"] += 1
                if result["prediction"] == "Attack":
                    totals["alerts"] += 1
            elif persistence == "dropped":
                session["dropped"] = session.get("dropped", 0) + 1
                if outcome.get("reason") == "session_cap":
                    session["capped"] = True
                elif outcome.get("reason") in ("storage", "handoff_full"):
                    session["storage_stopped"] = outcome.get("reason") == "storage"
            totals["flows"] += 1
            totals["attacks" if result["prediction"] == "Attack" else "normals"] += 1
            if actual is not None:
                totals["labelled"] += 1
                if actual != result["prediction"]:
                    totals["mismatches"] += 1
            session["latency_total_ms"] += result["latency_ms"]
            session["avg_latency_ms"] = session["latency_total_ms"] / totals["flows"]
            if lag_ms is not None:
                session["lag_total_ms"] += lag_ms
                session["avg_detection_lag_ms"] = session["lag_total_ms"] / totals["flows"]
            if session["source_type"] == "live":
                session["capture"] = source.stats()
            session["seq"] += 1
            self.events.append({
                "seq": session["seq"],
                "timestamp": _legacy_stamp(event["event_time"]),
                "row_index": row_index,
                "prediction": result["prediction"],
                "confidence": result["confidence"],
                "latency_ms": result["latency_ms"],
                "detection_lag_ms": round(lag_ms, 1) if lag_ms is not None else None,
                "actual": actual,
                "match": None if actual is None else actual == result["prediction"],
                "proto": str(record.get("proto", "")),
                "service": str(record.get("service", "")),
                "state": str(record.get("state", "")),
                "sbytes": legacy._json_safe(record.get("sbytes")),
                "dbytes": legacy._json_safe(record.get("dbytes")),
                "source_ip": event_data.get("source_ip"),
                "destination_ip": event_data.get("destination_ip"),
                "source_port": event_data.get("source_port"),
                "destination_port": event_data.get("destination_port"),
                "end_reason": event_data.get("end_reason"),
                "event_uuid": event["event_uuid"],
                "persistence": persistence,
                "reason": outcome.get("reason"),
                "prediction_id": None,
                "alert_id": None,
            })

    def reset_for_tests(self):
        with self.lock:
            session = self.session
            if session and session["state"] in legacy.ACTIVE_STATES:
                self._halt_locked(session)
            thread = self.thread
        if thread and thread.is_alive():
            thread.join(timeout=5)
