"""An in-process fake of the Supabase surfaces the Stage 5D cloud app uses.

Offline tests only. It speaks real HTTP to the real CloudAuth, CloudRepository,
ModelCache, outbox, and upload worker, and signs real ES256 access tokens that
token_verification checks against a published JWKS. Row visibility follows a
simplified version of the 5B policies (approved memberships; Administrators
read every node). It is NOT evidence about PostgreSQL, RLS, PostgREST, Storage,
or the Edge Function: the integration lane (``pytest -m integration`` against
``supabase start``) remains the acceptance test for those.
"""

from __future__ import annotations

import hashlib
import http.server
import itertools
import json
import re
import secrets
import threading
import time
import urllib.parse
import uuid
from datetime import datetime, timezone

import jwt
from cryptography.hazmat.primitives.asymmetric import ec
from jwt.algorithms import ECAlgorithm

TABLES = (
    "profile", "user_role", "node", "node_membership", "capture_session", "network_traffic",
    "prediction", "alert", "report", "report_alert", "system_log", "ingest_event",
    "model_manifest", "model_summary", "deployment_summary", "training_summary",
)
KEYS = {
    "profile": "profile_id", "node_membership": "membership_id", "capture_session": "capture_id",
    "network_traffic": "traffic_id", "prediction": "prediction_id", "alert": "alert_id",
    "report": "report_id", "system_log": "log_id", "model_manifest": "manifest_id",
}
NODE_SCOPED = {
    "capture_session", "network_traffic", "prediction", "alert", "report", "report_alert",
    "system_log", "ingest_event", "alert_detail",
}


class Refusal(Exception):
    def __init__(self, status, message="refused"):
        self.status = status
        self.message = message


def _now_text():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _split_top(text):
    """Split a PostgREST logic list on commas outside double quotes."""
    parts, current, quoted, escaped = [], "", False, False
    for character in text:
        if escaped:
            current += character
            escaped = False
        elif character == "\\" and quoted:
            current += character
            escaped = True
        elif character == '"':
            quoted = not quoted
            current += character
        elif character == "," and not quoted:
            parts.append(current)
            current = ""
        else:
            current += character
    if current:
        parts.append(current)
    return parts


def _unquote(value):
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return re.sub(r"\\(.)", r"\1", value[1:-1])
    return value


def _matches(row, column, operator, value):
    actual = row.get(column)
    if operator == "eq":
        if isinstance(actual, bool):
            return str(actual).lower() == value.lower()
        return actual is not None and str(actual) == value
    if operator == "in":
        options = {_unquote(item) for item in _split_top(value.strip("()"))}
        return actual is not None and str(actual) in options
    if operator in ("gte", "lte"):
        if actual is None:
            return False
        return str(actual) >= value if operator == "gte" else str(actual) <= value
    if operator == "ilike":
        if actual is None:
            return False
        pattern = "^" + ".*".join(re.escape(part) for part in value.split("*")) + "$"
        return re.match(pattern, str(actual), re.I | re.S) is not None
    raise Refusal(400, "unsupported operator")


def _condition(item):
    column, operator, value = item.split(".", 2)
    return column, operator, _unquote(value)


class FakeSupabase:
    publishable_key = "sb_publishable_fake"

    def __init__(self):
        self.signing_key = ec.generate_private_key(ec.SECP256R1())
        self.kid = "fake-es256"
        self.lock = threading.RLock()
        self.tables = {name: [] for name in TABLES}
        self.counters = {name: itertools.count(1) for name in TABLES}
        self.users = {}
        self.refresh_tokens = {}
        self.admin_requests = {}
        self.objects = {}
        self.signed = {}
        self.faults = []
        self.offline = False
        self.access_ttl = 3600
        self.calls = []
        self.logouts = []
        self.refresh_grants = 0
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    # -- lifecycle -----------------------------------------------------------

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

    # -- fixtures ------------------------------------------------------------

    def insert(self, table, values):
        with self.lock:
            row = dict(values)
            key = KEYS.get(table)
            if key and row.get(key) is None:
                row[key] = next(self.counters[table])
            row.setdefault("created_at", datetime.now(timezone.utc).isoformat())
            self.tables[table].append(row)
            return row

    def add_user(self, email, password, username, role="analyst", active=True):
        with self.lock:
            user_id = str(uuid.uuid4())
            profile = self.insert("profile", {
                "auth_user_id": user_id, "username": username, "email": email.lower(),
                "is_active": active, "legacy_admin_id": None,
            })
            self.insert("user_role", {"profile_id": profile["profile_id"], "role": role})
            self.users[email.lower()] = {"user_id": user_id, "password": password,
                                         "profile_id": profile["profile_id"]}
            return profile

    def add_historical_profile(self, username):
        return self.insert("profile", {"auth_user_id": None, "username": username,
                                       "email": None, "is_active": True})

    def approve(self, profile_id, node_id, *, default=True):
        with self.lock:
            node_id = str(node_id)
            node = self._node(node_id)
            if node is None:
                node = self.insert("node", {"node_id": node_id, "display_name": "fixture",
                                            "hostname_hint": None, "status": "approved"})
            node["status"] = "approved"
            membership = self._membership(profile_id, node_id)
            if membership is None:
                membership = self.insert("node_membership", {
                    "node_id": node_id, "profile_id": profile_id, "status": "approved",
                    "is_default": default, "requested_at": _now_text(), "decided_at": None,
                })
            membership.update(status="approved", decided_at=_now_text())
            return membership

    def revoke(self, profile_id, node_id):
        with self.lock:
            membership = self._membership(profile_id, str(node_id))
            assert membership is not None
            membership.update(status="revoked", is_default=False, decided_at=_now_text())

    def publish_model(self, content, compatibility):
        with self.lock:
            digest = hashlib.sha256(content).hexdigest()
            path = f"releases/{uuid.uuid4()}/{digest}.joblib"
            self.objects[path] = content
            for manifest in self.tables["model_manifest"]:
                if manifest["status"] == "active":
                    manifest["status"] = "superseded"
            run = self.insert("training_summary", {"run_id": len(self.tables["training_summary"])
                                                   + 1, "filename": "algoguard_big.csv"})
            model_id = len(self.tables["model_summary"]) + 7
            self.insert("model_summary", {
                "model_id": model_id, "run_id": run["run_id"], "model_name": "Stacking Ensemble",
                "model_type": "Stacking", "accuracy": 97.5, "precision_score": 96.0,
                "recall": 98.0, "f1_score": 97.0, "roc_auc": 99.0, "fpr": 2.0,
                "model_size": 1.5,
            })
            deployment_id = len(self.tables["deployment_summary"]) + 11
            self.insert("deployment_summary", {
                "deployment_id": deployment_id, "model_id": model_id, "run_id": run["run_id"],
                "deployed_at": _now_text(), "is_active": 1,
            })
            return self.insert("model_manifest", {
                **compatibility, "model_id": model_id, "deployment_id": deployment_id,
                "object_path": path, "object_sha256": digest, "object_bytes": len(content),
                "status": "active", "activated_at": datetime.now(timezone.utc).isoformat(),
            })

    def token_for(self, email, ttl=None):
        user = self.users[email.lower()]
        return self._mint(user["user_id"], email, ttl)

    def fail(self, method, prefix, *, status=None, drop=False, after=False, times=1):
        """Inject failures: an HTTP status, a dropped connection, or a lost response."""
        with self.lock:
            self.faults.append({"method": method, "prefix": prefix, "status": status,
                                "drop": drop, "after": after, "times": times})

    def rows(self, table):
        with self.lock:
            return [dict(row) for row in self.tables[table]]

    def count_calls(self, method, prefix):
        with self.lock:
            return sum(1 for m, path in self.calls if m == method and path.startswith(prefix))

    # -- auth helpers --------------------------------------------------------

    def _mint(self, user_id, email, ttl=None):
        now = int(time.time())
        claims = {
            "sub": user_id, "aud": "authenticated", "iss": f"{self.url}/auth/v1",
            "iat": now, "exp": now + int(self.access_ttl if ttl is None else ttl),
            "email": email, "role": "authenticated",
        }
        return jwt.encode(claims, self.signing_key, algorithm="ES256",
                          headers={"kid": self.kid})

    def _jwks(self):
        jwk = json.loads(ECAlgorithm.to_jwk(self.signing_key.public_key()))
        jwk.update(kid=self.kid, alg="ES256", use="sig")
        return {"keys": [jwk]}

    def _subject(self, headers):
        if headers.get("apikey") != self.publishable_key:
            raise Refusal(401, "invalid api key")
        bearer = headers.get("Authorization", "")
        if not bearer.startswith("Bearer "):
            raise Refusal(401, "missing token")
        token = bearer[7:]
        if token == self.publishable_key:
            return None
        try:
            claims = jwt.decode(token, self.signing_key.public_key(), algorithms=["ES256"],
                                audience="authenticated", issuer=f"{self.url}/auth/v1")
        except jwt.InvalidTokenError:
            raise Refusal(401, "invalid token") from None
        return claims["sub"]

    # -- simplified row security ----------------------------------------------

    def _profile(self, subject):
        return next((row for row in self.tables["profile"] if row.get("auth_user_id") == subject
                     and row["is_active"]), None)

    def _node(self, node_id):
        return next((row for row in self.tables["node"] if row["node_id"] == node_id), None)

    def _membership(self, profile_id, node_id):
        return next((row for row in self.tables["node_membership"]
                     if row["profile_id"] == profile_id and row["node_id"] == node_id), None)

    def _is_admin(self, profile):
        return profile is not None and any(
            row["profile_id"] == profile["profile_id"] and row["role"] == "administrator"
            for row in self.tables["user_role"])

    def _is_operator(self, profile):
        return profile is not None and any(row["profile_id"] == profile["profile_id"]
                                           for row in self.tables["user_role"])

    def _can_write(self, profile, node_id):
        if profile is None:
            return False
        membership = self._membership(profile["profile_id"], str(node_id))
        node = self._node(str(node_id))
        return bool(membership and membership["status"] == "approved" and node
                    and node["status"] == "approved")

    def _can_read(self, profile, node_id):
        return self._is_admin(profile) or self._can_write(profile, node_id)

    def _visible(self, table, profile):
        if profile is None:
            return []
        if table == "alert_detail":
            rows = []
            for alert in self.tables["alert"]:
                prediction = next(p for p in self.tables["prediction"]
                                  if p["prediction_id"] == alert["prediction_id"])
                traffic = next(t for t in self.tables["network_traffic"]
                               if t["traffic_id"] == prediction["traffic_id"])
                rows.append({**alert, "predicted_label": prediction["predicted_label"],
                             "confidence_score": prediction["confidence_score"],
                             "model_name": prediction["model_name"],
                             "source_ip": traffic["source_ip"],
                             "destination_ip": traffic["destination_ip"]})
        else:
            rows = list(self.tables[table])
        if table in NODE_SCOPED:
            return [row for row in rows if self._can_read(profile, row["node_id"])]
        if table in ("profile", "user_role", "node_membership"):
            if self._is_admin(profile):
                return rows
            return [row for row in rows if row["profile_id"] == profile["profile_id"]]
        if table == "node":
            if self._is_admin(profile):
                return rows
            mine = {row["node_id"] for row in self.tables["node_membership"]
                    if row["profile_id"] == profile["profile_id"]}
            return [row for row in rows if row["node_id"] in mine]
        if table == "model_manifest":
            return [row for row in rows if self._is_operator(profile)
                    and row["status"] in ("published", "active", "superseded")]
        if table in ("model_summary", "deployment_summary", "training_summary"):
            return rows if self._is_operator(profile) else []
        raise Refusal(404, "unknown resource")

    # -- PostgREST reads -----------------------------------------------------

    def _select(self, table, query, profile):
        rows = self._visible(table, profile)
        for name, value in query:
            if name in ("select", "order", "limit", "offset"):
                continue
            if name in ("and", "or"):
                conditions = [_condition(item) for item in _split_top(value.strip()[1:-1])]
                check = all if name == "and" else any
                rows = [row for row in rows
                        if check(_matches(row, *condition) for condition in conditions)]
                continue
            operator, _, operand = value.partition(".")
            rows = [row for row in rows if _matches(row, name, operator, operand)]
        order = dict(query).get("order")
        if order:
            for part in reversed(order.split(",")):
                column, _, direction = part.partition(".")
                rows.sort(key=lambda row: (row.get(column) is None, row.get(column)),
                          reverse=direction == "desc")
        offset = int(dict(query).get("offset", 0))
        limit = int(dict(query).get("limit", 1000))
        return rows[offset:offset + limit]

    # -- RPC -----------------------------------------------------------------

    def _rpc(self, name, body, profile):
        handler = getattr(self, "_rpc_" + name, None)
        if handler is None:
            raise Refusal(404, "unknown function")
        if profile is None:
            raise Refusal(401, "authentication required")
        return handler(body, profile)

    def _require_writer(self, body, profile, node_key="p_node_id"):
        if body.get("p_profile_id") != profile["profile_id"] or not self._can_write(
                profile, body.get(node_key)):
            raise Refusal(403, "access denied")

    def _rpc_current_node_access(self, body, profile):
        return self._can_write(profile, body["p_node_id"])

    def _rpc_request_enrollment(self, body, profile):
        node_id = str(uuid.UUID(body["p_node_id"]))
        if self._node(node_id) is None:
            self.insert("node", {"node_id": node_id, "display_name": body["p_display_name"],
                                 "hostname_hint": body.get("p_hostname_hint"),
                                 "status": "pending"})
        membership = self._membership(profile["profile_id"], node_id)
        if membership is None:
            membership = self.insert("node_membership", {
                "node_id": node_id, "profile_id": profile["profile_id"], "status": "pending",
                "is_default": False, "requested_at": _now_text(), "decided_at": None,
            })
        return {"node_id": node_id, "status": membership["status"],
                "is_default": membership["is_default"]}

    def _rpc_detection_statistics(self, body, profile):
        predictions = [row for row in self._visible("prediction", profile)
                       if row["node_id"] == body["p_node_id"]]
        alerts = [row for row in self._visible("alert", profile)
                  if row["node_id"] == body["p_node_id"]]
        latencies = [row["latency_ms"] for row in predictions]
        return {
            "total_flows": len(predictions),
            "attack_count": sum(row["predicted_label"] == "Attack" for row in predictions),
            "normal_count": sum(row["predicted_label"] == "Normal" for row in predictions),
            "avg_latency_ms": sum(latencies) / len(latencies) if latencies else None,
            "avg_confidence": None,
            "last_prediction_at": max((row["prediction_timestamp"] for row in predictions),
                                      default=None),
            "alerts_total": len(alerts),
            "alerts_open": sum(row["alert_status"] == "Open" for row in alerts),
        }

    def _rpc_traffic_source_counts(self, body, profile):
        counts = {}
        for row in self._visible("network_traffic", profile):
            if row["node_id"] == body["p_node_id"]:
                counts[row["dataset_source"]] = counts.get(row["dataset_source"], 0) + 1
        return [{"dataset_source": key, "flow_count": value} for key, value in counts.items()]

    def _rpc_system_log_filter_options(self, body, profile):
        rows = [row for row in self._visible("system_log", profile)
                if row["node_id"] == body["p_node_id"]]
        return {
            "modules": sorted({row["module"] for row in rows}),
            "statuses": sorted({row["status"] for row in rows}),
            "models": sorted({row["model_name"] for row in rows if row.get("model_name")}),
        }

    def _rpc_store_flow_batch(self, body, profile):
        self._require_writer(body, profile)
        events = body.get("p_events")
        if not isinstance(events, list) or not 1 <= len(events) <= 50:
            raise Refusal(400, "batch must contain 1 to 50 events")
        allowed = {"event_uuid", "event_time", "capture_id", "model_id", "deployment_id",
                   "traffic", "prediction", "alert"}
        plan = []
        for event in events:
            if not isinstance(event, dict) or set(event) - allowed or event.get(
                    "prediction", {}).get("predicted_label") not in ("Normal", "Attack"):
                raise Refusal(400, "invalid flow")
            if event.get("capture_id") is not None:
                capture = next((row for row in self.tables["capture_session"]
                                if row["capture_id"] == event["capture_id"]), None)
                if capture is None or capture["node_id"] != body["p_node_id"]:
                    raise Refusal(400, "invalid flow reference")
            previous = next((row for row in self.tables["ingest_event"]
                             if row["event_uuid"] == event["event_uuid"]), None)
            if previous is not None and (previous["payload"] != event
                                         or previous["node_id"] != body["p_node_id"]):
                raise Refusal(409, "event conflict")
            plan.append((event, previous))
        answer = []
        for event, previous in plan:
            if previous is None:
                stamp = event["event_time"][:19].replace("T", " ")
                traffic = self.insert("network_traffic", {
                    **event["traffic"], "node_id": body["p_node_id"],
                    "owner_profile_id": profile["profile_id"], "capture_id": event["capture_id"],
                    "timestamp": stamp,
                })
                prediction = self.insert("prediction", {
                    **event["prediction"], "node_id": body["p_node_id"],
                    "owner_profile_id": profile["profile_id"],
                    "traffic_id": traffic["traffic_id"], "model_id": event["model_id"],
                    "deployment_id": event["deployment_id"], "prediction_timestamp": stamp,
                    "alert_created": 1 if event.get("alert") else 0,
                })
                alert = None
                if event.get("alert"):
                    alert = self.insert("alert", {
                        "node_id": body["p_node_id"], "owner_profile_id": profile["profile_id"],
                        "prediction_id": prediction["prediction_id"],
                        "severity_level": event["alert"].get("severity_level", "High"),
                        "alert_status": event["alert"].get("alert_status", "Open"),
                        "description": event["alert"].get("description"), "detected_at": stamp,
                    })
                log = self.insert("system_log", {
                    "node_id": body["p_node_id"], "profile_id": profile["profile_id"],
                    "module": "detection", "action": "flow_accepted", "status": "success",
                    "message": None, "model_name": None, "run_id": None,
                    "prediction_id": prediction["prediction_id"], "timestamp": stamp,
                    "event_uuid": None, "dataset_filename": None, "ip_address": None,
                })
                previous = self.insert("ingest_event", {
                    "event_uuid": event["event_uuid"], "node_id": body["p_node_id"],
                    "owner_profile_id": profile["profile_id"], "payload": event,
                    "traffic_id": traffic["traffic_id"],
                    "prediction_id": prediction["prediction_id"],
                    "alert_id": alert["alert_id"] if alert else None,
                    "audit_log_id": log["log_id"], "fresh": True,
                })
                replayed = False
            else:
                replayed = True
            answer.append({
                "event_uuid": event["event_uuid"], "traffic_id": str(previous["traffic_id"]),
                "prediction_id": str(previous["prediction_id"]),
                "alert_id": None if previous["alert_id"] is None else str(previous["alert_id"]),
                "audit_log_id": str(previous["audit_log_id"]), "replayed": replayed,
                "persistence": "committed",
            })
        return answer

    def _rpc_open_cloud_capture(self, body, profile):
        self._require_writer(body, profile)
        if body.get("p_source") not in ("csv", "pcap", "live", "manual"):
            raise Refusal(400, "invalid capture")
        if not any(row["deployment_id"] == body["p_deployment_id"] and row["status"] == "active"
                   for row in self.tables["model_manifest"]):
            raise Refusal(409, "active model unavailable")
        capture = next((row for row in self.tables["capture_session"]
                        if row["capture_uuid"] == body["p_capture_uuid"]), None)
        if capture is None:
            capture = self.insert("capture_session", {
                "capture_uuid": body["p_capture_uuid"], "node_id": body["p_node_id"],
                "owner_profile_id": profile["profile_id"],
                "deployment_id": body["p_deployment_id"], "interface": body["p_source"],
                "bpf_filter": body.get("p_filter", ""), "started_at": _now_text(),
                "finished_at": None, "packets_captured": 0, "packets_dropped": 0,
                "flows_emitted": 0, "status": "running", "closed_at": None,
            })
        elif (capture["node_id"], capture["owner_profile_id"], capture["deployment_id"]) != (
                body["p_node_id"], profile["profile_id"], body["p_deployment_id"]):
            raise Refusal(409, "capture conflict")
        return {"capture_id": str(capture["capture_id"]), "status": capture["status"]}

    def _rpc_finalize_cloud_capture(self, body, profile):
        values = body.get("p_values") or {}
        state = values.get("status")
        if state not in ("stopped", "completed", "error"):
            raise Refusal(400, "invalid terminal state")
        capture = next((row for row in self.tables["capture_session"]
                        if row["capture_id"] == body["p_capture_id"]
                        and row["owner_profile_id"] == profile["profile_id"]), None)
        if capture is None:
            raise Refusal(403, "capture unavailable")
        if capture["closed_at"] is None:
            capture.update(status=state, closed_at=_now_text(), finished_at=_now_text())
        for key in ("packets_captured", "packets_dropped", "flows_emitted"):
            capture[key] = max(capture[key], int(values.get(key) or 0))
        return {"capture_id": str(capture["capture_id"]), "status": capture["status"]}

    def _rpc_create_operational_report(self, body, profile):
        self._require_writer(body, profile)
        selected = sorted(set(body.get("p_alert_ids") or []))
        if not 1 <= len(selected) <= 250:
            raise Refusal(400, "select 1 to 250 alerts")
        existing = next((row for row in self.tables["report"]
                         if row.get("report_uuid") == body["p_request_id"]), None)
        if existing:
            linked = sorted(row["alert_id"] for row in self.tables["report_alert"]
                            if row["report_id"] == existing["report_id"])
            if linked != selected or existing["owner_profile_id"] != profile["profile_id"]:
                raise Refusal(409, "report identity conflict")
            return {"report_id": str(existing["report_id"]), "replayed": True}
        available = {row["alert_id"] for row in self.tables["alert"]
                     if row["node_id"] == body["p_node_id"]}
        if not set(selected) <= available:
            raise Refusal(400, "selected evidence unavailable")
        report = self.insert("report", {
            "node_id": body["p_node_id"], "owner_profile_id": profile["profile_id"],
            "report_uuid": body["p_request_id"], "report_type": "Operational evidence",
            "generated_at": _now_text(), "run_id": None,
        })
        for alert_id in selected:
            self.insert("report_alert", {"report_id": report["report_id"], "alert_id": alert_id,
                                         "node_id": body["p_node_id"]})
        return {"report_id": str(report["report_id"]), "replayed": False}

    def _rpc_append_system_log(self, body, profile):
        self._require_writer(body, profile)
        values = body.get("p_values") or {}
        if set(values) - {"module", "action", "status", "message", "model_name", "timestamp"}:
            raise Refusal(400, "invalid log entry")
        existing = next((row for row in self.tables["system_log"]
                         if row.get("event_uuid") == body["p_event_uuid"]), None)
        if existing:
            if any(existing.get(key) != values.get(key) for key in values):
                raise Refusal(409, "log conflict")
            return {"log_id": str(existing["log_id"]), "replayed": True}
        row = self.insert("system_log", {
            **values, "node_id": body["p_node_id"], "profile_id": profile["profile_id"],
            "event_uuid": body["p_event_uuid"], "run_id": None, "prediction_id": None,
            "dataset_filename": None, "ip_address": None,
        })
        return {"log_id": str(row["log_id"]), "replayed": False}

    # -- Edge Function -------------------------------------------------------

    def _account_admin(self, body, profile):
        if not self._is_admin(profile):
            raise Refusal(403, "access_denied")
        request_id = body.get("request_id")
        stored = self.admin_requests.get(request_id)
        command = {key: value for key, value in body.items() if key != "request_id"}
        public = {key: value for key, value in command.items() if key != "password"}
        if stored is not None:
            if stored[0] != public:
                raise Refusal(409, "request_conflict")
            return stored[1]
        action = command.get("action")
        if action == "create_account":
            password = command.get("password", "")
            if not re.fullmatch(r"(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[^A-Za-z0-9]).{12,128}",
                                password or ""):
                raise Refusal(400, "invalid_account")
            if command["email"].lower() in self.users or any(
                    row["username"] == command["username"] for row in self.tables["profile"]):
                raise Refusal(409, "request_conflict")
            created = self.add_user(command["email"], password, command["username"],
                                    command.get("role", "analyst"))
            result = {"status": "completed", "profile_id": created["profile_id"]}
        elif action == "membership":
            if command.get("status") not in ("approved", "revoked"):
                raise Refusal(400, "invalid_request")
            if command["status"] == "approved":
                self.approve(command["profile_id"], command["node_id"],
                             default=bool(command.get("is_default")))
            else:
                self.revoke(command["profile_id"], command["node_id"])
            result = {"status": "completed"}
        elif action in ("role", "account_status"):
            target = command.get("profile_id")
            if target == profile["profile_id"]:
                raise Refusal(403, "access_denied")
            if action == "role":
                self.tables["user_role"] = [row for row in self.tables["user_role"]
                                            if row["profile_id"] != target]
                self.insert("user_role", {"profile_id": target, "role": command["role"]})
            else:
                for row in self.tables["profile"]:
                    if row["profile_id"] == target:
                        row["is_active"] = bool(command["is_active"])
            result = {"status": "completed"}
        else:
            raise Refusal(400, "invalid_request")
        self.admin_requests[request_id] = (public, result)
        return result

    # -- HTTP ----------------------------------------------------------------

    def _fault(self, method, path):
        with self.lock:
            for fault in self.faults:
                if fault["method"] == method and path.startswith(fault["prefix"]):
                    fault["times"] -= 1
                    if fault["times"] <= 0:
                        self.faults.remove(fault)
                    return fault
        return None

    def _dispatch(self, method, path, query, headers, body):
        if path == "/auth/v1/.well-known/jwks.json":
            return 200, self._jwks()
        if path == "/auth/v1/token":
            self._subject(headers)
            grant = dict(query).get("grant_type")
            if grant == "password":
                user = self.users.get(str(body.get("email", "")).lower())
                profile = user and next(row for row in self.tables["profile"]
                                        if row["profile_id"] == user["profile_id"])
                if not user or user["password"] != body.get("password"):
                    raise Refusal(400, "invalid_grant")
                if not profile or not profile["is_active"]:
                    raise Refusal(400, "user_banned")
                email = str(body["email"]).lower()
            elif grant == "refresh_token":
                self.refresh_grants += 1
                email = self.refresh_tokens.pop(body.get("refresh_token"), None)
                if email is None:
                    raise Refusal(400, "invalid_grant")
                user = self.users[email]
            else:
                raise Refusal(400, "unsupported_grant_type")
            refresh = secrets.token_urlsafe(24)
            self.refresh_tokens[refresh] = email
            return 200, {"access_token": self._mint(user["user_id"], email),
                         "refresh_token": refresh, "token_type": "bearer",
                         "expires_in": self.access_ttl}
        if path == "/auth/v1/logout":
            subject = self._subject(headers)
            self.logouts.append((subject, dict(query).get("scope")))
            return 204, None
        if path.startswith("/storage/v1/object/sign/models/"):
            object_path = urllib.parse.unquote(path[len("/storage/v1/object/sign/models/"):])
            if method == "POST":
                profile = self._profile(self._subject(headers))
                if not any(row["object_path"] == object_path
                           for row in self._visible("model_manifest", profile)):
                    raise Refusal(400, "object not found")
                token = secrets.token_urlsafe(16)
                self.signed[token] = (object_path, time.time() + int(body.get("expiresIn", 60)))
                quoted = urllib.parse.quote(object_path, safe="/")
                return 200, {"signedURL": f"/object/sign/models/{quoted}?token={token}"}
            token = dict(query).get("token")
            granted = self.signed.get(token)
            if not granted or granted[0] != object_path or granted[1] < time.time():
                raise Refusal(400, "invalid signature")
            return 200, self.objects[object_path]
        subject = self._subject(headers)
        profile = self._profile(subject) if subject else None
        if path == "/functions/v1/account_admin":
            if profile is None:
                raise Refusal(401, "authentication_required")
            return 200, self._account_admin(body, profile)
        if path.startswith("/rest/v1/rpc/"):
            return 200, self._rpc(path[len("/rest/v1/rpc/"):], body or {}, profile)
        if path.startswith("/rest/v1/") and method == "GET":
            if profile is None and subject is not None:
                return 200, []
            return 200, self._select(path[len("/rest/v1/"):], query, profile)
        raise Refusal(404, "not found")

    def _handler(self):
        fake = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format, *args):
                pass

            def _serve(self):
                parts = urllib.parse.urlsplit(self.path)
                query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                with fake.lock:
                    fake.calls.append((self.command, parts.path))
                    offline = fake.offline
                fault = fake._fault(self.command, parts.path)
                if offline or (fault and fault["drop"]):
                    self.close_connection = True
                    self.connection.shutdown(2)
                    return
                if fault and fault["status"] and not fault["after"]:
                    return self._reply(fault["status"], {"message": "injected"})
                try:
                    body = json.loads(raw) if raw else None
                    with fake.lock:
                        status, data = fake._dispatch(self.command, parts.path, query,
                                                      self.headers, body)
                except Refusal as refusal:
                    status, data = refusal.status, {"message": refusal.message}
                if fault and fault["after"]:
                    # Committed above, response lost: the classic lost acknowledgement.
                    return self._reply(fault["status"] or 503, {"message": "lost"})
                self._reply(status, data)

            def _reply(self, status, data):
                if isinstance(data, bytes):
                    payload, kind = data, "application/octet-stream"
                else:
                    payload = b"" if data is None else json.dumps(data).encode()
                    kind = "application/json"
                self.send_response(status)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Connection", "close")
                self.end_headers()
                if payload:
                    self.wfile.write(payload)
                self.close_connection = True

            do_GET = do_POST = do_PATCH = _serve

        return Handler
