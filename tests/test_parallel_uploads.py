"""DEF-10 (Stage 6C.2): several upload batches in flight at once, with crash and duplicate checks.

A fake repository stands in for the cloud: it commits each event UUID once, replays a UUID it has
already committed (as ``store_flow_batch`` does), and can lose an acknowledgement or refuse a
batch. The outbox and the upload worker are the real ones.
"""

import threading
import time
from uuid import UUID, uuid4

from cloud_outbox import Outbox
from cloud_repository import FlowReceipt, RepositoryError, UserNodeContext
from cloud_sync import UPLOAD_PARALLELISM, SyncWorker


class Identity:
    def __init__(self, context):
        self.user_id = context.user_id
        self.active = True
        self.session_bound = True
        self.context = context

    def clear(self):
        self.active = False


class Cloud:
    """Commits by event UUID; ``faults`` maps a call number to 'lost_ack' or an error category."""

    def __init__(self, delay=0.05):
        self.delay = delay
        self.lock = threading.Lock()
        self.committed = {}
        self.calls = []
        self.replays = 0
        self.in_flight = 0
        self.most_in_flight = 0
        self.faults = {}

    def store(self, events):
        with self.lock:
            call = len(self.calls)
            self.calls.append([event["event_uuid"] for event in events])
            self.in_flight += 1
            self.most_in_flight = max(self.most_in_flight, self.in_flight)
            fault = self.faults.get(call)
        try:
            time.sleep(self.delay)
            if fault in ("transient", "permission", "authentication"):
                raise RepositoryError(fault)
            receipts = []
            with self.lock:
                for event in events:
                    replayed = event["event_uuid"] in self.committed
                    self.replays += replayed
                    number = self.committed.setdefault(event["event_uuid"], len(self.committed) + 1)
                    receipts.append(FlowReceipt(UUID(event["event_uuid"]), number, number, None,
                                                number, replayed))
            if fault == "lost_ack":
                raise RepositoryError("transient")  # Committed; the answer never arrived.
            return tuple(receipts)
        finally:
            with self.lock:
                self.in_flight -= 1


class Repo:
    def __init__(self, cloud, context):
        self.cloud = cloud
        self.context = context

    def node_access(self):
        return True

    def store_flows(self, events):
        return self.cloud.store(events)


class Auth:
    def __init__(self, cloud):
        self.cloud = cloud

    def repository(self, identity):
        return Repo(self.cloud, identity.context)


def spool_with(tmp_path, count):
    context = UserNodeContext(uuid4(), 1, uuid4(), "memory-only-token")
    spool = Outbox(tmp_path / "outbox")
    spool.start()
    for _ in range(count):
        spool.submit(context, "capture", {"event_uuid": str(uuid4()), "deployment_id": 1},
                     capped=False)
    deadline = time.monotonic() + 60
    while not spool.drain():
        assert time.monotonic() < deadline
    return spool, Identity(context)


def test_batches_upload_in_parallel_and_never_share_an_event(tmp_path):
    cloud = Cloud()
    spool, identity = spool_with(tmp_path, 300)
    try:
        worker = SyncWorker(Auth(cloud), spool)
        worker.sync_once(identity)
        assert cloud.most_in_flight == UPLOAD_PARALLELISM == 4
        assert all(len(batch) <= 50 for batch in cloud.calls)
        sent = [event for batch in cloud.calls for event in batch]
        assert len(sent) == len(set(sent)) == 300  # each event in exactly one request
        assert spool.counts(identity.context)["synced"] == 300
        assert spool.pending(identity.context) == []
    finally:
        spool.close()


def test_a_lost_acknowledgement_in_one_parallel_batch_replays_only_that_batch(tmp_path):
    cloud = Cloud()
    cloud.faults[1] = "lost_ack"
    spool, identity = spool_with(tmp_path, 200)
    try:
        worker = SyncWorker(Auth(cloud), spool)
        worker.sync_once(identity)
        owner = str(identity.user_id)
        assert worker.retry[owner][0] == 1 and worker.last_error[owner] == "transient"
        # The other batches were acknowledged; the cloud holds all 200 exactly once.
        assert spool.counts(identity.context)["pending"] == 50
        assert len(cloud.committed) == 200
        worker.retry.clear()
        worker.sync_once(identity)
        assert cloud.replays == 50
        assert len(cloud.committed) == 200
        counts = spool.counts(identity.context)
        assert (counts["synced"], counts["pending"]) == (200, 0)
        assert owner not in worker.retry
    finally:
        spool.close()


def test_a_refusal_outranks_a_transient_failure_and_keeps_other_receipts(tmp_path):
    cloud = Cloud()
    cloud.faults.update({0: "transient", 2: "permission"})
    spool, identity = spool_with(tmp_path, 200)
    try:
        worker = SyncWorker(Auth(cloud), spool)
        worker.sync_once(identity)
        owner = str(identity.user_id)
        assert worker.blocked[owner] == "permission"
        assert owner not in worker.retry
        counts = spool.counts(identity.context)
        # Two batches committed and were acknowledged; the refused ones stay pending, not rejected.
        assert (counts["synced"], counts["pending"], counts["rejected"]) == (100, 100, 0)
    finally:
        spool.close()


def test_one_request_at_a_time_remains_available(tmp_path):
    cloud = Cloud(delay=0)
    spool, identity = spool_with(tmp_path, 120)
    try:
        SyncWorker(Auth(cloud), spool, parallelism=1).sync_once(identity)
        assert cloud.most_in_flight == 1
        assert [len(batch) for batch in cloud.calls] == [50, 50, 20]
    finally:
        spool.close()


def test_a_pass_sends_at_most_ten_batches(tmp_path):
    cloud = Cloud(delay=0)
    spool, identity = spool_with(tmp_path, 620)
    try:
        worker = SyncWorker(Auth(cloud), spool)
        worker.sync_once(identity)
        assert len(cloud.calls) == 10
        assert spool.counts(identity.context)["pending"] == 120
        worker.sync_once(identity)
        assert spool.counts(identity.context)["synced"] == 620
    finally:
        spool.close()
