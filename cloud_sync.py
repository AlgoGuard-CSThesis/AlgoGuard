"""Background upload under the original user's current authorization only (5D.2/5D.3).

Pending events belong to the user, profile, and node that produced them. They
are uploaded only with that same user's valid identity: never another account's
token and never a privileged fallback credential. After logout or a restart the
events stay durable and pending until that user signs in again.

Failure handling:

* transient (network, 429, 5xx) - capped exponential backoff with jitter; the
  server deduplicates by event UUID, so a lost acknowledgement is reconciled
  by simply sending the same events again. Up to ``UPLOAD_PARALLELISM`` batches
  are in flight at once (DEF-10); they never share an event, and every batch
  of a pass has finished before the next pass reads the spool again;
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

from cloud_outbox import BATCH_LIMIT
from cloud_repository import RepositoryError

INTERVAL_SECONDS = 1.0
MEMBERSHIP_RECHECK_SECONDS = 15.0
# While uploads are blocked because this installation is not (or no longer) approved, approval
# is checked again this often, so uploads start by themselves once an administrator approves it.
PERMISSION_RECHECK_SECONDS = 30.0
MAX_BACKOFF_SECONDS = 60.0
MAX_BATCHES_PER_TICK = 10
# DEF-10: one request at a time managed about 65 events/s to the Tokyo pilot, so a burst of 300
# waited up to 5 s. Four requests at once keep each batch at 50 events (the server's limit).
UPLOAD_PARALLELISM = 4
PERMANENT_EVENT_ERRORS = {"validation", "conflict"}
# Node health (6B.2) is reported this often while the user is signed in; administrators see a
# node as stale after three missed reports.
HEARTBEAT_SECONDS = 60.0


class SyncWorker:
    def __init__(self, auth, outbox, *, clock=time.monotonic, rng=random.random, health=None,
                 parallelism=UPLOAD_PARALLELISM):
        self.auth = auth
        self.outbox = outbox
        self.parallelism = max(1, int(parallelism))
        # Builds this installation's health report from a user/node context (set by the app).
        self.health = health
        self.last_heartbeat = {}
        self.heartbeat_error = {}
        self.clock = clock
        self.rng = rng
        self.identities = {}
        self.blocked = {}
        self.permission_checked = {}  # owner -> when a permission block last rechecked approval
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
        """Stop uploading and reporting health for this user (logout)."""
        owner = str(identity.user_id)
        with self.lock:
            if self.identities.get(owner) is identity:
                del self.identities[owner]
                self.last_heartbeat.pop(owner, None)

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

    def _store_parallel(self, repo, batches):
        """Upload disjoint batches at once and wait for all of them (DEF-10).

        Each batch is acknowledged or rejected on its own, so a failed batch never undoes another
        one's receipts. The most serious failure is raised afterwards: a refusal (authentication,
        permission) before a local storage error, and either before a transient one.
        """
        if len(batches) == 1:
            self._store(repo, batches[0])
            return
        failures: list[BaseException | None] = [None] * len(batches)

        def upload(index, batch):
            try:
                self._store(repo, batch)
            except BaseException as error:  # Re-raised below, in the worker thread.
                failures[index] = error

        # Daemon threads, not a pool: a request stuck until its 10 s timeout must never hold up
        # closing the application.
        threads = [threading.Thread(target=upload, args=(index, batch), daemon=True,
                                    name="algoguard-upload-batch")
                   for index, batch in enumerate(batches)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        errors = [error for error in failures if error is not None]
        if errors:
            raise min(errors, key=lambda error: (
                0 if isinstance(error, RepositoryError) and not error.retryable
                else 2 if isinstance(error, RepositoryError) else 1))

    def _upload(self, repo):
        """Send pending events, ``parallelism`` batches at a time, at most 10 batches a pass."""
        sent = 0
        while sent < MAX_BATCHES_PER_TICK:
            width = min(self.parallelism, MAX_BATCHES_PER_TICK - sent)
            events = self.outbox.pending(repo.context, limit=width * BATCH_LIMIT)
            if not events:
                return
            self._store_parallel(repo, [events[start:start + BATCH_LIMIT]
                                        for start in range(0, len(events), BATCH_LIMIT)])
            sent += width
            if len(events) < width * BATCH_LIMIT:
                return

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

    def _approval_restored(self, identity, owner):
        """Lift a permission block once the installation is approved (Stage 6D.3).

        A new installation is refused from its first sign-in until an administrator approves it;
        without this, its uploads stayed blocked after approval until the analyst pressed Retry
        uploads or signed in again. Approval is checked at most every PERMISSION_RECHECK_SECONDS.
        Other blocks still wait for Retry uploads.
        """
        with self.lock:
            if self.blocked.get(owner) != "permission":
                return False
            now = self.clock()
            if now - self.permission_checked.get(owner, -1e9) < PERMISSION_RECHECK_SECONDS:
                return False
            self.permission_checked[owner] = now
        try:
            approved = self.auth.repository(identity).node_access()
        except RepositoryError:
            return False  # still refused, or the cloud is unreachable: check again later
        if not approved:
            return False
        with self.lock:
            self.blocked.pop(owner, None)
            self.permission_checked.pop(owner, None)
            self.last_authorization[owner] = self.clock()
        return True

    def sync_once(self, identity):
        owner = str(identity.user_id)
        if owner in self.blocked and not self._approval_restored(identity, owner):
            return
        repo = None
        try:
            repo = self.auth.repository(identity)
            now = self.clock()
            if now - self.last_authorization.get(owner, -1e9) >= MEMBERSHIP_RECHECK_SECONDS:
                if not repo.node_access():
                    raise RepositoryError("permission")
                self.last_authorization[owner] = now
            self._upload(repo)
            self._summaries(repo)
            self.outbox.recheck_capacity()
            self._heartbeat(repo, owner)
            with self.lock:
                self.retry.pop(owner, None)
                self.last_error.pop(owner, None)
                self.last_success[owner] = self.clock()
        except RepositoryError as error:
            category = error.category
            if category == "authentication":
                # Outside the worker lock: renewal is a network call.
                category = self._renew(identity, repo)
            with self.lock:
                self.last_error[owner] = error.category
                if category == "transient":
                    attempt = min(self.retry.get(owner, (0, 0))[0] + 1, 10)
                    delay = min(MAX_BACKOFF_SECONDS, 2 ** (attempt - 1)) * (
                        0.5 + 0.5 * self.rng()
                    )
                    self.retry[owner] = (attempt, self.clock() + delay)
                elif category == "renewed":
                    self.last_authorization.pop(owner, None)  # Recheck membership next pass.
                elif category == "authentication":
                    identity.clear()
                    if self.identities.get(owner) is identity:
                        del self.identities[owner]
                else:
                    # permission/protocol: stop uploads; keep events pending and exportable.
                    self.blocked[owner] = category
                    self.permission_checked[owner] = self.clock()

    def _renew(self, identity, repo):
        """The cloud refused this user's token: renew the sign-in once (6C.1).

        Returns ``renewed`` when a refresh token that still works kept the user signed in (after
        a signing-key revocation, for example; uploads resume on the next pass), ``transient``
        when renewal must wait for the cloud, and ``authentication`` when the sign-in has ended
        and the user's events wait for their next online login.
        """
        if repo is None or not identity.active:
            return "authentication"
        try:
            self.auth.repository(identity, refused=repo.context.access_token)
        except RepositoryError as error:
            return "transient" if error.retryable else "authentication"
        return "renewed"

    def _heartbeat(self, repo, owner):
        """Report node health at most once a minute; a failed report never stops uploads."""
        if self.health is None or (
                self.clock() - self.last_heartbeat.get(owner, -1e9) < HEARTBEAT_SECONDS):
            return
        self.last_heartbeat[owner] = self.clock()
        try:
            repo.report_node_health(self.health(repo.context))
        except RepositoryError as error:
            self.heartbeat_error[owner] = error.category
        else:
            self.heartbeat_error.pop(owner, None)

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
