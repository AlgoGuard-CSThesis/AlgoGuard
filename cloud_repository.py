"""Authenticated HTTPS repository contract. Stage 5D will integrate its consumers.

No database driver, privileged key, token persistence, or SQLite fallback.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

TIMEOUT_SECONDS = 10
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_BIGINT = 2**63 - 1


class RepositoryError(RuntimeError):
    def __init__(self, category: str):
        self.category = category
        self.retryable = category == "transient"
        super().__init__(f"Cloud operation failed: {category}.")


@dataclass(frozen=True)
class UserNodeContext:
    user_id: UUID
    profile_id: int
    node_id: UUID
    access_token: str = field(repr=False)

    def __post_init__(self):
        if (
            not isinstance(self.user_id, UUID)
            or not isinstance(self.node_id, UUID)
            or type(self.profile_id) is not int
            or not 0 < self.profile_id <= MAX_BIGINT
            or not self.access_token
            or any(c.isspace() for c in self.access_token)
        ):
            raise RepositoryError("validation")


@dataclass(frozen=True)
class Page:
    rows: tuple[dict[str, Any], ...]
    next_offset: int | None


@dataclass(frozen=True)
class FlowReceipt:
    event_uuid: UUID
    traffic_id: int
    prediction_id: int
    alert_id: int | None
    audit_log_id: int
    replayed: bool
    persistence: str = "committed"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RepositoryError("protocol")


# Resource -> stable order key, whether it is node-scoped.
RESOURCES = {
    "profile": ("profile_id", False),
    "user_role": ("profile_id,role", False),
    "node": ("node_id", True),
    "node_membership": ("membership_id", True),
    "capture_session": ("capture_id", True),
    "network_traffic": ("traffic_id", True),
    "prediction": ("prediction_id", True),
    "alert": ("alert_id", True),
    "report": ("report_id", True),
    "report_alert": ("report_id,alert_id", True),
    "system_log": ("log_id", True),
    "ingest_event": ("event_uuid", True),
    "model_manifest": ("manifest_id", False),
    "training_summary": ("run_id", False),
    "model_summary": ("model_id", False),
    "deployment_summary": ("deployment_id", False),
}
FILTERS = {
    "profile_id",
    "run_id",
    "model_id",
    "deployment_id",
    "capture_id",
    "traffic_id",
    "prediction_id",
    "alert_id",
    "report_id",
    "event_uuid",
    "status",
    "module",
    "model_name",
    "alert_status",
    "predicted_label",
    "is_active",
    "dataset_source",
}
INSERTS = {
    "capture_session": {"interface", "bpf_filter", "started_at"},
    "report": {"report_type", "date_range_start", "date_range_end", "run_id", "generated_at"},
    "system_log": {"module", "action", "status", "message", "timestamp", "run_id", "model_name"},
}
UPDATES = {
    "capture_session": {
        "finished_at",
        "packets_captured",
        "packets_dropped",
        "flows_emitted",
        "status",
        "closed_at",
    },
    "alert": {"alert_status", "description"},
}


class CloudRepository:
    def __init__(self, project_url: str, publishable_key: str, context: UserNodeContext):
        parts = urllib.parse.urlsplit(project_url)
        if (
            parts.scheme not in {"http", "https"}
            or not parts.hostname
            or parts.username
            or parts.password
            or parts.query
            or parts.fragment
            or parts.path not in {"", "/"}
            or (parts.scheme == "http" and parts.hostname not in {"127.0.0.1", "localhost", "::1"})
            or not publishable_key.startswith("sb_publishable_")
            or any(c.isspace() for c in publishable_key)
        ):
            raise RepositoryError("validation")
        self.base_url = project_url.rstrip("/")
        self.publishable_key = publishable_key
        self.context = context
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

    def _request(self, method: str, path: str, *, params=None, body=None):
        try:
            encoded = None if body is None else json.dumps(body, allow_nan=False).encode()
        except (ValueError, TypeError):
            raise RepositoryError("validation") from None
        url = self.base_url + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(
            url,
            data=encoded,
            method=method,
            headers={
                "apikey": self.publishable_key,
                "Authorization": "Bearer " + self.context.access_token,
                "Content-Type": "application/json",
                "Prefer": "return=representation",
            },
        )
        try:
            with self._opener.open(request, timeout=TIMEOUT_SECONDS) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise RepositoryError("protocol")
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            # Never echo response details, URL, credentials, or hidden row contents.
            code = exc.code
            exc.close()
            category = {
                400: "validation",
                401: "authentication",
                403: "permission",
                404: "not_found",
                409: "conflict",
                422: "validation",
            }.get(code)
            raise RepositoryError(
                category or ("transient" if code == 429 or code >= 500 else "protocol")
            ) from None
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError):
            raise RepositoryError("transient") from None
        except (ValueError, UnicodeError):
            raise RepositoryError("protocol") from None

    def list_records(
        self,
        resource: str,
        *,
        page_size=50,
        offset=0,
        filters: dict | None = None,
        all_visible_nodes=False,
    ) -> Page:
        """Offset pagination ordered by immutable keys; concurrent inserts may shift pages.

        all_visible_nodes removes only the convenience node filter. RLS still
        limits analysts to assigned nodes and permits Administrator observation.
        """
        if (
            resource not in RESOURCES
            or type(page_size) is not int
            or not 1 <= page_size <= 100
            or type(offset) is not int
            or not 0 <= offset <= 1_000_000
        ):
            raise RepositoryError("validation")
        key, scoped = RESOURCES[resource]
        params = {"select": "*", "order": key, "limit": page_size + 1, "offset": offset}
        if scoped and not all_visible_nodes:
            params["node_id"] = "eq." + str(self.context.node_id)
        for name, value in (filters or {}).items():
            if name not in FILTERS or not isinstance(value, (str, int, bool)):
                raise RepositoryError("validation")
            params[name] = (
                "eq." + str(value).lower() if isinstance(value, bool) else "eq." + str(value)
            )
        rows = self._request("GET", "/rest/v1/" + resource, params=params)
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise RepositoryError("protocol")
        return Page(tuple(rows[:page_size]), offset + page_size if len(rows) > page_size else None)

    def store_flows(self, events: list[dict]) -> tuple[FlowReceipt, ...]:
        if not isinstance(events, list) or not 1 <= len(events) <= 50:
            raise RepositoryError("validation")
        expected = []
        for event in events:
            try:
                expected.append(UUID(event["event_uuid"]))
                if len(json.dumps(event, allow_nan=False).encode()) > 65536:
                    raise ValueError
            except (ValueError, TypeError, KeyError):
                raise RepositoryError("validation") from None
        data = self._request(
            "POST",
            "/rest/v1/rpc/store_flow_batch",
            body={
                "p_node_id": str(self.context.node_id),
                "p_profile_id": self.context.profile_id,
                "p_events": events,
            },
        )
        try:
            if not isinstance(data, list) or len(data) != len(expected):
                raise ValueError
            receipts = []
            for event_id, row in zip(expected, data):
                if UUID(row["event_uuid"]) != event_id or row["persistence"] != "committed":
                    raise ValueError
                if type(row["replayed"]) is not bool:
                    raise ValueError
                ids = [int(row[key]) for key in ("traffic_id", "prediction_id", "audit_log_id")]
                alert = int(row["alert_id"]) if row["alert_id"] is not None else None
                if any(
                    not 0 < value <= MAX_BIGINT
                    for value in ids + ([] if alert is None else [alert])
                ):
                    raise ValueError
                receipts.append(
                    FlowReceipt(event_id, ids[0], ids[1], alert, ids[2], row["replayed"])
                )
            return tuple(receipts)
        except (KeyError, ValueError, TypeError, AttributeError):
            raise RepositoryError("protocol") from None

    def insert_record(self, resource: str, values: dict):
        if resource not in INSERTS or not values or set(values) - INSERTS[resource]:
            raise RepositoryError("validation")
        owner = "profile_id" if resource == "system_log" else "owner_profile_id"
        return self._request(
            "POST",
            "/rest/v1/" + resource,
            body={**values, "node_id": str(self.context.node_id), owner: self.context.profile_id},
        )

    def update_record(self, resource: str, record_id: int, values: dict):
        if (
            resource not in UPDATES
            or type(record_id) is not int
            or not 0 < record_id <= MAX_BIGINT
            or not values
            or set(values) - UPDATES[resource]
        ):
            raise RepositoryError("validation")
        result = self._request(
            "PATCH",
            "/rest/v1/" + resource,
            params={
                RESOURCES[resource][0]: "eq." + str(record_id),
                "node_id": "eq." + str(self.context.node_id),
            },
            body=values,
        )
        if result == []:
            raise RepositoryError("not_found")  # Hidden and absent records stay indistinguishable.
        return result

    def enroll(self, display_name: str, hostname_hint: str | None = None):
        return self._request(
            "POST",
            "/rest/v1/rpc/request_enrollment",
            body={
                "p_node_id": str(self.context.node_id),
                "p_display_name": display_name,
                "p_hostname_hint": hostname_hint,
            },
        )

    def administer(self, request_id: UUID, command: dict):
        if not isinstance(request_id, UUID) or "request_id" in command:
            raise RepositoryError("validation")
        return self._request(
            "POST", "/functions/v1/account_admin", body={"request_id": str(request_id), **command}
        )

    def statistics(self):
        return self._request(
            "POST",
            "/rest/v1/rpc/detection_statistics",
            body={"p_node_id": str(self.context.node_id)},
        )

    def traffic_sources(self):
        return self._request(
            "POST",
            "/rest/v1/rpc/traffic_source_counts",
            body={"p_node_id": str(self.context.node_id)},
        )
