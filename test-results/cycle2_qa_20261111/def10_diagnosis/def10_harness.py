"""DEF-10 offline replica of a Save-every-flow burst on PC A (diagnostic, not a test).

Real Outbox and SyncWorker; a fake cloud whose store_flows takes REQUEST_S before its transaction
starts (TLS + upload from Manila) and SERVER_S more before it answers. A producer hands 300
events to the spool at PRODUCE_RATE/s while a capture-like thread holds the GIL for BUSY_MS out of
every BUSY_MS + IDLE_MS (the web process's capture loop at maximum speed). Delay = transaction
start minus the event's creation, as the pilot's received_at minus event_time.

    python def10_harness.py [shipped|grouped] [busy_ms] [idle_ms]
"""
import statistics
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
from uuid import UUID, uuid4

sys.path.insert(0, ".")
from cloud_outbox import Outbox  # noqa: E402
from cloud_repository import FlowReceipt  # noqa: E402
from cloud_sync import SyncWorker  # noqa: E402

REQUEST_S, SERVER_S = 0.35, 0.35
PRODUCE_RATE = 100.0
TRAFFIC = {f"feature_{i}": 1234.56789 + i for i in range(40)}
CONTEXT = SimpleNamespace(user_id=uuid4(), profile_id=7, node_id=uuid4(), access_token="t")
created, received = {}, {}


class FakeRepo:
    context = CONTEXT

    def node_access(self):
        time.sleep(REQUEST_S)
        return True

    def store_flows(self, events):
        time.sleep(REQUEST_S)
        now = time.perf_counter()
        for event in events:
            received.setdefault(event["event_uuid"], now)
        time.sleep(SERVER_S)
        return tuple(FlowReceipt(UUID(e["event_uuid"]), 1, 1, None, 1, False) for e in events)


class FakeAuth:
    def repository(self, identity, **_):
        return FakeRepo()


def grouped_writer(self):
    """Hypothesis: commit every waiting event in one transaction (scratch copy)."""
    import queue as q
    while not self.stop_event.is_set() or not self.queue.empty():
        try:
            items = [self.queue.get(timeout=0.2)]
        except q.Empty:
            continue
        while len(items) < 100:
            try:
                items.append(self.queue.get_nowait())
            except q.Empty:
                break
        try:
            with self.db_lock, self.db:
                for item in items:
                    (event_id, user_id, profile_id, node_id, session_id, deployment_id, created_at,
                     payload, capped) = item
                    self.db.execute("insert into event values"
                                    "(?,?,?,?,?,?,?,?,'pending',null,null,?)",
                                    (event_id, user_id, profile_id, node_id, session_id,
                                     deployment_id, created_at, payload, "h"))
            with self.lock:
                for item in items:
                    self.memory_status.pop(item[0], None)
                self.durable.notify_all()
        finally:
            for _ in items:
                self.queue.task_done()


def busy(stop, busy_ms, idle_ms):
    while not stop.is_set():
        end = time.perf_counter() + busy_ms / 1000
        total = 0
        while time.perf_counter() < end:
            for i in range(500):
                total += i * i
        if idle_ms:
            time.sleep(idle_ms / 1000)


def main(variant, busy_ms, idle_ms):
    if variant == "grouped":
        Outbox._writer = grouped_writer
    elif variant == "per-event":  # the writer before the DEF-10 fix: one transaction per event
        def per_event_writer(self):
            import queue as q
            while not self.stop_event.is_set() or not self.queue.empty():
                try:
                    item = self.queue.get(timeout=0.2)
                except q.Empty:
                    continue
                try:
                    self._persist_one(item)
                finally:
                    self.queue.task_done()
        Outbox._writer = per_event_writer
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
        box = Outbox(folder)
        identity = SimpleNamespace(user_id=CONTEXT.user_id, active=True, session_bound=True)
        sync = SyncWorker(FakeAuth(), box)
        sync.register(identity)
        sync.start()
        stop = threading.Event()
        if busy_ms:
            threading.Thread(target=busy, args=(stop, busy_ms, idle_ms), daemon=True).start()
        time.sleep(1.5)  # first pass done; the burst starts at a random point of the cycle
        for _ in range(300):
            event = {"event_uuid": str(uuid4()), "deployment_id": 3, "traffic": TRAFFIC,
                     "prediction": {"predicted_label": "Attack"}, "alert": {"d": "x" * 200}}
            created[event["event_uuid"]] = time.perf_counter()
            box.submit(CONTEXT, "burst", event)
            time.sleep(1 / PRODUCE_RATE)
        deadline = time.perf_counter() + 90
        while len(received) < 300 and time.perf_counter() < deadline:
            time.sleep(0.1)
        stop.set()
        sync.close()
        box.stop_event.set()
        delays = sorted(received[k] - created[k] for k in created if k in received)
        p95 = delays[max(0, int(0.95 * len(delays)) - 1)]
        print(f"{variant:8s} busy {busy_ms}/{idle_ms} ms: received {len(delays)}/300; delay p50 "
              f"{statistics.median(delays):.2f} s, p95 {p95:.2f} s, max {delays[-1]:.2f} s")


if __name__ == "__main__":
    args = sys.argv[1:] + ["shipped", "30", "30"][len(sys.argv[1:]):]
    main(args[0], int(args[1]), int(args[2]))
