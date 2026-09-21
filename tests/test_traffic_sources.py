"""Tests for the Live Monitor's traffic sources, including PCAP replay."""

import sqlite3
import threading
import time
from functools import partial
from unittest.mock import Mock

import pandas as pd
import pytest

import config
from services import live_monitor_service as monitor
from services import traffic_source_service as traffic_sources
from services.deployment_service import load_active_artifact
from services.traffic_source_service import (
    CsvReplaySource,
    PcapReplaySource,
    TrafficSourceCancelled,
    TrafficSourceError,
    live_capture_available,
)

# The application imports scapy lazily on purpose, so the web app starts and
# both replay modes keep working on a machine with no capture stack (see
# services/traffic_source_service's module docstring). A hard `import
# scapy.all` here broke that contract: without scapy, pytest failed during
# COLLECTION and took the entire run down with it -- zero results instead of
# every other test still reporting. Skipping just this module keeps the
# suite's dependency story matching the application's.
#
# Placed after the imports above, which need no capture stack, so every
# import still sits at module top and ruff's E402 stays quiet.
scapy_all = pytest.importorskip(
    "scapy.all",
    reason="PCAP and live-capture tests need scapy: python -m pip install -r requirements.txt",
)


def write_sample_pcap(path, sessions=3):
    """Write a small capture: N complete HTTP sessions plus one DNS exchange."""
    # Explicit fixture MAC addresses prevent wrpcap from resolving real neighbors.
    Ether = partial(scapy_all.Ether, src="02:00:00:00:00:01", dst="02:00:00:00:00:02")
    IP = scapy_all.IP
    TCP = scapy_all.TCP
    UDP = scapy_all.UDP

    packets = []
    base = 1_700_000_000.0
    for index in range(sessions):
        client = f"192.168.1.{10 + index}"
        server = "203.0.113.7"
        sport = 51000 + index
        start = base + index * 2.0
        stream = [
            (0.00, IP(src=client, dst=server, ttl=62) / TCP(sport=sport, dport=80, flags="S")),
            (0.02, IP(src=server, dst=client, ttl=250) / TCP(sport=80, dport=sport, flags="SA")),
            (0.04, IP(src=client, dst=server, ttl=62) / TCP(sport=sport, dport=80, flags="A")),
            (
                0.06,
                IP(src=client, dst=server, ttl=62)
                / TCP(sport=sport, dport=80, flags="PA")
                / (b"x" * 120),
            ),
            (
                0.10,
                IP(src=server, dst=client, ttl=250)
                / TCP(sport=80, dport=sport, flags="PA")
                / (b"y" * 400),
            ),
            (0.14, IP(src=client, dst=server, ttl=62) / TCP(sport=sport, dport=80, flags="FA")),
            (0.16, IP(src=server, dst=client, ttl=250) / TCP(sport=80, dport=sport, flags="FA")),
            (0.18, IP(src=client, dst=server, ttl=62) / TCP(sport=sport, dport=80, flags="A")),
        ]
        for offset, payload in stream:
            frame = Ether() / payload
            frame.time = start + offset
            packets.append(frame)

    query = (
        Ether() / IP(src="192.168.1.50", dst="198.51.100.9", ttl=64) / UDP(sport=40000, dport=53)
    )
    query.time = base + 30.0
    answer = (
        Ether() / IP(src="198.51.100.9", dst="192.168.1.50", ttl=120) / UDP(sport=53, dport=40000)
    )
    answer.time = base + 30.02
    packets.extend([query, answer])

    scapy_all.wrpcap(str(path), packets)
    return path


def test_pcap_source_aggregates_packets_into_flows(tmp_path):
    pcap_path = write_sample_pcap(tmp_path / "sample.pcap", sessions=3)

    source = PcapReplaySource(str(pcap_path))
    source.prepare()

    assert source.row_total is None  # sequential replay does not pre-buffer the full capture
    events = []
    while True:
        try:
            events.append(source.next_event())
        except StopIteration:
            break

    assert source.row_total == 4  # three HTTP sessions plus one DNS exchange

    http_events = [event for event in events if event["record"]["service"] == "http"]
    dns_events = [event for event in events if event["record"]["service"] == "dns"]
    assert len(http_events) == 3
    assert len(dns_events) == 1

    event = http_events[0]
    assert set(event["record"]) == set(source.columns)
    assert event["actual"] is None
    assert event["source_ip"].startswith("192.168.1.")
    assert event["destination_ip"] == "203.0.113.7"
    assert event["destination_port"] == 80
    assert event["record"]["state"] == "FIN"
    assert event["record"]["spkts"] >= 4
    assert event["record"]["sttl"] == 62
    assert event["record"]["dttl"] == 250


def test_pcap_source_rejects_captures_without_ip_flows(tmp_path):
    empty = tmp_path / "empty.pcap"
    scapy_all.wrpcap(
        str(empty),
        [scapy_all.Ether(src="02:00:00:00:00:01", dst="ff:ff:ff:ff:ff:ff") / scapy_all.ARP()],
    )
    source = PcapReplaySource(str(empty))
    with pytest.raises(TrafficSourceError, match="no classifiable IP flows"):
        source.prepare()


def test_pcap_source_honors_cancellation_before_preparation(tmp_path):
    pcap_path = write_sample_pcap(tmp_path / "cancelled.pcap", sessions=1)
    cancel_event = threading.Event()
    cancel_event.set()

    source = PcapReplaySource(str(pcap_path), cancel_event=cancel_event)
    with pytest.raises(TrafficSourceCancelled, match="cancelled"):
        source.prepare()


def test_sequential_pcap_replay_does_not_buffer_the_full_capture(tmp_path):
    pcap_path = write_sample_pcap(tmp_path / "large.pcap", sessions=50)

    source = PcapReplaySource(str(pcap_path), order="sequential")
    source.prepare()

    assert source.row_total is None
    assert source.packets_read < 50
    assert source._flows == []
    source.close()


def test_csv_source_replays_rows_with_labels(tmp_path, trained_bundle):
    csv_path = tmp_path / "sample.csv"
    trained_bundle["frame"].to_csv(csv_path, index=False)

    source = CsvReplaySource(str(csv_path))
    source.prepare()
    assert source.row_total == len(trained_bundle["frame"])

    event = source.next_event()
    assert event["actual"] in {"Normal", "Attack"}
    assert event["source_ip"].startswith("10.")
    assert event["end_reason"] == "replay"


@pytest.mark.parametrize(
    "labels",
    [[0, 1], [0.0, 1.0], [" normal ", " attack "], ["BENIGN", "malicious"]],
)
def test_csv_source_normalizes_supported_labels(tmp_path, labels):
    csv_path = tmp_path / "labels.csv"
    pd.DataFrame({"value": [10, 20], "label": labels}).to_csv(csv_path, index=False)
    source = CsvReplaySource(csv_path)

    source.prepare()

    assert [source.next_event()["actual"] for _ in labels] == ["Normal", "Attack"]


@pytest.mark.parametrize("label", ["Normal", "Attack", 0, 1])
def test_csv_source_can_replay_a_single_known_class(tmp_path, label):
    csv_path = tmp_path / "single_class.csv"
    pd.DataFrame({"value": [10], "label": [label]}).to_csv(csv_path, index=False)
    source = CsvReplaySource(csv_path)
    source.prepare()
    assert source.next_event()["actual"] == ("Normal" if label in {"Normal", 0} else "Attack")


@pytest.mark.parametrize("labels", [["safe", "danger"], ["Normal", None]])
def test_csv_source_rejects_unusable_ground_truth(tmp_path, labels):
    csv_path = tmp_path / "ambiguous.csv"
    pd.DataFrame({"value": [10, 20], "label": labels}).to_csv(csv_path, index=False)
    with pytest.raises(TrafficSourceError, match="label|Normal"):
        CsvReplaySource(csv_path).prepare()


def test_live_capture_availability_reports_a_reason_when_unavailable():
    available, reason = live_capture_available()
    assert isinstance(available, bool)
    if not available:
        assert reason


def test_windows_without_pcap_provider_disables_live_capture(monkeypatch):
    from scapy.config import conf

    monkeypatch.setattr(traffic_sources.os, "name", "nt")
    monkeypatch.setattr(conf, "use_pcap", False)

    available, reason = live_capture_available()

    assert available is False
    assert "Npcap" in reason


def wait_for(condition, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if condition():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture()
def deployed_stack(monkeypatch, trained_bundle):
    from services import database_service as db
    from services.deployment_service import deploy_model

    with db.get_connection() as connection:
        row = connection.execute(
            "SELECT model_id FROM detection_model WHERE run_id = ? AND model_name = ?",
            (trained_bundle["run_id"], "Stacking Ensemble"),
        ).fetchone()
    deploy_model(row["model_id"], 1)
    monkeypatch.setitem(monitor.SPEED_CHOICES, "fast", 0.001)
    return trained_bundle


def test_monitor_replays_a_pcap_end_to_end(monkeypatch, tmp_path, deployed_stack):
    write_sample_pcap(tmp_path / "office.pcap", sessions=3)
    # live_monitor_service now resolves the capture folder from configuration at
    # use time, so there is no module-level CAPTURE_FOLDER left to patch.
    monkeypatch.setenv("ALGOGUARD_CAPTURE_FOLDER", str(tmp_path))
    config.reset_config_cache()

    monitor.start_session(
        1, source_type="pcap", capture_file="office.pcap", speed="fast", persist="none"
    )
    assert wait_for(lambda: monitor.get_status()["session"]["state"] == "completed")

    status = monitor.get_status()
    session = status["session"]
    assert session["source_type"] == "pcap"
    assert session["row_total"] == 4
    assert session["totals"]["flows"] == 4
    assert session["totals"]["labelled"] == 0
    assert session["totals"]["mismatches"] == 0

    events = status["events"]
    assert all(event["actual"] is None for event in events)
    assert all(event["match"] is None for event in events)
    assert any(event["destination_port"] == 80 for event in events)
    assert all(event["prediction"] in {"Normal", "Attack"} for event in events)


@pytest.mark.parametrize("source_type", ["pcap", "live"])
def test_packet_monitor_rejects_incompatible_model_before_capture(
    monkeypatch, deployed_stack, source_type
):
    artifact, deployment = load_active_artifact()
    artifact["feature_columns"] = [*artifact["feature_columns"], "custom_feature"]
    artifact["feature_defaults"]["custom_feature"] = 42
    monkeypatch.setattr(monitor, "load_active_artifact", lambda: (artifact, deployment))
    source = (
        PcapReplaySource("unused.pcap")
        if source_type == "pcap"
        else traffic_sources.LiveCaptureSource(None)
    )
    prepare = Mock()
    close = Mock()
    monkeypatch.setattr(source, "prepare", prepare)
    monkeypatch.setattr(source, "close", close)
    monkeypatch.setattr(monitor, "_make_source", lambda *args, **kwargs: source)
    session = monitor._new_session(
        source_type, None, None, "fast", "sequential", "none", 1
    )

    monitor._worker(session, threading.Event(), threading.Event())

    assert session["state"] == "error"
    assert "unavailable from packet capture: custom_feature" in session["error_message"]
    assert session["totals"]["flows"] == 0
    prepare.assert_not_called()
    close.assert_called_once()


def test_packet_fixtures_do_not_resolve_network_addresses(monkeypatch, tmp_path):
    def reject_lookup(*args, **kwargs):
        raise AssertionError("Packet fixtures must not look up real network addresses")

    monkeypatch.setattr(scapy_all.conf.neighbor, "resolve", reject_lookup)
    write_sample_pcap(tmp_path / "offline.pcap", sessions=1)


@pytest.mark.parametrize(
    "failure_stage", ["capture_record", "start_log", "store_flow", "finish_log"]
)
def test_capture_is_closed_even_when_database_and_error_logging_fail(
    monkeypatch, deployed_stack, failure_stage
):
    artifact, _ = load_active_artifact()
    source = Mock(columns=artifact["feature_columns"], row_total=None, paced=False)
    source.stats.return_value = {"packets": 2, "dropped": 0, "flows": 1}
    source.next_event.side_effect = [
        {
            "record": dict(artifact["feature_defaults"]), "actual": None,
            "source_ip": "192.0.2.1", "destination_ip": "198.51.100.1",
            "source_port": 12345, "destination_port": 80, "protocol": "tcp",
            "flow_last_ts": None,
        },
        StopIteration,
    ]
    monkeypatch.setattr(monitor, "_make_source", lambda *args, **kwargs: source)

    def database_failure(*args, **kwargs):
        raise sqlite3.DatabaseError("simulated database failure")

    def log_event(admin, module, action, *args, **kwargs):
        if action == "monitor_failed" or (
            failure_stage == "start_log" and action == "monitor_started"
        ) or (failure_stage == "finish_log" and action == "monitor_completed"):
            database_failure()

    monkeypatch.setattr(monitor, "log_system_event", log_event)
    monkeypatch.setattr(
        monitor, "insert_capture_session",
        database_failure if failure_stage == "capture_record" else lambda *args: 42,
    )
    monkeypatch.setattr(monitor, "store_classified_flow", database_failure)
    finalized = []

    def finalize(*args):
        source.close.assert_called_once()
        finalized.append(args)

    monkeypatch.setattr(monitor, "finalize_capture_session", finalize)
    session = monitor._new_session(
        "live", "test interface", None, "fast", "sequential",
        "all" if failure_stage == "store_flow" else "none", 1,
    )

    monitor._worker(session, threading.Event(), threading.Event())

    assert session["state"] == "error"
    assert "database failure" in session["error_message"]
    source.close.assert_called_once()
    assert finalized == ([] if failure_stage == "capture_record" else [(42, 2, 0, 1, "error")])


def test_monitor_rejects_bad_capture_selections(monkeypatch, tmp_path):
    monkeypatch.setenv("ALGOGUARD_CAPTURE_FOLDER", str(tmp_path))
    config.reset_config_cache()
    with pytest.raises(monitor.LiveMonitorError, match="captures folder"):
        monitor.start_session(1, source_type="pcap", capture_file="../evil.pcap")
    with pytest.raises(monitor.LiveMonitorError, match="recordings"):
        monitor.start_session(1, source_type="pcap", capture_file="notes.txt")
    with pytest.raises(monitor.LiveMonitorError, match="missing"):
        monitor.start_session(1, source_type="pcap", capture_file="ghost.pcap")
    with pytest.raises(monitor.LiveMonitorError, match="source type"):
        monitor.start_session(1, source_type="telepathy")


def test_capture_filter_excludes_the_application_port(monkeypatch):
    from services.traffic_source_service import algoguard_port, build_capture_filter

    monkeypatch.setenv("ALGOGUARD_PORT", "5000")
    assert algoguard_port() == 5000
    assert build_capture_filter({5000}) == "ip and (tcp or udp) and not port 5000"
    assert build_capture_filter({}) == "ip and (tcp or udp)"

    monkeypatch.setenv("ALGOGUARD_PORT", "not-a-port")
    assert algoguard_port() == 5000  # falls back instead of crashing capture


def test_live_source_defaults_to_excluding_algoguards_own_traffic(monkeypatch):
    from services.traffic_source_service import LiveCaptureSource

    monkeypatch.setenv("ALGOGUARD_PORT", "5077")
    source = LiveCaptureSource("lo")
    assert source.exclude_ports == {5077}
    assert "not port 5077" in source.bpf_filter


def test_own_traffic_is_dropped_even_when_the_bpf_filter_did_not_apply():
    """The Python-side guard is what protects unfiltered fallback captures."""
    from scapy.all import IP, TCP, Ether

    from services.traffic_source_service import LiveCaptureSource

    source = LiveCaptureSource("lo", exclude_ports={5000})

    own = Ether() / IP(src="127.0.0.1", dst="127.0.0.1") / TCP(sport=44444, dport=5000, flags="S")
    other = Ether() / IP(src="10.0.0.1", dst="10.0.0.2") / TCP(sport=44444, dport=80, flags="S")
    own.time = other.time = 1_700_000_000.0

    source._on_packet(own)
    assert source.next_event(timeout=0.1) is None
    assert source.packets_excluded == 1
    assert source.packets_captured == 0

    source._on_packet(other)
    source.next_event(timeout=0.1)
    assert source.packets_captured == 1
    assert source.stats()["excluded"] == 1


def test_monitor_options_advertise_all_source_types():
    options = monitor.get_options()
    values = {item["value"] for item in options["sources"]}
    assert values == {"csv", "pcap", "live"}
    assert "live_capture" in options
    assert isinstance(options["captures"], list)


@pytest.mark.parametrize("exception", [None, OSError("capture device disconnected")])
def test_live_source_reports_a_capture_thread_that_exits_after_startup(exception):
    source = traffic_sources.LiveCaptureSource("test interface")
    source._sniffer = Mock(exception=exception)
    source._sniffer.thread.is_alive.return_value = False

    with pytest.raises(TrafficSourceError, match="capture.*stopped"):
        source.next_event(timeout=0)


@pytest.mark.parametrize("ignored_type", ["own_traffic", "non_ip"])
def test_ignored_packets_still_expire_idle_flows(monkeypatch, ignored_type):
    source = traffic_sources.LiveCaptureSource("test interface", exclude_ports={5000})
    packet = scapy_all.IP(src="192.0.2.1", dst="192.0.2.2") / scapy_all.UDP(sport=40000, dport=53)
    packet.time = 1000.0
    monkeypatch.setattr(traffic_sources.time, "time", lambda: 1000.0)
    source._on_packet(packet)
    assert source.next_event(timeout=0) is None

    ignored = (
        scapy_all.IP(src="192.0.2.1", dst="192.0.2.2") / scapy_all.TCP(dport=5000)
        if ignored_type == "own_traffic" else scapy_all.ARP()
    )
    ignored.time = 1020.0
    source._on_packet(ignored)
    # Keep the queue nonempty: capture may constantly receive excluded traffic.
    source._on_packet(ignored)
    event = source.next_event(timeout=0)
    if event is None:
        event = source.next_event(timeout=0)

    assert event is not None
    assert event["end_reason"] == "idle_timeout"
    assert event["destination_port"] == 53
    assert source.packets_captured == 1
