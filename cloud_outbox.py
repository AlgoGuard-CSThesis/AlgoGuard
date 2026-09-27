"""Bounded local handoff and separate durable spool; never a business database.

Persistence states reported to callers and the UI (Stage 5D.2):

* ``session_only`` - classified but not selected for storage (monitor setting).
* ``in_memory``    - accepted into the bounded handoff; lost if the process dies.
* ``durable_pending`` - written to this owner-restricted SQLite spool.
* ``synced``       - acknowledged by the cloud as committed.
* ``dropped``      - not stored: session cap, full handoff, or local capacity/disk.
* ``rejected``     - the cloud permanently refused it; kept for export.

Only ``synced`` means cloud-committed. Nothing in memory is ever reported saved.
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import sqlite3
import subprocess
import threading
import time
from collections import OrderedDict
from pathlib import Path
from uuid import UUID

HANDOFF_LIMIT = 1000
TOTAL_BYTES = 100 * 1024 * 1024
SESSION_LIMIT = 300
MAX_AGE = 7 * 86400
BATCH_LIMIT = 50
EVENT_BYTES = 65536
SUMMARY_BYTES = 4096
# Separate reserved capacity: capture summaries can never be crowded out by audit
# entries, and neither can grow without bound during a prolonged outage.
SUMMARY_LIMITS = {"capture": 32, "log": 96}
RECOVERY_FRACTION = 0.8
_TRACKED_OUTCOMES = 1000
_TRACKED_SESSIONS = 256
SCHEMA_VERSION = 2


def restrict_owner(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        import ctypes

        size = ctypes.c_ulong(1024)
        name = ctypes.create_unicode_buffer(size.value)
        if not ctypes.windll.secur32.GetUserNameExW(2, name, ctypes.byref(size)):
            raise OSError("Cannot identify the spool owner.")
        result = subprocess.run(
            [
                "icacls",
                str(directory),
                "/inheritance:r",
                "/grant:r",
                name.value + ":(OI)(CI)F",
                "*S-1-5-18:(OI)(CI)F",
            ],
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode:
            raise OSError("Cannot restrict the spool to its owner.")
    else:
        directory.chmod(0o700)


class _Bounded(OrderedDict):
    def __init__(self, limit):
        super().__init__()
        self.limit = limit

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        self.move_to_end(key)
        while len(self) > self.limit:
            self.popitem(last=False)


def _owner(context):
    return str(context.user_id), context.profile_id, str(context.node_id)


class Outbox:
    def __init__(self, directory, *, handoff_limit=HANDOFF_LIMIT, total_bytes=TOTAL_BYTES):
        self.directory = Path(directory)
        restrict_owner(self.directory)
        self.path = self.directory / "outbox.sqlite3"
        self.total_bytes = total_bytes
        self.flow_budget = (total_bytes - 1024 * 1024) // 2
        self.queue = queue.Queue(maxsize=handoff_limit)
        self.lock = threading.RLock()
        self.db_lock = threading.RLock()
        self.durable = threading.Condition(self.lock)
        self.reservations = {}
        self.reservation_times = {}
        self.memory_status = {}
        self.lost = _Bounded(_TRACKED_OUTCOMES)
        self.session_dropped = _Bounded(_TRACKED_SESSIONS)
        self.dropped = 0
        self.summaries_dropped = 0
        self.disk_failed = False
        self.loss_reason = None  # None, "quota" (recoverable), or "disk" (until restart)
        self.stop_event = threading.Event()
        self.thread = None
        self.db = sqlite3.connect(self.path, check_same_thread=False, timeout=1)
        self.db.row_factory = sqlite3.Row
        self.db.execute("pragma journal_mode=delete")
        self.db.execute("pragma synchronous=full")
        # A rollback journal can approach database size. Reserve half the budget
        # for it, plus 1 MiB for headers/metadata; no WAL can grow unchecked.
        pages = max(16, self.flow_budget // 4096)
        self.db.execute(f"pragma max_page_count={pages}")
        self._migrate()
        for row in self.db.execute("select * from reservation"):
            self.reservations[row["session_id"]] = row["accepted"]
            self.reservation_times[row["session_id"]] = row["created_at"]

    def _migrate(self):
        with self.db_lock, self.db:
            self.db.executescript("""
                create table if not exists event (
                    event_uuid text primary key, user_id text not null,
                    profile_id integer not null, node_id text not null,
                    session_id text not null, deployment_id integer not null,
                    created_at real not null, payload text not null,
                    status text not null check(status in ('pending','synced','rejected')),
                    reason text, receipt text, payload_hash text not null
                );
                create index if not exists pending_owner on event(user_id,status,created_at);
                create table if not exists reservation (
                    session_id text primary key, accepted integer not null,
                    created_at real not null
                );
                create table if not exists lifecycle (
                    summary_id text primary key, user_id text not null,
                    profile_id integer not null, node_id text not null,
                    created_at real not null, payload text not null,
                    status text not null default 'pending', reason text
                );
            """)
            version = self.db.execute("pragma user_version").fetchone()[0]
            if version < 2:
                columns = {row[1] for row in self.db.execute("pragma table_info(lifecycle)")}
                if "kind" not in columns:
                    self.db.execute(
                        "alter table lifecycle add column kind text not null default 'capture'"
                    )
                self.db.execute(f"pragma user_version={SCHEMA_VERSION}")

    # -- handoff -----------------------------------------------------------

    def start(self):
        with self.lock:
            if self.thread and self.thread.is_alive():
                return
            self.stop_event.clear()
            self.thread = threading.Thread(target=self._writer, name="algoguard-spool", daemon=True)
            self.thread.start()

    def accepting(self):
        with self.lock:
            return not self.disk_failed

    def submit(self, context, session_id, event, *, capped=True):
        """Hand one immutable event to the writer without blocking on disk or network."""
        try:
            event_id = str(UUID(event["event_uuid"]))
            payload = json.dumps(event, sort_keys=True, separators=(",", ":"), allow_nan=False)
            if len(payload.encode()) > EVENT_BYTES:
                raise ValueError
            deployment_id = int(event["deployment_id"])
            session_id = str(session_id)
        except (ValueError, TypeError, KeyError, AttributeError):
            return {"event_uuid": event.get("event_uuid"), "persistence": "rejected",
                    "reason": "invalid_event"}
        user_id, profile_id, node_id = _owner(context)
        with self.lock:
            if event_id in self.memory_status:
                # Event UUIDs are generated once by the application. A second
                # handoff never consumes another cap slot or duplicates a write.
                existing = self.memory_status[event_id]
                if existing[1:4] == (user_id, node_id, payload):
                    return {"event_uuid": event_id, "persistence": existing[0]}
                return {"event_uuid": event_id, "persistence": "rejected",
                        "reason": "identity_conflict"}
            if self.disk_failed:
                return self._drop_locked(event_id, session_id, "storage")
            if capped and self.reservations.get(session_id, 0) >= SESSION_LIMIT:
                return self._drop_locked(event_id, session_id, "session_cap")
            item = (event_id, user_id, profile_id, node_id, session_id, deployment_id,
                    time.time(), payload, capped)
            try:
                self.queue.put_nowait(item)
            except queue.Full:
                return self._drop_locked(event_id, session_id, "handoff_full")
            if capped:
                self.reservations[session_id] = self.reservations.get(session_id, 0) + 1
                self.reservation_times.setdefault(session_id, time.time())
            self.memory_status[event_id] = ("in_memory", user_id, node_id, payload, session_id)
        return {"event_uuid": event_id, "persistence": "in_memory"}

    def _drop_locked(self, event_id, session_id, reason):
        self.dropped += 1
        self.session_dropped[session_id] = self.session_dropped.get(session_id, 0) + 1
        self.lost[event_id] = reason
        return {"event_uuid": event_id, "persistence": "dropped", "reason": reason}

    def _writer(self):
        while not self.stop_event.is_set() or not self.queue.empty():
            try:
                item = self.queue.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                self._persist(item)
            finally:
                self.queue.task_done()

    def _directory_bytes(self):
        return sum(path.stat().st_size for path in self.directory.iterdir() if path.is_file())

    def _used_bytes(self):
        return (
            self.db.execute("pragma page_count").fetchone()[0]
            - self.db.execute("pragma freelist_count").fetchone()[0]
        ) * 4096

    def _persist(self, item):
        event_id, user_id, profile_id, node_id, session_id, deployment_id, created_at, payload, \
            capped = item
        reason = "disk"
        try:
            if self._directory_bytes() >= self.total_bytes:
                reason = "quota"
                raise OSError("Spool capacity reached.")
            with self.db_lock, self.db:
                existing = self.db.execute(
                    "select * from event where event_uuid=?", (event_id,)
                ).fetchone()
                identity = (event_id, user_id, profile_id, node_id, session_id, deployment_id)
                payload_hash = hashlib.sha256(payload.encode()).hexdigest()
                if existing and (
                    tuple(existing[key] for key in (
                        "event_uuid", "user_id", "profile_id", "node_id", "session_id",
                        "deployment_id",
                    )) != identity
                    or existing["payload_hash"] != payload_hash
                ):
                    reason = "identity_conflict"
                    raise ValueError("Event identity conflict.")
                if not existing:
                    if self._used_bytes() + len(payload.encode()) * 2 + 1024 * 1024 > (
                        self.flow_budget
                    ):
                        reason = "quota"
                        raise OSError("Flow quota full; lifecycle reserve retained.")
                    self.db.execute(
                        "insert into event values(?,?,?,?,?,?,?,?,'pending',null,null,?)",
                        identity + (created_at, payload, payload_hash),
                    )
                    if capped:
                        self.db.execute(
                            "insert into reservation values(?,1,?) on conflict(session_id) "
                            "do update set accepted=accepted+1",
                            (session_id, created_at),
                        )
            with self.lock:
                if existing and capped:
                    self.reservations[session_id] -= 1  # Already counted when first stored.
                self.memory_status.pop(event_id, None)
                self.durable.notify_all()
        except (sqlite3.Error, OSError, ValueError) as error:
            if isinstance(error, sqlite3.OperationalError) and "full" in str(error).lower():
                reason = "quota"
            with self.lock:
                if reason != "identity_conflict":
                    self.disk_failed = True
                    self.loss_reason = "disk" if self.loss_reason == "disk" else reason
                if capped:
                    self.reservations[session_id] = max(
                        0, self.reservations.get(session_id, 1) - 1
                    )
                self.memory_status.pop(event_id, None)
                self._drop_locked(
                    event_id, session_id,
                    "identity_conflict" if reason == "identity_conflict" else "storage",
                )
                self.durable.notify_all()

    def recheck_capacity(self):
        """Resume accepting flows after a quota stop once space is back below 80%."""
        with self.lock:
            if not self.disk_failed or self.loss_reason != "quota":
                return not self.disk_failed
        with self.db_lock:
            used = self._used_bytes()
        if used < self.flow_budget * RECOVERY_FRACTION and (
            self._directory_bytes() < self.total_bytes * RECOVERY_FRACTION
        ):
            with self.lock:
                if self.loss_reason == "quota":
                    self.disk_failed = False
                    self.loss_reason = None
        return self.accepting()

    def wait_durable(self, event_id, timeout=1.0):
        """Wait briefly for the writer; report the honest state either way."""
        deadline = time.monotonic() + timeout
        with self.durable:
            while event_id in self.memory_status:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return "in_memory"
                self.durable.wait(remaining)
            if event_id in self.lost:
                return "dropped"
        return "durable_pending"

    # -- upload side ---------------------------------------------------------

    def pending(self, context, limit=BATCH_LIMIT):
        with self.db_lock:
            rows = self.db.execute(
                "select event_uuid,payload from event "
                "where user_id=? and profile_id=? and node_id=? "
                "and status='pending' order by created_at,event_uuid limit ?",
                _owner(context) + (min(int(limit), BATCH_LIMIT),),
            ).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def acknowledge(self, context, receipts):
        with self.db_lock, self.db:
            for receipt in receipts:
                self.db.execute(
                    "update event set status='synced',payload='{}',receipt=? "
                    "where event_uuid=? and user_id=? "
                    "and profile_id=? and node_id=? and status='pending'",
                    (
                        json.dumps(
                            {"prediction_id": receipt.prediction_id, "alert_id": receipt.alert_id}
                        ),
                        str(receipt.event_uuid),
                        *_owner(context),
                    ),
                )

    def reject(self, context, events, reason):
        with self.db_lock, self.db:
            self.db.executemany(
                "update event set status='rejected',reason=? where event_uuid=? and user_id=? "
                "and profile_id=? and node_id=? and status='pending'",
                [(reason, event["event_uuid"], *_owner(context)) for event in events],
            )

    # -- reporting -----------------------------------------------------------

    def counts(self, context):
        user_id, _, node_id = _owner(context)
        with self.db_lock:
            rows = self.db.execute(
                "select status,count(*) from event where user_id=? and node_id=? group by status",
                (user_id, node_id),
            ).fetchall()
            summaries = self.db.execute(
                "select count(*) from lifecycle where user_id=? and node_id=? and status='pending'",
                (user_id, node_id),
            ).fetchone()[0]
        with self.lock:
            memory = sum(
                1 for item in self.memory_status.values() if item[1:3] == (user_id, node_id)
            )
            result: dict[str, int | bool | str | None] = {
                "pending": 0, "synced": 0, "rejected": 0,
            }
            result.update({row[0]: row[1] for row in rows})
            result.update(
                in_memory=memory,
                dropped=self.dropped,
                disk_failed=self.disk_failed,
                loss_reason=self.loss_reason,
                summaries_pending=summaries,
                summaries_dropped=self.summaries_dropped,
            )
        return result

    def session_counts(self, context, session_id):
        user_id, _, node_id = _owner(context)
        session_id = str(session_id)
        with self.db_lock:
            rows = self.db.execute(
                "select status,count(*) from event where user_id=? and node_id=? "
                "and session_id=? group by status",
                (user_id, node_id, session_id),
            ).fetchall()
        with self.lock:
            memory = sum(
                1
                for item in self.memory_status.values()
                if item[1:3] == (user_id, node_id) and item[4] == session_id
            )
            result = {"in_memory": 0, "pending": 0, "synced": 0, "rejected": 0}
            result.update({row[0]: row[1] for row in rows})
            result["in_memory"] = memory
            result["dropped"] = self.session_dropped.get(session_id, 0)
            result["reserved"] = self.reservations.get(session_id, 0)
            result["storage_stopped"] = self.disk_failed
        return result

    def statuses(self, context, event_ids):
        user_id, _, node_id = _owner(context)
        event_ids = [str(value) for value in list(event_ids)[:100]]
        if not event_ids:
            return {}
        result = {}
        with self.lock:
            for event_id in event_ids:
                item = self.memory_status.get(event_id)
                if item is not None and item[1:3] == (user_id, node_id):
                    result[event_id] = {"persistence": "in_memory"}
                elif event_id in self.lost:
                    result[event_id] = {"persistence": "dropped", "reason": self.lost[event_id]}
        with self.db_lock:
            rows = self.db.execute(
                "select event_uuid,status,reason,receipt from event where user_id=? and node_id=? "
                "and event_uuid in (" + ",".join("?" for _ in event_ids) + ")",
                (user_id, node_id, *event_ids),
            ).fetchall()
        for row in rows:
            state = {"persistence": "durable_pending" if row["status"] == "pending"
                     else row["status"]}
            if row["reason"]:
                state["reason"] = row["reason"]
            for key, value in json.loads(row["receipt"] or "{}").items():
                state[key] = str(value) if value is not None else None
            result[row["event_uuid"]] = state
        return result

    def expire(self, now=None):
        cutoff = (time.time() if now is None else now) - MAX_AGE
        with self.db_lock, self.db:
            lost = self.db.execute(
                "select count(*) from event where created_at<? and status<>'synced'", (cutoff,)
            ).fetchone()[0]
            self.db.execute("delete from event where created_at<?", (cutoff,))
            self.db.execute(
                "delete from reservation where created_at<? and session_id not in "
                "(select session_id from event)",
                (cutoff,),
            )
            lost_summaries = self.db.execute(
                "select count(*) from lifecycle where created_at<? and status='pending'",
                (cutoff,),
            ).fetchone()[0]
            self.db.execute("delete from lifecycle where created_at<?", (cutoff,))
        with self.lock:
            self.dropped += lost
            self.summaries_dropped += lost_summaries
            for key, created in list(self.reservation_times.items()):
                if created < cutoff:
                    self.reservation_times.pop(key, None)
                    self.reservations.pop(key, None)
        return lost

    # -- reserved summaries: capture finalisation and audit entries ----------

    def save_lifecycle(self, context, summary_id, payload, kind="capture"):
        """Store a bounded summary outside the flow quota, or raise OSError/ValueError."""
        if kind not in SUMMARY_LIMITS:
            raise ValueError("Unknown summary kind.")
        encoded = json.dumps(payload, allow_nan=False)
        if len(encoded.encode()) > SUMMARY_BYTES:
            raise ValueError("Summary exceeds reserved capacity.")
        user_id, profile_id, node_id = _owner(context)
        try:
            with self.db_lock, self.db:
                exists = self.db.execute(
                    "select 1 from lifecycle where summary_id=?", (str(summary_id),)
                ).fetchone()
                count = self.db.execute(
                    "select count(*) from lifecycle where kind=? and status='pending'", (kind,)
                ).fetchone()[0]
                if not exists and count >= SUMMARY_LIMITS[kind]:
                    raise OSError("Reserved summary capacity is full.")
                self.db.execute(
                    "insert into lifecycle(summary_id,user_id,profile_id,node_id,created_at,"
                    "payload,status,reason,kind) values(?,?,?,?,?,?,'pending',null,?) "
                    "on conflict(summary_id) do update set payload=excluded.payload,"
                    "status='pending' where lifecycle.user_id=excluded.user_id "
                    "and lifecycle.node_id=excluded.node_id",
                    (str(summary_id), user_id, profile_id, node_id, time.time(), encoded, kind),
                )
        except (OSError, sqlite3.Error):
            with self.lock:
                self.summaries_dropped += 1
            raise OSError("Reserved summary capacity is unavailable.") from None

    def pending_summaries(self, context, limit=10):
        with self.db_lock:
            rows = self.db.execute(
                "select summary_id,kind,payload from lifecycle where user_id=? and profile_id=? "
                "and node_id=? and status='pending' order by created_at,summary_id limit ?",
                _owner(context) + (int(limit),),
            ).fetchall()
        return [(row[0], row[1], json.loads(row[2])) for row in rows]

    def pending_lifecycle(self, context):
        return [
            (summary_id, payload)
            for summary_id, kind, payload in self.pending_summaries(context)
            if kind == "capture"
        ]

    def finish_lifecycle(self, context, summary_id, reason=None):
        user_id = str(context.user_id)
        with self.db_lock, self.db:
            if reason:
                self.db.execute(
                    "update lifecycle set status='rejected',reason=? "
                    "where summary_id=? and user_id=?",
                    (reason, str(summary_id), user_id),
                )
            else:
                self.db.execute(
                    "delete from lifecycle where summary_id=? and user_id=?",
                    (str(summary_id), user_id),
                )

    def export_pending(self, context):
        user_id, _, node_id = _owner(context)
        with self.db_lock:
            rows = self.db.execute(
                "select event_uuid,session_id,deployment_id,created_at,status,reason,payload "
                "from event where user_id=? and node_id=? and status<>'synced' order by created_at",
                (user_id, node_id),
            ).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]

    def export_summaries(self, context):
        user_id, _, node_id = _owner(context)
        with self.db_lock:
            rows = self.db.execute(
                "select summary_id,kind,created_at,status,reason,payload from lifecycle "
                "where user_id=? and node_id=? order by created_at",
                (user_id, node_id),
            ).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]

    def drain(self, deadline=5):
        end = time.monotonic() + min(deadline, 5)
        while self.queue.unfinished_tasks and time.monotonic() < end:
            time.sleep(0.01)
        return self.queue.unfinished_tasks == 0

    def close(self):
        self.stop_event.set()
        self.drain()
        if self.thread:
            self.thread.join(timeout=0.3)
        if not self.thread or not self.thread.is_alive():
            with self.db_lock:
                self.db.close()
