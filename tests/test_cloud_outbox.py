import time
from uuid import uuid4

from cloud_outbox import MAX_AGE, Outbox
from cloud_repository import FlowReceipt, UserNodeContext


def context():
    return UserNodeContext(uuid4(), 1, uuid4(), "memory-only-token")


def event():
    return {"event_uuid": str(uuid4()), "deployment_id": 1, "value": "payload"}


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
        assert spool.drain()
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
        assert spool.expire(time.time() + MAX_AGE + 1) == 1
        assert spool.export_pending(owner) == []
        assert "capture" not in spool.reservations
    finally:
        spool.close()
