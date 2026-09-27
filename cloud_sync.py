"""Background upload under the original user's current authorization only (5D.2/5D.3).

Pending events belong to the user, profile, and node that produced them. They
are uploaded only with that same user's valid identity: never another account's
token and never a privileged fallback credential. After logout or a restart the
events stay durable and pending until that user signs in again.

Failure handling:

* transient (network, 429, 5xx) - capped exponential backoff with jitter; the
  server deduplicates by event UUID, so a lost acknowledgement is reconciled
  by simply sending the same events again;
* validation/conflict - permanent for the offending event only: a failed batch
  is split so one bad event cannot reject 49 good ones;
* permission (revoked membership, deactivated account) - uploads stop with an
  actionable status; events remain pending and exportable until access returns
  or the seven-day limit expires them;
* authentication (expired and not refreshable) - the identity is dropped; the
  events wait for the next online login by the same user.
"""

from __future__ import annotations

import random
import threading
import time
from uuid import uuid4

from cloud_repository import RepositoryError

INTERVAL_SECONDS = 1.0
MEMBERSHIP_RECHECK_SECONDS = 15.0
MAX_BACKOFF_SECONDS = 60.0
MAX_BATCHES_PER_TICK = 10
PERMANENT_EVENT_ERRORS = {"validation", "conflict"}


class SyncWorker:
    def __init__(self, auth, outbox, *, clock=time.monotonic, rng=random.random):
        self.auth = auth
        self.outbox = outbox
        self.clock = clock
        self.rng = rng
        self.identities = {}
        self.blocked = {}
        self.retry = {}
        self.last_error = {}
        self.last_success = {}
        self.last_authorization = {}
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.thread = None

    # -- registration --------------------------------------------------------

    def register(self, identity):
        """Upload this user's pending events with ``identity`` from now on.

        A browser identity (refreshable) replaces a bearer identity, never the
        reverse, and a fresh login clears a previous permission block so the
        user can retry after an administrator restores access.
        """
        owner = str(identity.user_id)
        with self.lock:
            current = self.identities.get(owner)
            if current is identity:
                return
            if (
                current is None
                or not current.active
                or (identity.session_bound and not current.session_bound)
            ):
                self.identities[owner] = identity
                if identity.session_bound:
                    self.blocked.pop(owner, None)
                    self.retry.pop(owner, None)
                    self.last_authorization.pop(owner, None)

    def unregister(self, identity):
        owner = str(identity.user_id)
        with self.lock:
            if self.identities.get(owner) is identity:
                del self.identities[owner]

    def status(self, identity):
        owner = str(identity.user_id)
        with self.lock:
            registered = self.identities.get(owner)
            if owner in self.blocked:
                state = "blocked"
            elif owner in self.retry:
                state = "retrying"
            elif registered is None or not registered.active:
                state = "waiting_for_login"
            else:
                state = "active"
            retry = self.retry.get(owner)
            return {
                "state": state,
                "reason": self.blocked.get(owner) or self.last_error.get(owner),
                "retry_in_seconds": (
                    max(0.0, round(retry[1] - self.clock(), 1)) if retry else None
                ),
                "last_success_age_seconds": (
                    round(self.clock() - self.last_success[owner], 1)
                    if owner in self.last_success else None
                ),
            }

    # -- one synchronisation pass --------------------------------------------

    def _store(self, repo, events):
        """Upload a batch; isolate permanently invalid events instead of rejecting all."""
        try:
            receipts = repo.store_flows(events)
        except RepositoryError as error:
            if error.category not in PERMANENT_EVENT_ERRORS:
                raise
            if len(events) == 1:
                self.outbox.reject(repo.context, events, error.category)
                return 0
            accepted = 0
            for event in events:
                accepted += self._store(repo, [event])
            return accepted
        self.outbox.acknowledge(repo.context, receipts)
        return len(receipts)

    def _summaries(self, repo):
        for summary_id, kind, payload in self.outbox.pending_summaries(repo.context):
            try:
                if kind == "capture":
                    repo.finalize_capture(int(payload["capture_id"]), payload)
                else:
                    repo.rpc(
                        "append_system_log",
                        {
                            "p_node_id": str(repo.context.node_id),
                            "p_profile_id": repo.context.profile_id,
                            "p_event_uuid": summary_id,
                            "p_values": payload,
                        },
                    )
            except RepositoryError as error:
                if error.category in PERMANENT_EVENT_ERRORS | {"not_found"}:
                    self.outbox.finish_lifecycle(repo.context, summary_id, error.category)
                    continue
                raise
            except (KeyError, TypeError, ValueError):
                self.outbox.finish_lifecycle(repo.context, summary_id, "invalid_summary")
                continue
            self.outbox.finish_lifecycle(repo.context, summary_id)

    def sync_once(self, identity):
        owner = str(identity.user_id)
        if owner in self.blocked:
            return
        try:
            repo = self.auth.repository(identity)
            now = self.clock()
            if now - self.last_authorization.get(owner, -1e9) >= MEMBERSHIP_RECHECK_SECONDS:
                if not repo.node_access():
                    raise RepositoryError("permission")
                self.last_authorization[owner] = now
            for _ in range(MAX_BATCHES_PER_TICK):
                events = self.outbox.pending(repo.context)
                if not events:
                    break
                self._store(repo, events)
                if len(events) < 50:
                    break
            self._summaries(repo)
            self.outbox.recheck_capacity()
            with self.lock:
                self.retry.pop(owner, None)
                self.last_error.pop(owner, None)
                self.last_success[owner] = self.clock()
        except RepositoryError as error:
            with self.lock:
                self.last_error[owner] = error.category
                if error.retryable:
                    attempt = min(self.retry.get(owner, (0, 0))[0] + 1, 10)
                    delay = min(MAX_BACKOFF_SECONDS, 2 ** (attempt - 1)) * (
                        0.5 + 0.5 * self.rng()
                    )
                    self.retry[owner] = (attempt, self.clock() + delay)
                elif error.category == "authentication":
                    identity.clear()
                    if self.identities.get(owner) is identity:
                        del self.identities[owner]
                else:
                    # permission/protocol: stop uploads; keep events pending and exportable.
                    self.blocked[owner] = error.category

    # -- background loop -----------------------------------------------------

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.outbox.start()
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, name="algoguard-upload", daemon=True)
        self.thread.start()

    def _due(self):
        with self.lock:
            now = self.clock()
            return [
                identity
                for owner, identity in self.identities.items()
                if identity.active and self.retry.get(owner, (0, 0))[1] <= now
            ]

    def _run(self):
        last_expiry = -1e9
        while not self.stop_event.is_set():
            if self.clock() - last_expiry >= 60:
                try:
                    self.outbox.expire()
                except Exception:
                    pass  # A local disk error must never stop uploads or recurse into the spool.
                last_expiry = self.clock()
            for identity in self._due():
                if self.stop_event.is_set():
                    break
                try:
                    self.sync_once(identity)
                except Exception:
                    # Local storage failures are retried with backoff and shown in the
                    # UI; they are never written to the spool that just failed.
                    owner = str(identity.user_id)
                    with self.lock:
                        attempt = min(self.retry.get(owner, (0, 0))[0] + 1, 10)
                        self.retry[owner] = (attempt, self.clock() + min(60, 2**attempt))
                        self.last_error[owner] = "local_storage"
            self.stop_event.wait(INTERVAL_SECONDS)

    def close(self, timeout=2.0):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=timeout)


def audit(outbox, context, module, action, status, message=None, model_name=None):
    """Queue one application audit entry in the bounded reserve; never raise.

    Audit failures are counted (``summaries_dropped``) instead of logged, so a
    failing spool or cloud can never recursively fill itself or stop capture.
    """
    from datetime import datetime, timezone

    payload = {
        "module": str(module)[:80],
        "action": str(action)[:80],
        "status": str(status)[:40],
        "message": None if message is None else str(message)[:2000],
        "model_name": None if model_name is None else str(model_name)[:200],
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
    }
    try:
        outbox.save_lifecycle(context, str(uuid4()), payload, kind="log")
        return True
    except (OSError, ValueError):
        return False
