import time
from uuid import uuid4

from cloud_outbox import MAX_AGE, TOMBSTONE_AGE, Outbox
from cloud_repository import FlowReceipt, UserNodeContext


def context():
    return UserNodeContext(uuid4(), 1, uuid4(), "memory-only-token")


def event():
    return {"event_uuid": str(uuid4()), "deployment_id": 1, "value": "payload"}


def drained(spool, within=60):
    """Outbox.drain() waits at most 5 s; slower disks (CI runners) need several rounds."""
    deadline = time.monotonic() + within
    while not spool.drain():
        if time.monotonic() > deadline:
            return False
    return True


def test_durable_restart_original_owner_and_acknowledgement(tmp_path):
    owner = context()
    row = event()
    spool = Outbox(tmp_path / "outbox")
    assert spool.submit(owner, "capture", row)["persistence"] == "in_memory"
    assert spool.pending(owner) == []
    spool.start()
    assert spool.drain()
    assert spool.counts(owner)["pending"] == 1
    spool.close()
    recovered = Outbox(tmp_path / "outbox")
    try:
        assert recovered.pending(owner) == [row]
        assert recovered.pending(context()) == []
        receipt = FlowReceipt(uuid4(), 1, 2, None, 3, False)
        recovered.acknowledge(owner, [receipt])
        assert recovered.pending(owner) == [row]
        from uuid import UUID

        receipt = FlowReceipt(UUID(row["event_uuid"]), 1, 2, None, 3, False)
        recovered.acknowledge(owner, [receipt])
        assert recovered.pending(owner) == []
        assert recovered.counts(owner)["synced"] == 1
        assert recovered.db.execute("select payload from event").fetchone()[0] == "{}"
    finally:
        recovered.close()


def test_cap_reserves_before_enqueue_and_retry_does_not_consume_slot(tmp_path):
    owner = context()
    spool = Outbox(tmp_path / "outbox")
    rows = [event() for _ in range(300)]
    try:
        for row in rows:
            assert spool.submit(owner, "capture", row)["persistence"] == "in_memory"
        assert spool.submit(owner, "capture", rows[0])["persistence"] == "in_memory"
        assert spool.reservations["capture"] == 300
        assert spool.submit(owner, "capture", event())["persistence"] == "dropped"
        spool.start()
        assert drained(spool)
        assert len(spool.pending(owner)) == 50
        assert spool.counts(owner)["pending"] == 300
    finally:
        spool.close()


def test_handoff_overflow_and_conflicting_identity_are_explicit(tmp_path):
    owner = context()
    spool = Outbox(tmp_path / "outbox", handoff_limit=1)
    try:
        first = event()
        assert spool.submit(owner, "capture", first)["persistence"] == "in_memory"
        assert spool.submit(owner, "capture", event())["persistence"] == "dropped"
        assert (
            spool.submit(owner, "capture", {**first, "value": "changed"})["persistence"]
            == "rejected"
        )
        spool.start()
        assert spool.drain()
    finally:
        spool.close()


def commits_of(spool):
    """Count the spool's committed transactions from here on (each costs a disk flush)."""
    statements = []
    spool.db.set_trace_callback(statements.append)
    return lambda: sum(1 for text in statements if text.strip().upper().startswith("COMMIT"))


def test_a_burst_waiting_in_the_handoff_is_saved_in_a_few_writes(tmp_path):
    """DEF-10: one flushed transaction per event let a Save-every-flow burst outrun the spool on
    a PC with a slow disk flush, so uploads waited for records still in memory."""
    owner = context()
    spool = Outbox(tmp_path / "outbox")
    try:
        rows = [event() for _ in range(150)]
        for row in rows:
            assert spool.submit(owner, "capture", row)["persistence"] == "in_memory"
        commits = commits_of(spool)
        spool.start()
        assert drained(spool)
        assert spool.counts(owner)["pending"] == 150
        assert commits() <= 2
        assert spool.reservations["capture"] == 150
    finally:
        spool.close()


def test_a_conflict_in_a_saved_group_refuses_only_its_own_event(tmp_path):
    owner = context()
    original = event()
    first = Outbox(tmp_path / "outbox")
    first.submit(owner, "capture", original)
    first.start()
    assert drained(first)
    first.close()
    spool = Outbox(tmp_path / "outbox")  # a restart: the handoff no longer knows the event
    try:
        before, changed, after = event(), {**original, "value": "changed"}, event()
        for row in (before, changed, after):
            assert spool.submit(owner, "capture", row)["persistence"] == "in_memory"
        commits = commits_of(spool)
        spool.start()
        assert drained(spool)
        assert commits() == 1
        assert not spool.disk_failed
        assert spool.lost[original["event_uuid"]] == "identity_conflict"
        assert spool.reservations["capture"] == 3  # the refused event gave its slot back
        assert spool.pending(owner) == [original, before, after]
    finally:
        spool.close()


def test_a_disk_filling_up_during_a_group_keeps_the_events_that_fit(tmp_path):
    owner = context()
    spool = Outbox(tmp_path / "outbox")
    try:
        rows = [{**event(), "pad": "x" * 6000} for _ in range(20)]
        for row in rows:
            spool.submit(owner, "capture", row, capped=False)
        pages = spool.db.execute("pragma page_count").fetchone()[0]
        spool.db.execute(f"pragma max_page_count={pages + 12}")
        spool.start()
        assert drained(spool)
        states = spool.statuses(owner, [row["event_uuid"] for row in rows])
        saved = [key for key, value in states.items() if value["persistence"] == "durable_pending"]
        dropped = [key for key, value in states.items() if value["persistence"] == "dropped"]
        assert saved and dropped and len(saved) + len(dropped) == 20
        assert spool.disk_failed and spool.loss_reason == "quota"
        assert spool.db.execute("pragma integrity_check").fetchone()[0] == "ok"
    finally:
        spool.close()


def test_flow_capacity_leaves_reserved_lifecycle_space(tmp_path):
    owner = context()
    spool = Outbox(tmp_path / "outbox", total_bytes=4 * 1024 * 1024)
    spool.start()
    try:
        for _ in range(30):
            spool.submit(owner, "capture", {**event(), "value": "x" * 60000})
        assert spool.drain()
        assert spool.disk_failed and spool.dropped > 0
        spool.save_lifecycle(owner, "capture", {"capture_id": 1, "status": "stopped"})
        assert spool.pending_lifecycle(owner)[0][1]["status"] == "stopped"
        assert sum(path.stat().st_size for path in spool.directory.iterdir()) < 4 * 1024 * 1024
        spool.finish_lifecycle(owner, "capture")
        assert spool.pending_lifecycle(owner) == []
    finally:
        spool.close()


def test_permanent_rejection_export_and_seven_day_expiry(tmp_path):
    owner = context()
    spool = Outbox(tmp_path / "outbox")
    spool.start()
    try:
        row = event()
        spool.submit(owner, "capture", row)
        assert spool.drain()
        spool.reject(owner, [row], "permission")
        assert spool.pending(owner) == []
        exported = spool.export_pending(owner)
        assert exported[0]["status"] == "rejected" and exported[0]["payload"] == row
        assert spool.export_pending(context()) == []
        # After seven days the rejected record stays visible, without its payload (6B.3).
        assert spool.expire(time.time() + MAX_AGE + 1) == 0
        exported = spool.export_pending(owner)
        assert exported[0]["status"] == "rejected" and exported[0]["reason"] == "permission"
        assert exported[0]["payload"] == {}
        assert "capture" not in spool.reservations
        spool.expire(time.time() + TOMBSTONE_AGE + 1)
        assert spool.export_pending(owner) == []
    finally:
        spool.close()


def test_an_unsent_event_expires_as_rejected_never_as_synced(tmp_path):
    """6B.3: local pending events expire after seven days and stay visible as expired."""
    owner = context()
    spool = Outbox(tmp_path / "outbox")
    spool.start()
    try:
        row = event()
        spool.submit(owner, "capture", row)
        assert spool.drain()
        assert spool.expire(time.time() + MAX_AGE + 1) == 1
        assert spool.pending(owner) == []
        counts = spool.counts(owner)
        assert (counts["pending"], counts["rejected"], counts["synced"]) == (0, 1, 0)
        assert counts["expired"] == 1
        exported = spool.export_pending(owner)
        assert exported[0]["status"] == "rejected" and exported[0]["reason"] == "expired"
    finally:
        spool.close()
