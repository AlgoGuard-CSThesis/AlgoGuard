import json
import threading
import time

import pytest

from services import database_service as db
from services import live_monitor_service as monitor
from services.deployment_service import deploy_model

DATASET = "algoguard_big.csv"


def model_id_for(run_id, model_name):
    with db.get_connection() as connection:
        row = connection.execute(
            "SELECT model_id FROM detection_model WHERE run_id = ? AND model_name = ?",
            (run_id, model_name),
        ).fetchone()
        assert row is not None
        return row["model_id"]


@pytest.fixture()
def fast_monitor(monkeypatch, trained_bundle):
    """Deploy the fixture stack and replay it as fast as the loop allows."""
    deploy_model(model_id_for(trained_bundle["run_id"], "Stacking Ensemble"), 1)
    monkeypatch.setitem(monitor.SPEED_CHOICES, "fast", 0.001)
    return trained_bundle


@pytest.fixture()
def replay_dataset(monkeypatch, tmp_path, trained_bundle):
    """Point the monitor at a CSV matching the fixture model's feature schema."""
    csv_path = tmp_path / DATASET
    trained_bundle["frame"].to_csv(csv_path, index=False)
    monkeypatch.setattr(monitor, "DATASET_FOLDER", str(tmp_path))
    return csv_path


def wait_for(condition, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if condition():
            return True
        time.sleep(0.05)
    return False


def start(**overrides):
    options = {"dataset": DATASET, "speed": "fast", "persist": "none"}
    options.update(overrides)
    return monitor.start_session(1, **options)


def test_idle_status_is_safe_before_any_session():
    status = monitor.get_status()
    assert status["session"]["state"] == "idle"
    assert status["events"] == []
    assert status["last_seq"] == 0


def test_invalid_options_are_rejected(replay_dataset):
    with pytest.raises(monitor.LiveMonitorError, match="bundled traffic samples"):
        monitor.start_session(1, dataset="../../etc/passwd")
    with pytest.raises(monitor.LiveMonitorError, match="replay speed"):
        start(speed="instant")
    with pytest.raises(monitor.LiveMonitorError, match="replay order"):
        start(order="backwards")
    with pytest.raises(monitor.LiveMonitorError, match="storage option"):
        start(persist="everything")


def test_session_classifies_flows_and_emits_ordered_events(fast_monitor, replay_dataset):
    start()
    assert wait_for(lambda: monitor.get_status()["session"]["totals"]["flows"] > 5)

    status = monitor.get_status()
    session = status["session"]
    assert session["state"] in ("running", "completed")
    assert session["deployment"]["model_name"] == "Stacking Ensemble"
    assert session["row_total"] == len(fast_monitor["frame"])

    sequences = [event["seq"] for event in status["events"]]
    assert sequences == sorted(sequences)
    event = status["events"][0]
    assert event["prediction"] in {"Normal", "Attack"}
    assert 0 <= event["confidence"] <= 100
    assert event["actual"] in {"Normal", "Attack"}
    assert event["match"] == (event["actual"] == event["prediction"])
    assert event["source_ip"].startswith("10.")
    assert event["destination_ip"].startswith("192.168.")

    totals = session["totals"]
    assert totals["attacks"] + totals["normals"] == totals["flows"]


def test_since_filter_returns_only_newer_events(fast_monitor, replay_dataset):
    start()
    assert wait_for(lambda: monitor.get_status()["session"]["totals"]["flows"] > 5)

    first = monitor.get_status()
    later = monitor.get_status(since_seq=first["last_seq"])
    assert all(event["seq"] > first["last_seq"] for event in later["events"])


@pytest.mark.parametrize("old_cursor", [1, 100])
def test_status_resets_a_cursor_from_another_session(authenticated_client, old_cursor):
    old_session = monitor._new_session("csv", None, DATASET, "fast", "sequential", "none", 1)
    new_session = monitor._new_session("csv", None, DATASET, "fast", "sequential", "none", 1)
    assert old_session["session_id"] != new_session["session_id"]
    new_session.update(state="running", seq=3)
    monitor._SESSION = new_session
    monitor._EVENTS.extend({"seq": seq} for seq in range(1, 4))

    response = authenticated_client.get(
        f"/monitor/status?since={old_cursor}&session_id={old_session['session_id']}"
    )
    status = response.get_json()

    assert status["session"]["session_id"] == new_session["session_id"]
    assert [event["seq"] for event in status["events"]] == [1, 2, 3]
    response = authenticated_client.get(
        f"/monitor/status?since=3&session_id={new_session['session_id']}"
    )
    assert response.get_json()["events"] == []


def test_restart_with_no_events_still_exposes_new_session_identity():
    monitor._SESSION = monitor._new_session("csv", None, DATASET, "fast", "sequential", "none", 1)
    status = monitor.get_status(100, "previous-session")
    assert status["session"]["session_id"] != "previous-session"
    assert status["last_seq"] == 0
    assert status["events"] == []


def test_pause_freezes_the_stream_and_resume_continues(fast_monitor, replay_dataset):
    start(speed="slow")
    assert wait_for(lambda: monitor.get_status()["session"]["totals"]["flows"] >= 1)

    assert monitor.pause_session()["state"] == "paused"
    frozen = monitor.get_status()["session"]["totals"]["flows"]
    time.sleep(0.5)
    assert monitor.get_status()["session"]["totals"]["flows"] == frozen

    assert monitor.resume_session()["state"] == "running"


def test_stop_ends_the_session_and_joins_the_thread(fast_monitor, replay_dataset):
    start()
    assert wait_for(lambda: monitor.get_status()["session"]["totals"]["flows"] >= 1)

    stopped = monitor.stop_session()
    assert stopped["state"] in ("stopped", "completed")
    assert monitor._THREAD is None or not monitor._THREAD.is_alive()

    with pytest.raises(monitor.LiveMonitorError, match="No monitoring session"):
        monitor.stop_session()


def test_second_session_is_refused_while_one_runs(fast_monitor, replay_dataset):
    start(speed="slow")
    assert wait_for(lambda: monitor.get_status()["session"]["state"] == "running")

    with pytest.raises(monitor.LiveMonitorError, match="already running"):
        start()


def test_stopping_worker_blocks_a_replacement_session():
    class StubbornThread:
        def __init__(self):
            self.alive = True
            self.joined = False

        def is_alive(self):
            return self.alive

        def join(self, timeout=None):
            self.joined = True

    thread = StubbornThread()
    monitor._SESSION = monitor._new_session("csv", None, DATASET, "fast", "sequential", "none", 1)
    monitor._THREAD = thread
    monitor._STOP_EVENT = threading.Event()
    monitor._PAUSE_EVENT = threading.Event()

    stopped = monitor.stop_session()

    assert thread.joined is True
    assert stopped["state"] == "stopping"
    with pytest.raises(monitor.LiveMonitorError, match="already running"):
        start()

    thread.alive = False


def test_session_only_mode_stores_nothing(fast_monitor, replay_dataset):
    before = len(db.list_alerts(limit=1000))
    start(persist="none")
    assert wait_for(lambda: monitor.get_status()["session"]["totals"]["flows"] > 5)
    monitor.stop_session()

    assert monitor.get_status()["session"]["totals"]["persisted"] == 0
    assert len(db.list_alerts(limit=1000)) == before


def test_attack_mode_persists_alerts_only(fast_monitor, replay_dataset):
    before = len(db.list_alerts(limit=1000))
    start(persist="attacks")
    assert wait_for(
        lambda: monitor.get_status()["session"]["totals"]["attacks"] >= 3,
        timeout=20.0,
    )
    monitor.stop_session()

    totals = monitor.get_status()["session"]["totals"]
    assert totals["persisted"] == totals["alerts"] == len(db.list_alerts(limit=1000)) - before
    assert totals["persisted"] <= monitor.MAX_PERSISTED_PER_SESSION
    sources = {row["dataset_source"] for row in db.count_traffic_by_source()}
    assert "live_monitor" in sources


def test_all_mode_persists_every_flow(fast_monitor, replay_dataset):
    start(persist="all")
    assert wait_for(lambda: monitor.get_status()["session"]["totals"]["flows"] > 5)
    monitor.stop_session()

    totals = monitor.get_status()["session"]["totals"]
    assert totals["persisted"] == totals["flows"]
    assert totals["alerts"] == totals["attacks"]


@pytest.mark.parametrize(
    ("field", "value"),
    [("dur", None), ("sbytes", "not-a-number"), ("dbytes", float("inf")),
     ("proto", None), ("state", "")],
)
def test_replay_imputes_invalid_features_and_persists_valid_json(
    fast_monitor, replay_dataset, field, value
):
    frame = fast_monitor["frame"].head(1).copy()
    frame[field] = value
    frame.to_csv(replay_dataset, index=False)

    start(persist="all")
    assert wait_for(lambda: monitor.get_status()["session"]["state"] in {"completed", "error"})
    status = monitor.get_status()
    assert status["session"]["state"] == "completed", status["session"]["error_message"]
    assert status["session"]["totals"]["persisted"] == 1
    with db.get_connection() as connection:
        saved = connection.execute(
            """SELECT nt.feature_payload, p.input_payload FROM prediction p
               JOIN network_traffic nt ON nt.traffic_id = p.traffic_id
               WHERE p.prediction_id = ?""",
            (status["events"][0]["prediction_id"],),
        ).fetchone()
    for payload in saved:
        decoded = json.loads(payload, parse_constant=lambda value: pytest.fail(value))
        assert decoded[field] is None


def test_numeric_replay_labels_match_the_same_verdicts_as_text_labels(
    fast_monitor, replay_dataset
):
    frame = fast_monitor["frame"].head(8).copy()
    artifact, _ = monitor.load_active_artifact()
    expected_predictions = artifact["pipeline"].predict(frame[artifact["feature_columns"]])
    expected_mismatches = int(
        (expected_predictions != (frame["label"] == "Attack").astype(int)).sum()
    )
    frame["label"] = (frame["label"] == "Attack").astype(int)
    frame.to_csv(replay_dataset, index=False)

    start()
    assert wait_for(lambda: monitor.get_status()["session"]["state"] in {"completed", "error"})
    status = monitor.get_status()
    assert status["session"]["state"] == "completed", status["session"]["error_message"]
    assert status["session"]["totals"]["mismatches"] == expected_mismatches
    assert all(event["actual"] in {"Normal", "Attack"} for event in status["events"])


def test_storage_cap_stops_writing_but_keeps_classifying(
    monkeypatch,
    fast_monitor,
    replay_dataset,
):
    monkeypatch.setattr(monitor, "MAX_PERSISTED_PER_SESSION", 3)
    start(persist="all")
    assert wait_for(lambda: monitor.get_status()["session"]["totals"]["flows"] >= 8)
    monitor.stop_session()

    session = monitor.get_status()["session"]
    assert session["capped"] is True
    assert session["totals"]["persisted"] == 3
    assert session["totals"]["flows"] >= 8


def test_session_runs_to_completion(fast_monitor, replay_dataset):
    start()
    assert wait_for(
        lambda: monitor.get_status()["session"]["state"] == "completed",
        timeout=30.0,
    )

    session = monitor.get_status()["session"]
    assert session["totals"]["flows"] == session["row_total"]
    assert session["error_message"] is None

    # A finished session must not block the next one.
    start()
    assert monitor.get_status()["session"]["state"] in ("starting", "running")


def test_session_completion_waits_for_final_bookkeeping(
    fast_monitor, replay_dataset, monkeypatch
):
    fast_monitor["frame"].head(1).to_csv(replay_dataset, index=False)
    logging_started = threading.Event()
    finish_logging = threading.Event()
    original_log = monitor.log_system_event

    def slow_completion_log(admin_id, module, action, *args, **kwargs):
        if action == "monitor_completed":
            logging_started.set()
            assert finish_logging.wait(timeout=5)
        return original_log(admin_id, module, action, *args, **kwargs)

    monkeypatch.setattr(monitor, "log_system_event", slow_completion_log)
    start()
    try:
        assert logging_started.wait(timeout=5)
        assert monitor.get_status()["session"]["state"] in monitor.ACTIVE_STATES
    finally:
        finish_logging.set()

    assert wait_for(lambda: monitor.get_status()["session"]["state"] == "completed")
    start()
    assert monitor.get_status()["session"]["state"] in monitor.ACTIVE_STATES


def test_random_order_still_pairs_labels_with_flows(fast_monitor, replay_dataset):
    start(order="random")
    assert wait_for(lambda: monitor.get_status()["session"]["totals"]["flows"] > 10)
    monitor.stop_session()

    events = monitor.get_status(since_seq=0)["events"]
    assert all(event["actual"] in {"Normal", "Attack"} for event in events)
    # Shuffling must not silently misalign a row with another row's label.
    totals = monitor.get_status()["session"]["totals"]
    assert totals["mismatches"] < totals["flows"]


def test_missing_deployment_reports_an_error(monkeypatch, replay_dataset):
    from services import deployment_service

    monkeypatch.setattr(deployment_service, "get_active_deployment", lambda: None)
    monkeypatch.setattr(monitor, "load_active_artifact", deployment_service.load_active_artifact)
    start()
    assert wait_for(lambda: monitor.get_status()["session"]["state"] == "error")
    assert "No model has been deployed" in monitor.get_status()["session"]["error_message"]


def test_monitor_page_renders_controls(authenticated_client, fast_monitor):
    response = authenticated_client.get("/monitor")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'id="monitorStart"' in html
    assert 'id="monitorChart"' in html
    assert "/monitor/status" in html
    assert 'const ACTIVE_STATES = ["starting", "running", "paused", "stopping"]' in html
    assert "startButton.disabled = monitorIsActive ||" in html
    for dataset in monitor.DATASET_CHOICES:
        assert dataset in html


def test_monitor_endpoints_require_authentication(app_module):
    anonymous = app_module.app.test_client()
    for path in ("/monitor/start", "/monitor/pause", "/monitor/resume", "/monitor/stop"):
        response = anonymous.post(path, json={})
        assert response.status_code == 401
        assert response.get_json()["status"] == "error"

    status_response = anonymous.get("/monitor/status")
    assert status_response.status_code == 401


def test_monitor_routes_drive_a_session(authenticated_client, fast_monitor, replay_dataset):
    started = authenticated_client.post(
        "/monitor/start",
        json={"dataset": DATASET, "speed": "fast", "persist": "none"},
    )
    assert started.status_code == 200
    assert started.get_json()["session"]["state"] in ("starting", "running")

    conflict = authenticated_client.post("/monitor/start", json={"dataset": DATASET})
    assert conflict.status_code == 409
    assert conflict.get_json()["status"] == "error"

    assert wait_for(lambda: monitor.get_status()["session"]["totals"]["flows"] >= 1)
    status = authenticated_client.get("/monitor/status?since=0").get_json()
    assert status["status"] == "success"
    assert status["last_seq"] >= 1
    assert "totals" in status["session"]

    assert authenticated_client.post("/monitor/stop").status_code == 200
