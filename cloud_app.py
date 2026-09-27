"""Isolated cloud-pilot application (Stage 5D); never opens the legacy business database.

``app.py`` serves this application when ``ALGOGUARD_DB_MODE=supabase``. Every
read and write goes through the authenticated repository with the signed-in
user's token and the local installation's node ID; persistent detections go
through the bounded local outbox. No page falls back to SQLite, and no path
uses a privileged credential.

JSON API contract changes from the SQLite application (see
``docs/migration/05d-integration.md``):

* ``POST /predict`` answers with ``event_uuid`` and ``persistence``
  (``in_memory``, ``durable_pending``, ``dropped``, or ``rejected``);
  ``prediction_id``/``alert_id`` are ``null`` until upload is acknowledged.
  Poll ``GET /api/events/<event_uuid>`` for ``synced`` and the database IDs.
* Monitor status rows carry ``event_uuid``/``persistence`` and the session
  carries ``synchronization`` counts and ``upload`` state.
* JSON endpoints also accept ``Authorization: Bearer <access token>``
  (no cookie, no CSRF token); HTML pages accept only the browser session.
"""

from __future__ import annotations

import atexit
import hmac
import json
import re
import secrets
import socket
from collections import namedtuple
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from flask import Flask, flash, g, jsonify, redirect, render_template, request, url_for
from flask import session as flask_session

from cloud_auth import CloudAuth, MemorySession, MemorySessionInterface
from cloud_monitor import CloudMonitor, LiveMonitorError, deployment_summary, flow_event
from cloud_outbox import Outbox, restrict_owner
from cloud_repository import RepositoryError
from cloud_sync import SyncWorker, audit
from model_delivery import ActiveModelStore
from model_runtime import ModelDeliveryError, verify_manifest
from node_enrollment import local_node_id
from services.live_monitor_service import get_options
from services.simulation_service import (
    SimulationServiceError,
    predict_with_artifact,
    schema_for_artifact,
)

# Keep Flask's request-local proxy while exposing the cloud session's extra fields.
session = cast(MemorySession, flask_session)

JSON_ENDPOINTS = {
    "predict_api",
    "event_status",
    "monitor_start",
    "monitor_pause",
    "monitor_resume",
    "monitor_stop",
    "monitor_status",
    "outbox_export",
    "report_json",
}
MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
ERROR_MESSAGES = {
    "authentication": "Online login is required.",
    "permission": "Your account or this installation needs administrator approval.",
    "transient": "The cloud is unavailable. Pending records stay on this computer.",
    "conflict": "This request conflicts with an existing record.",
    "validation": "Check the submitted values.",
    "not_found": "The requested record is unavailable.",
}
ERROR_STATUS = {
    "authentication": 401,
    "permission": 403,
    "validation": 400,
    "conflict": 409,
    "not_found": 404,
}
PERSISTENCE_LABELS = {
    "session_only": "Session only (not stored)",
    "in_memory": "In memory only - not yet saved",
    "durable_pending": "Saved on this computer - pending upload",
    "synced": "Synced to the cloud",
    "dropped": "Not stored",
    "rejected": "Rejected by the cloud",
}
PASSWORD_RULE = re.compile(r"(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[^A-Za-z0-9]).{12,128}", re.S)
PAGE_SIZE = 50
LOG_PAGE_SIZE = 25
MAX_OFFSET = 1_000_000

OwnerContext = namedtuple("OwnerContext", "user_id profile_id node_id")


@dataclass
class CloudServices:
    node_id: UUID
    auth: CloudAuth
    outbox: Outbox
    sync: SyncWorker
    models: ActiveModelStore
    monitor: CloudMonitor

    def close(self):
        self.monitor.shutdown()
        self.sync.close()
        self.outbox.close()


def build_services(cfg):
    state_dir = Path(cfg.state_dir)
    restrict_owner(state_dir)
    node_id = local_node_id(state_dir / "node-id")
    auth = CloudAuth(cfg.supabase_url, cfg.supabase_publishable_key, node_id)
    outbox = Outbox(state_dir / "outbox")
    sync = SyncWorker(auth, outbox)
    models = ActiveModelStore(state_dir / "models")
    monitor = CloudMonitor(auth, outbox, sync, models)
    return CloudServices(node_id, auth, outbox, sync, models, monitor)


def _safe_next_url(next_url):
    if not next_url:
        return url_for("dashboard")
    try:
        parsed = urlsplit(next_url)
    except ValueError:
        return url_for("dashboard")
    if (
        parsed.scheme
        or parsed.netloc
        or not next_url.startswith("/")
        or next_url.startswith("//")
        or "\\" in next_url
        or any(character in next_url for character in ("\r", "\n"))
    ):
        return url_for("dashboard")
    return next_url


def _utc_text(value):
    """Canonical 'YYYY-MM-DD HH:MM:SS UTC' for a timestamptz string, else None."""
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return str(value)
    if stamp.tzinfo is not None:
        stamp = stamp.astimezone(timezone.utc)
    return stamp.strftime("%Y-%m-%d %H:%M:%S UTC")


def _owner(identity):
    """Outbox ownership without a network call or a token."""
    return OwnerContext(identity.user_id, identity.profile_id, identity.node_id)


def _offset():
    return min(max(0, request.args.get("offset", 0, type=int)), MAX_OFFSET)


def _form_uuid(name="request_id"):
    try:
        return UUID(request.form.get(name, ""))
    except ValueError:
        raise RepositoryError("validation") from None


def _parallel(tasks):
    """Run independent cloud reads together; ~100 ms each from Manila to the pilot."""
    with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
        futures = {name: pool.submit(task) for name, task in tasks.items()}
    results = {}
    for name, future in futures.items():
        try:
            results[name] = future.result()
        except Exception as error:  # Returned for the caller to classify.
            results[name] = error
    return results


def _raise_first(results, names):
    for name in names:
        if isinstance(results[name], Exception):
            raise results[name]


def create_cloud_app(cfg, *, services=None, start_workers=True):
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=secrets.token_hex(32),
        CLOUD_MODE=True,
        SESSION_COOKIE_NAME="algoguard_cloud_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=bool(cfg.secure_cookies),
        MAX_CONTENT_LENGTH=1024 * 1024,
    )
    session_interface = MemorySessionInterface()
    app.session_interface = session_interface
    services = services or build_services(cfg)
    app.extensions["algoguard_cloud"] = services
    if start_workers:
        services.sync.start()
        atexit.register(services.close)

    def csrf_token():
        token = session.get("_csrf_token")
        if not token:
            token = secrets.token_urlsafe(32)
            session["_csrf_token"] = token
        return token

    def csp_nonce():
        if not hasattr(g, "csp_nonce"):
            g.csp_nonce = secrets.token_urlsafe(18)
        return g.csp_nonce

    app.jinja_env.globals.update(csrf_token=csrf_token, csp_nonce=csp_nonce)

    def wants_json():
        return request.endpoint in JSON_ENDPOINTS or g.get("bearer", False)

    def json_error(message, status):
        return jsonify({"status": "error", "message": message}), status

    # -- request pipeline ------------------------------------------------------

    @app.before_request
    def authenticate():
        g.bearer = False
        g.identity = None
        if request.endpoint in (None, "static"):
            return None
        header = request.headers.get("Authorization")
        if header is not None:
            if request.endpoint not in JSON_ENDPOINTS:
                return json_error("Bearer tokens are accepted by the JSON API only.", 400)
            scheme, _, token = header.partition(" ")
            if scheme != "Bearer" or not token.strip():
                return json_error("Send Authorization: Bearer <access token>.", 401)
            g.bearer = True
            # A fresh identity per request: one caller's token can never be
            # swapped into another request or into the browser session.
            g.identity = services.auth.authenticate_access(token.strip())
        else:
            identity = session.identity
            if identity is not None and not identity.active:
                session.identity = None
                identity = None
            g.identity = identity

        if request.endpoint != "login" and g.identity is None:
            if wants_json():
                return json_error("Online login required.", 401)
            target = request.full_path if request.query_string else request.path
            return redirect(url_for("login", next=target))

        if request.method in MUTATING_METHODS and not g.bearer:
            expected = session.get("_csrf_token")
            provided = request.form.get("_csrf_token") or request.headers.get("X-CSRF-Token")
            if not (
                expected
                and provided
                and provided.isascii()
                and hmac.compare_digest(str(expected), provided)
            ):
                if wants_json():
                    return json_error("Invalid or expired request token.", 400)
                return "Invalid or expired request token.", 400

        if g.identity is not None and request.endpoint != "login":
            g.repo = services.auth.repository(g.identity)
            if not g.bearer:
                session.update(
                    admin_id=g.identity.profile_id,
                    admin_username=g.identity.username,
                    admin_role=g.identity.display_role,
                )

        if request.method == "POST" and request.endpoint in {"predict_api", "monitor_start"}:
            payload = request.get_json(silent=True)
            if not isinstance(payload, dict):
                return json_error(
                    "Send a valid JSON object with Content-Type application/json.", 400
                )
            g.json_payload = payload
        return None

    @app.after_request
    def security_headers(response):
        if request.endpoint != "static":
            response.headers["Cache-Control"] = (
                "no-store, no-cache, must-revalidate, max-age=0, private"
            )
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            f"script-src 'self' 'nonce-{csp_nonce()}'; "
            "style-src 'self' 'unsafe-inline'; "
            "font-src 'self' data:; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
            "base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), geolocation=(), microphone=()"
        return response

    @app.errorhandler(RepositoryError)
    def repository_error(error):
        message = ERROR_MESSAGES.get(error.category, "The cloud response could not be verified.")
        status = ERROR_STATUS.get(error.category, 503)
        if error.category == "authentication" and not g.get("bearer"):
            session.identity = None
            if not wants_json():
                flash("Your sign-in ended. Log in online to continue.", "warning")
                return redirect(url_for("login"))
        if wants_json():
            return json_error(message, status)
        return render_template(
            "cloud_message.html", title="Connection or access needs attention", message=message,
            detail=None,
        ), status

    @app.errorhandler(ModelDeliveryError)
    def model_error(error):
        if wants_json():
            return json_error(str(error), 503)
        return render_template(
            "cloud_message.html", title="Model unavailable", message=str(error), icon="bi-cpu",
            detail="Models are published by the maintainer; this installation never trains.",
        ), 503

    @app.errorhandler(LiveMonitorError)
    def monitor_error(error):
        return json_error(str(error), 409)

    @app.errorhandler(SimulationServiceError)
    def simulation_error(error):
        return json_error(str(error), 400)

    # -- helpers ---------------------------------------------------------------

    def end_identity(identity, action="logout"):
        """Stop the old capture, clear its tokens; its pending records stay its own."""
        services.monitor.stop_for_logout(identity)
        audit(services.outbox, _owner(identity), "Authentication", action, "Success",
              f"{identity.username} signed out of this installation.")
        services.sync.unregister(identity)
        services.auth.logout(identity)

    def profile_names(repo, profile_ids):
        wanted = sorted({int(value) for value in profile_ids if value is not None})[:100]
        names = {value: f"Profile #{value}" for value in wanted}
        if wanted:
            try:
                rows = repo.list_records("profile", filters={"profile_id": wanted},
                                         page_size=100).rows
            except RepositoryError as error:
                if error.category == "authentication":
                    raise
                rows = ()
            names.update({row["profile_id"]: row["username"] for row in rows})
        return names

    def active_deployment(repo):
        """Display metadata for the active release without downloading its bytes."""
        rows = repo.list_records("model_manifest", filters={"status": "active"}).rows
        if len(rows) != 1:
            raise ModelDeliveryError("No active compatible model is published. "
                                     "Contact maintenance.")
        manifest = dict(rows[0])
        verify_manifest(manifest)
        found = _parallel({
            "model": lambda: repo.list_records(
                "model_summary", filters={"model_id": manifest["model_id"]}).rows,
            "deployment": lambda: repo.list_records(
                "deployment_summary", filters={"deployment_id": manifest["deployment_id"]}).rows,
        })
        model = found["model"][0] if not isinstance(found["model"], Exception) and found[
            "model"] else {}
        deployment = found["deployment"][0] if not isinstance(
            found["deployment"], Exception) and found["deployment"] else {}
        filename = None
        if model.get("run_id"):
            try:
                runs = repo.list_records("training_summary",
                                         filters={"run_id": model["run_id"]}).rows
                filename = runs[0].get("filename") if runs else None
            except RepositoryError:
                filename = None
        return {
            **{key: model.get(key) for key in (
                "accuracy", "precision_score", "recall", "f1_score", "roc_auc", "fpr",
                "model_size", "model_type", "run_id")},
            "model_name": model.get("model_name") or "Stacking Ensemble",
            "deployment_id": manifest["deployment_id"],
            "deployed_at": _utc_text(manifest.get("activated_at")) or deployment.get("deployed_at"),
            "filename": filename,
        }

    def manual_prediction(pinned, deployment, payload):
        outcome = predict_with_artifact(pinned.artifact, payload)
        event = flow_event(deployment, outcome, outcome["flow_data"], "simulation", None)
        submitted = services.outbox.submit(g.repo.context, "manual", event, capped=False)
        persistence = submitted["persistence"]
        if persistence == "in_memory":
            persistence = services.outbox.wait_durable(event["event_uuid"], 1.0)
        services.sync.register(g.identity)
        return {
            "prediction": outcome["prediction"],
            "confidence": outcome["confidence"],
            "latency_ms": outcome["latency_ms"],
            "deployed_model_name": deployment["model_name"],
            "deployment_id": deployment["deployment_id"],
            "model_id": deployment["model_id"],
            "source_run_id": deployment.get("run_id"),
            "event_uuid": event["event_uuid"],
            "persistence": persistence,
            "persistence_label": PERSISTENCE_LABELS.get(persistence, persistence),
            "reason": submitted.get("reason"),
            "synced": False,
            "prediction_id": None,
            "alert_id": None,
            "alert_expected": outcome["prediction"] == "Attack"
            and persistence in ("in_memory", "durable_pending"),
        }

    # -- authentication --------------------------------------------------------

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "GET" and g.identity is not None and not g.bearer:
            return redirect(url_for("dashboard"))
        next_url = request.args.get("next") or request.form.get("next") or ""
        email = ""
        if request.method == "POST":
            email = request.form.get("email", "").strip()[:254]
            try:
                identity = services.auth.login(email, request.form.get("password", ""))
            except RepositoryError as error:
                if error.category == "transient":
                    flash("The cloud is unreachable. Online sign-in is required; a cached "
                          "model does not permit offline access.", "danger")
                else:
                    flash("Email or password was not accepted, or this account is not active.",
                          "danger")
                return render_template("login.html", next_url=next_url, username=email)
            previous = session.identity
            if previous is not None:
                end_identity(previous, "account_switched")
            session_interface.rotate(session)
            session.clear()
            session.identity = identity
            session["_csrf_token"] = secrets.token_urlsafe(32)
            session.update(admin_id=identity.profile_id, admin_username=identity.username,
                           admin_role=identity.display_role)
            repo = services.auth.repository(identity)
            host = (socket.gethostname() or "AlgoGuard installation").strip()
            try:
                repo.enroll(host[:100] or "AlgoGuard installation", host[:255] or None)
            except RepositoryError as error:
                if error.category == "authentication":
                    raise
                flash("Signed in, but this installation's enrollment could not be confirmed. "
                      "Reload the dashboard when the connection is stable.", "warning")
            services.sync.register(identity)
            audit(services.outbox, repo.context, "Authentication", "login_successful", "Success",
                  f"{identity.username} signed in on this installation.")
            flash("Welcome back to AlgoGuard.", "success")
            return redirect(_safe_next_url(next_url))
        return render_template("login.html", next_url=next_url, username=email)

    @app.post("/logout")
    def logout():
        end_identity(g.identity)
        session.identity = None
        session.clear()
        session_interface.forget(session)
        response = redirect(url_for("login", logged_out="1"))
        response.delete_cookie(app.config["SESSION_COOKIE_NAME"], path="/")
        return response

    # -- pages -----------------------------------------------------------------

    @app.get("/")
    def dashboard():
        repo = g.repo
        results = _parallel({
            "stats": repo.statistics,
            "alerts": lambda: repo.list_records("alert_detail", page_size=5,
                                                descending=True).rows,
            "logs": lambda: repo.list_records("system_log", page_size=5, descending=True).rows,
            "approved": repo.node_access,
            "deployment": lambda: active_deployment(repo),
        })
        _raise_first(results, ("stats", "alerts", "logs"))
        stats = {
            "total_flows": 0, "attack_count": 0, "normal_count": 0, "alerts_open": 0,
            "alerts_total": 0, "avg_latency_ms": None, "last_prediction_at": None,
            **{key: value for key, value in (results["stats"] or {}).items()
               if value is not None},
        }
        total = stats["total_flows"] or 0
        stats["attack_ratio"] = (stats["attack_count"] / total * 100) if total else 0.0
        approved = results["approved"]
        if isinstance(approved, RepositoryError) and approved.category == "authentication":
            raise approved
        deployment = results["deployment"]
        model_message = None
        if isinstance(deployment, Exception):
            if isinstance(deployment, RepositoryError) and deployment.category == "authentication":
                raise deployment
            model_message = (str(deployment) if isinstance(deployment, ModelDeliveryError)
                             else "The active model could not be checked. Reconnect and retry.")
            deployment = None
        return render_template(
            "dashboard.html",
            active_deployment=deployment,
            model_message=model_message,
            stats=stats,
            latest_prediction=None,
            latest_alerts=results["alerts"],
            latest_logs=results["logs"],
            node_id=str(services.node_id),
            node_approved=None if isinstance(approved, Exception) else bool(approved),
            sync_status=services.outbox.counts(_owner(g.identity)),
            upload=services.sync.status(g.identity),
        )

    @app.get("/monitor")
    def live_monitor():
        deployment, message = None, None
        try:
            pinned = services.models.resolve(g.repo)
            deployment = deployment_summary(g.repo, pinned)
        except ModelDeliveryError as error:
            message = str(error)
        return render_template(
            "monitor.html",
            available=deployment is not None,
            unavailable_message=message,
            deployment=deployment,
            options=get_options(),
            initial_status=services.monitor.status(g.identity),
        )

    @app.post("/monitor/start")
    def monitor_start():
        payload = g.json_payload
        defaults = get_options()["defaults"]
        public = services.monitor.start(
            g.identity,
            source_type=payload.get("source_type", defaults["source_type"]),
            dataset=payload.get("dataset", defaults["dataset"]),
            capture_file=payload.get("capture_file"),
            interface=payload.get("interface"),
            speed=payload.get("speed", defaults["speed"]),
            order=payload.get("order", defaults["order"]),
            persist=payload.get("persist", defaults["persist"]),
        )
        return jsonify({"status": "success", "session": public})

    @app.post("/monitor/pause")
    def monitor_pause():
        return jsonify({"status": "success", "session": services.monitor.pause(g.identity)})

    @app.post("/monitor/resume")
    def monitor_resume():
        return jsonify({"status": "success", "session": services.monitor.resume(g.identity)})

    @app.post("/monitor/stop")
    def monitor_stop():
        return jsonify({"status": "success", "session": services.monitor.stop(g.identity)})

    @app.get("/monitor/status")
    def monitor_status():
        return jsonify({
            "status": "success",
            **services.monitor.status(
                g.identity, request.args.get("since", 0, type=int),
                request.args.get("session_id"),
            ),
        })

    @app.route("/simulation", methods=["GET", "POST"])
    def simulation_demo():
        try:
            pinned = services.models.resolve(g.repo)
        except ModelDeliveryError as error:
            schema = {"available": False, "message": str(error), "fields": [],
                      "deployment": None}
            return render_template("simulation.html", schema=schema, form_data={}, result=None)
        deployment = deployment_summary(g.repo, pinned)
        schema = schema_for_artifact(pinned.artifact, deployment)
        form_data = {field["name"]: field.get("default", "") for field in schema["fields"]}
        result = None
        if request.method == "POST":
            form_data.update(request.form.to_dict())
            form_data.pop("_csrf_token", None)
            try:
                result = manual_prediction(pinned, deployment, form_data)
            except SimulationServiceError as error:
                flash(str(error), "danger")
        return render_template("simulation.html", schema=schema, form_data=form_data,
                               result=result)

    @app.post("/predict")
    def predict_api():
        pinned = services.models.resolve(g.repo)
        deployment = deployment_summary(g.repo, pinned)
        return jsonify({"status": "success",
                        **manual_prediction(pinned, deployment, g.json_payload)})

    @app.get("/api/events/<event_uuid>")
    def event_status(event_uuid):
        try:
            event_uuid = str(UUID(event_uuid))
        except ValueError:
            return json_error("Unknown event for this account.", 404)
        state = services.outbox.statuses(_owner(g.identity), [event_uuid]).get(event_uuid)
        if state is None:
            return json_error("Unknown event for this account.", 404)
        return jsonify({"status": "success", "event_uuid": event_uuid, **state,
                        "synced": state["persistence"] == "synced"})

    @app.get("/alerts")
    def alert_history():
        offset = _offset()
        alert_status = request.args.get("alert_status", "").strip()[:40]
        page = g.repo.list_records(
            "alert_detail", offset=offset, page_size=PAGE_SIZE, descending=True,
            filters={"alert_status": alert_status} if alert_status else None,
        )
        return render_template(
            "alerts.html", alerts=page.rows, next_offset=page.next_offset, offset=offset,
            page_size=PAGE_SIZE, alert_status=alert_status, report_request_id=str(uuid4()),
            node_id=str(services.node_id),
        )

    @app.get("/logs")
    def system_logs():
        filters = {
            key: request.args.get(key, "").strip()
            for key in ("date_from", "date_to", "module", "status", "model", "run_id", "search")
        }
        page_number = min(max(1, request.args.get("page", 1, type=int)),
                          MAX_OFFSET // LOG_PAGE_SIZE)
        equal = {}
        for key, column in (("module", "module"), ("status", "status"), ("model", "model_name")):
            if filters[key]:
                equal[column] = filters[key][:200]
        if filters["run_id"]:
            if filters["run_id"].isdigit() and 0 < int(filters["run_id"]) < 2**63:
                equal["run_id"] = int(filters["run_id"])
            else:
                flash("Run ID must be a positive whole number.", "warning")
                filters["run_id"] = ""
        bounds = []
        for key, suffix in (("date_from", "00:00:00"), ("date_to", "23:59:59")):
            value = filters[key]
            try:
                bounds.append(f"{datetime.strptime(value, '%Y-%m-%d'):%Y-%m-%d} {suffix}"
                              if value else None)
            except ValueError:
                flash("Dates must use the YYYY-MM-DD format.", "warning")
                filters[key] = ""
                bounds.append(None)
        search = filters["search"][:100] or None
        repo = g.repo
        results = _parallel({
            "page": lambda: repo.list_records(
                "system_log", page_size=LOG_PAGE_SIZE,
                offset=(page_number - 1) * LOG_PAGE_SIZE, filters=equal or None,
                descending=True, between=tuple(bounds) if any(bounds) else None, search=search,
            ),
            "options": repo.log_filter_options,
        })
        _raise_first(results, ("page",))
        options = results["options"]
        if isinstance(options, Exception):
            if isinstance(options, RepositoryError) and options.category == "authentication":
                raise options
            options = {"modules": [], "statuses": [], "models": []}
        page = results["page"]
        names = profile_names(g.repo, [row.get("profile_id") for row in page.rows])
        items = [
            {**row, "admin_username": names.get(row.get("profile_id"))} for row in page.rows
        ]
        return render_template(
            "logs.html",
            logs={"items": items, "total": None, "page": page_number, "pages": None,
                  "has_next": page.next_offset is not None},
            filters=filters,
            filter_options=options,
        )

    @app.route("/reports", methods=["GET", "POST"])
    def reports():
        if request.method == "POST":
            raw = request.form.getlist("alert_id") or request.form.get("alert_ids", "").split(",")
            try:
                ids = sorted({int(value) for value in raw if str(value).strip()})
            except ValueError:
                ids = []
            if not ids:
                flash("Select at least one alert for the report.", "warning")
                return redirect(url_for("alert_history"))
            if len(ids) > 250:
                flash("A report can include at most 250 alerts.", "warning")
                return redirect(url_for("alert_history"))
            try:
                report_id, replayed = g.repo.create_report(_form_uuid(), ids)
            except RepositoryError as error:
                if error.category not in ("validation", "conflict"):
                    raise
                flash("Some selected alerts are unavailable for a report on this installation. "
                      "Reload the Alerts page and try again.", "warning")
                return redirect(url_for("alert_history"))
            if not replayed:
                audit(services.outbox, g.repo.context, "Reports", "report_created", "Success",
                      f"Report {report_id} created with {len(ids)} alert(s) of evidence.")
            flash("This report was already saved." if replayed
                  else "Report saved together with its alert evidence.", "success")
            return redirect(url_for("report_detail", report_id=report_id))
        offset = _offset()
        page = g.repo.list_records("report", offset=offset, page_size=PAGE_SIZE, descending=True)
        return render_template(
            "reports.html", reports=page.rows, next_offset=page.next_offset, offset=offset,
            usernames=profile_names(g.repo, [row.get("owner_profile_id") for row in page.rows]),
        )

    def report_evidence(report_id):
        rows = g.repo.list_records("report", filters={"report_id": report_id},
                                   all_visible_nodes=True).rows
        if not rows:
            raise RepositoryError("not_found")
        alert_ids, offset = [], 0
        while offset is not None and len(alert_ids) < 250:
            links = g.repo.list_records("report_alert", filters={"report_id": report_id},
                                        page_size=100, offset=offset, all_visible_nodes=True)
            alert_ids += [row["alert_id"] for row in links.rows]
            offset = links.next_offset
        alerts = []
        for start in range(0, len(alert_ids), 100):
            alerts += g.repo.list_records(
                "alert_detail", filters={"alert_id": alert_ids[start:start + 100]},
                page_size=100, all_visible_nodes=True,
            ).rows
        return rows[0], alerts

    @app.get("/reports/<int:report_id>")
    def report_detail(report_id):
        report, alerts = report_evidence(report_id)
        return render_template("report_detail.html", report=report, alerts=alerts)

    @app.get("/api/reports/<int:report_id>")
    def report_json(report_id):
        report, alerts = report_evidence(report_id)
        return jsonify({"status": "success", "report": report, "alerts": alerts})

    @app.get("/outbox/export")
    def outbox_export():
        owner = _owner(g.identity)
        body = {
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "node_id": str(services.node_id),
            "user_id": str(g.identity.user_id),
            "profile_id": g.identity.profile_id,
            "note": "Records not yet committed in the cloud; synced records are excluded.",
            "events": services.outbox.export_pending(owner),
            "summaries": services.outbox.export_summaries(owner),
        }
        return app.response_class(
            json.dumps(body, indent=2), mimetype="application/json",
            headers={"Content-Disposition": 'attachment; filename="algoguard-pending.json"'},
        )

    @app.post("/sync/retry")
    def sync_retry():
        owner = str(g.identity.user_id)
        with services.sync.lock:
            services.sync.blocked.pop(owner, None)
            services.sync.retry.pop(owner, None)
            services.sync.last_authorization.pop(owner, None)
        services.sync.register(g.identity)
        flash("Uploads will be retried with your current access.", "success")
        return redirect(url_for("dashboard"))

    @app.route("/nodes", methods=["GET", "POST"])
    def nodes():
        administrator = "administrator" in services.auth.current_roles(g.identity)
        if request.method == "POST":
            if not administrator:
                raise RepositoryError("permission")
            status = request.form.get("status", "")
            if request.form.get("action") != "membership" or status not in ("approved", "revoked"):
                raise RepositoryError("validation")
            try:
                command = {
                    "action": "membership",
                    "node_id": str(UUID(request.form.get("node_id", ""))),
                    "profile_id": int(request.form.get("profile_id", "")),
                    "status": status,
                    "is_default": status == "approved"
                    and request.form.get("is_default") == "true",
                }
            except ValueError:
                raise RepositoryError("validation") from None
            g.repo.administer(_form_uuid(), command)
            audit(services.outbox, g.repo.context, "Admin Management", "membership_updated",
                  "Success", f"Membership of profile {command['profile_id']} on node "
                  f"{command['node_id']} set to {status}.")
            flash("Membership updated.", "success")
            return redirect(url_for("nodes"))
        repo = g.repo
        results = _parallel({
            "approved": repo.node_access,
            "memberships": lambda: repo.list_records(
                "node_membership", page_size=100, descending=True, all_visible_nodes=True).rows,
            "nodes": lambda: repo.list_records("node", page_size=100,
                                               all_visible_nodes=True).rows,
        })
        _raise_first(results, ("memberships", "nodes"))
        approved = results["approved"]
        known = {row["node_id"]: row for row in results["nodes"]}
        names = profile_names(repo, [row.get("profile_id") for row in results["memberships"]])
        memberships = [
            {
                **row,
                "username": names.get(row.get("profile_id")),
                "display_name": known.get(row["node_id"], {}).get("display_name"),
                "hostname_hint": known.get(row["node_id"], {}).get("hostname_hint"),
                "approve_request_id": str(uuid4()),
                "revoke_request_id": str(uuid4()),
            }
            for row in results["memberships"]
        ]
        return render_template(
            "nodes.html",
            node_id=str(services.node_id),
            node_approved=None if isinstance(approved, Exception) else bool(approved),
            administrator=administrator,
            pending=[row for row in memberships if row["status"] == "pending"],
            memberships=memberships,
        )

    @app.route("/admins", methods=["GET", "POST"])
    def manage_admins():
        if "administrator" not in services.auth.current_roles(g.identity):
            flash("Only Administrator accounts can manage accounts.", "danger")
            return redirect(url_for("dashboard"))
        form_data = {"email": "", "username": "", "role": "analyst"}
        if request.method == "POST":
            action = request.form.get("action")
            if action == "create_account":
                form_data.update(
                    email=request.form.get("email", "").strip()[:254],
                    username=request.form.get("username", "").strip()[:80],
                    role=request.form.get("role", "analyst"),
                )
                password = request.form.get("password", "")
                if form_data["role"] not in ("analyst", "administrator"):
                    flash("Choose Analyst or Administrator.", "danger")
                elif password != request.form.get("confirm_password", ""):
                    flash("Passwords do not match.", "danger")
                elif not PASSWORD_RULE.fullmatch(password):
                    flash("Passwords need 12 to 128 characters with upper- and lower-case "
                          "letters, a digit, and a symbol.", "danger")
                else:
                    try:
                        g.repo.administer(_form_uuid(), {"action": "create_account",
                                                         **form_data, "password": password})
                    except RepositoryError as error:
                        if error.category == "validation":
                            flash("Check the email address and display name.", "danger")
                        elif error.category == "conflict":
                            flash("An account with this email or display name already exists.",
                                  "danger")
                        else:
                            raise
                    else:
                        audit(services.outbox, g.repo.context, "Admin Management",
                              "account_created", "Success",
                              f"Created {form_data['role']} account {form_data['username']}.")
                        flash("Account created. Approve its installation on the Nodes page "
                              "after the user's first sign-in.", "success")
                        return redirect(url_for("manage_admins"))
            elif action in ("role", "account_status"):
                try:
                    profile_id = int(request.form.get("profile_id", ""))
                except ValueError:
                    raise RepositoryError("validation") from None
                if profile_id == g.identity.profile_id:
                    flash("You cannot change your own role or account status.", "danger")
                    return redirect(url_for("manage_admins"))
                command = {"action": action, "profile_id": profile_id}
                if action == "role":
                    role = request.form.get("role", "")
                    if role not in ("analyst", "administrator"):
                        raise RepositoryError("validation")
                    command["role"] = role
                else:
                    command["is_active"] = request.form.get("is_active") == "true"
                g.repo.administer(_form_uuid(), command)
                audit(services.outbox, g.repo.context, "Admin Management", f"{action}_changed",
                      "Success", f"Changed {action.replace('_', ' ')} of profile {profile_id}.")
                flash("Account updated.", "success")
                return redirect(url_for("manage_admins"))
            else:
                raise RepositoryError("validation")
        offset = _offset()
        page = g.repo.list_records("profile", offset=offset, page_size=PAGE_SIZE)
        ids = [row["profile_id"] for row in page.rows]
        roles = {}
        if ids:
            for row in g.repo.list_records("user_role", filters={"profile_id": ids},
                                           page_size=100).rows:
                roles.setdefault(row["profile_id"], []).append(row["role"])
        accounts = [
            {**row, "roles": sorted(roles.get(row["profile_id"], [])),
             "role_request_id": str(uuid4()), "status_request_id": str(uuid4())}
            for row in page.rows
        ]
        return render_template(
            "accounts.html", accounts=accounts, form_data=form_data, request_id=str(uuid4()),
            current_profile_id=g.identity.profile_id, offset=offset,
            next_offset=page.next_offset,
        )

    return app
