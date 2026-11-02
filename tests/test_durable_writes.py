"""Stage 6C.2 (offline): a genuinely full spool, bounded audit during a long outage, a local disk
error while acknowledging, and a dashboard that tells the four outcomes apart.

The injected failures on the real stack (lost acknowledgements, kills, a stalled network) are in
``tests/integration/test_durable_recovery.py``.
"""

import sqlite3
import time
from uuid import uuid4

from cloud_app import _owner
from cloud_outbox import SUMMARY_LIMITS, Outbox
from cloud_repository import UserNodeContext
from cloud_sync import audit
from tests.cloud_support import post_json, signed_in, wait_for
from tests.test_cloud_app import MANUAL_FLOW


def context():
    return UserNodeContext(uuid4(), 1, uuid4(), "memory-only-token")


def settled(spool):
    deadline = time.monotonic() + 60
    while not spool.drain():
        assert time.monotonic() < deadline


def test_a_genuinely_full_spool_stops_saving_and_resumes_when_space_returns(tmp_path):
    owner = context()
    spool = Outbox(tmp_path / "outbox")
    spool.start()
    try:
        kept = {"event_uuid": str(uuid4()), "deployment_id": 1}
        spool.submit(owner, "capture", kept, capped=False)
        settled(spool)
        # The disk fills up: SQLite itself refuses the next pages ("database or disk is full").
        pages = spool.db.execute("pragma page_count").fetchone()[0]
        spool.db.execute(f"pragma max_page_count={pages + 2}")
        outcomes = [spool.submit(owner, "capture", {"event_uuid": str(uuid4()),
                                                    "deployment_id": 1, "pad": "x" * 6000},
                                 capped=False) for _ in range(20)]
        settled(spool)
        assert spool.disk_failed and spool.loss_reason == "quota"
        dropped = [item for item in outcomes
                   if spool.statuses(owner, [item["event_uuid"]]).get(item["event_uuid"], {})
                   .get("persistence") == "dropped"]
        assert dropped, "some events must have been refused by the full disk"
        refused = spool.submit(owner, "capture", {"event_uuid": str(uuid4()),
                                                  "deployment_id": 1}, capped=False)
        assert refused == {"event_uuid": refused["event_uuid"], "persistence": "dropped",
                           "reason": "storage"}
        # What was saved before stays intact and pending.
        assert spool.statuses(owner, [kept["event_uuid"]])[kept["event_uuid"]] == {
            "persistence": "durable_pending"}
        assert spool.db.execute("pragma integrity_check").fetchone()[0] == "ok"
        # Space returns: saving resumes on the next capacity check, without a restart.
        spool.db.execute("pragma max_page_count=1073741823")
        assert spool.recheck_capacity()
        resumed = spool.submit(owner, "capture", {"event_uuid": str(uuid4()),
                                                  "deployment_id": 1}, capped=False)
        settled(spool)
        assert spool.statuses(owner, [resumed["event_uuid"]])[resumed["event_uuid"]] == {
            "persistence": "durable_pending"}
    finally:
        spool.close()


def test_a_journal_removed_while_the_spool_is_measured_is_not_a_disk_failure(tmp_path,
                                                                             monkeypatch):
    """Found by the 15-minute 6C.4 run: the upload worker's transaction deleted its rollback
    journal between the writer listing the spool folder and reading the journal's size. That was
    taken for a disk failure, and saving stopped until the application restarted."""
    owner = context()
    spool = Outbox(tmp_path / "outbox")
    real = spool.directory

    class Journal:  # listed and still a file, then gone before it is measured
        def is_file(self):
            return True

        def stat(self):
            raise FileNotFoundError("outbox.sqlite3-journal")

    class Racing:
        def iterdir(self):
            return iter([*real.iterdir(), Journal()])

        def __truediv__(self, other):
            return real / other

    monkeypatch.setattr(spool, "directory", Racing())
    spool.start()
    try:
        row = {"event_uuid": str(uuid4()), "deployment_id": 1}
        spool.submit(owner, "capture", row, capped=False)
        settled(spool)
        assert not spool.disk_failed
        assert spool.statuses(owner, [row["event_uuid"]])[row["event_uuid"]] == {
            "persistence": "durable_pending"}
    finally:
        spool.close()


def test_synced_receipts_are_capped_so_the_spool_stays_bounded(tmp_path, monkeypatch):
    """6C.4: receipts of uploaded events go oldest first beyond the cap; nothing unsent does."""
    import cloud_outbox
    from cloud_repository import FlowReceipt

    monkeypatch.setattr(cloud_outbox, "SYNCED_KEEP", 50)
    owner = context()
    spool = Outbox(tmp_path / "outbox")
    spool.start()
    try:
        rows = [{"event_uuid": str(uuid4()), "deployment_id": 1, "pad": "x" * 3000}
                for _ in range(200)]
        for row in rows:
            spool.submit(owner, "capture", row, capped=False)
        settled(spool)
        from uuid import UUID

        spool.acknowledge(owner, [FlowReceipt(UUID(row["event_uuid"]), 1, 1, None, 1, False)
                                  for row in rows[:180]])
        spool.reject(owner, [rows[180]], "validation")
        grown = spool._used_bytes()
        spool.expire()
        counts = spool.counts(owner)
        assert (counts["synced"], counts["pending"], counts["rejected"]) == (50, 19, 1)
        newest = [row["event_uuid"] for row in rows[130:180]]
        assert {key: value["persistence"] for key, value in spool.statuses(owner, newest).items()
                } == dict.fromkeys(newest, "synced")
        for start in (0, 100):  # statuses() answers at most 100 ids per call
            older = [row["event_uuid"] for row in rows[start:min(start + 100, 130)]]
            assert spool.statuses(owner, older) == {}
        assert spool._used_bytes() < grown / 2  # emptied pages are free for reuse
    finally:
        spool.close()


def test_audit_entries_stay_within_their_reserve_during_a_long_outage(tmp_path):
    owner = context()
    spool = Outbox(tmp_path / "outbox")
    try:
        # Hours of failures, each wanting an audit entry: the reserve takes the first 96 only.
        results = [audit(spool, owner, "Live Monitor", "upload_failed", "Failed", "x" * 3000)
                   for _ in range(1000)]
        assert results.count(True) == SUMMARY_LIMITS["log"] == 96
        assert spool.summaries_dropped == 904  # counted, never logged anywhere else
        assert len([row for row in spool.pending_summaries(owner, limit=1000)
                    if row[1] == "log"]) == 96
        # Audit never crowds out capture summaries: their own reserve is untouched.
        for index in range(SUMMARY_LIMITS["capture"]):
            spool.save_lifecycle(owner, f"capture-{index}", {"capture_id": index + 1,
                                                             "status": "stopped"})
        kinds = [row[1] for row in spool.pending_summaries(owner, limit=1000)]
        assert (kinds.count("log"), kinds.count("capture")) == (96, 32)
        size = sum(path.stat().st_size for path in spool.directory.iterdir())
        assert size < 1024 * 1024
        # A spool that cannot be written makes audit return False; it never raises.
        spool.db.close()
        assert audit(spool, owner, "Authentication", "logout", "Success") is False
    finally:
        spool.close()


def test_a_local_disk_error_while_acknowledging_is_retried_without_duplicates(cloud,
                                                                               monkeypatch):
    client = signed_in(cloud.app)
    identity = next(item.identity for item in cloud.app.session_interface.sessions.values()
                    if item.identity is not None)
    worker = cloud.services.sync
    worker.close()
    event = post_json(client, "/predict", MANUAL_FLOW).get_json()["event_uuid"]
    outbox = cloud.services.outbox
    original = outbox.acknowledge
    failures = []

    def failing(context, receipts):
        if not failures:
            failures.append(1)
            raise sqlite3.OperationalError("disk I/O error")
        return original(context, receipts)

    monkeypatch.setattr(outbox, "acknowledge", failing)
    worker.start()
    wait_for(lambda: worker.last_error.get(str(identity.user_id)) == "local_storage"
             or outbox.statuses(identity, [event])[event]["persistence"] == "synced",
             timeout=20)
    worker.retry.clear()
    wait_for(lambda: outbox.statuses(identity, [event])[event]["persistence"] == "synced",
             timeout=20)
    stored = [row for row in cloud.fake.rows("ingest_event") if row["event_uuid"] == event]
    assert failures and len(stored) == 1
    # The failure was never written into the spool that had just failed.
    assert not [row for row in outbox.pending_summaries(_owner(identity), limit=100)
                if "disk" in str(row[2]).lower()]


def test_the_dashboard_tells_dropped_pending_rejected_and_synced_apart(cloud):
    client = signed_in(cloud.app)
    identity = next(item.identity for item in cloud.app.session_interface.sessions.values()
                    if item.identity is not None)
    synced = post_json(client, "/predict", MANUAL_FLOW).get_json()["event_uuid"]
    wait_for(lambda: client.get(f"/api/events/{synced}").get_json().get("synced"), timeout=20)
    cloud.services.sync.close()
    rejected = post_json(client, "/predict", MANUAL_FLOW).get_json()["event_uuid"]
    cloud.fake.insert("ingest_event", {"event_uuid": rejected, "node_id": cloud.node,
                                       "owner_profile_id": cloud.analyst["profile_id"],
                                       "payload": {"different": True}, "traffic_id": 1,
                                       "prediction_id": 1, "alert_id": None, "audit_log_id": 1})
    cloud.services.sync.sync_once(identity)
    pending = post_json(client, "/predict", MANUAL_FLOW).get_json()["event_uuid"]
    cloud.services.outbox.drain()
    with cloud.services.outbox.lock:
        cloud.services.outbox.disk_failed = True
        cloud.services.outbox.loss_reason = "disk"
    dropped = post_json(client, "/predict", MANUAL_FLOW).get_json()
    assert dropped["persistence"] == "dropped"
    page = client.get("/").get_data(as_text=True)
    assert "<dt>Pending upload</dt><dd>1 durable" in page
    assert "<dt>Synced</dt><dd>1 committed in the cloud (this computer's recent uploads)" in page
    assert "<dt>Rejected</dt><dd>1" in page
    assert "<dt>Not stored</dt><dd>1 &middot; local storage stopped (disk)" in page
    states = cloud.services.outbox.statuses(identity, [synced, rejected, pending])
    assert [states[key]["persistence"] for key in (synced, rejected, pending)] == [
        "synced", "rejected", "durable_pending"]
