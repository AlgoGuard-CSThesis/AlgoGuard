"""Unit tests for packet-to-flow aggregation and UNSW-style feature extraction."""

import pytest

from services.flow_tracker_service import (
    ACTIVE_TIMEOUT_SECONDS,
    CONNECTION_WINDOW,
    FLOW_FEATURE_COLUMNS,
    IDLE_TIMEOUT_SECONDS,
    FlowTracker,
    PacketInfo,
    compute_features,
)

CLIENT = "192.168.1.10"
SERVER = "93.184.216.34"


def tcp_packet(ts, src, dst, sport, dport, length=60, ttl=64, flags="A"):
    return PacketInfo(
        ts=ts,
        src_ip=src,
        dst_ip=dst,
        src_port=sport,
        dst_port=dport,
        proto="tcp",
        length=length,
        ttl=ttl,
        tcp_flags=flags,
    )


def udp_packet(ts, src, dst, sport, dport, length=80, ttl=64):
    return PacketInfo(
        ts=ts,
        src_ip=src,
        dst_ip=dst,
        src_port=sport,
        dst_port=dport,
        proto="udp",
        length=length,
        ttl=ttl,
    )


def http_session(start=1000.0):
    """A complete TCP session to port 80: handshake, data, full teardown."""
    c2s = {"src": CLIENT, "dst": SERVER, "sport": 52100, "dport": 80, "ttl": 62}
    s2c = {"src": SERVER, "dst": CLIENT, "sport": 80, "dport": 52100, "ttl": 254}
    return [
        tcp_packet(start + 0.000, flags="S", length=60, **c2s),
        tcp_packet(start + 0.020, flags="SA", length=60, **s2c),
        tcp_packet(start + 0.040, flags="A", length=52, **c2s),
        tcp_packet(start + 0.060, flags="PA", length=350, **c2s),
        tcp_packet(start + 0.120, flags="PA", length=1200, **s2c),
        tcp_packet(start + 0.140, flags="A", length=52, **c2s),
        tcp_packet(start + 0.200, flags="FA", length=52, **c2s),
        tcp_packet(start + 0.220, flags="FA", length=52, **s2c),
        tcp_packet(start + 0.240, flags="A", length=52, **c2s),
    ]


def run_all(tracker, packets):
    emitted = []
    for packet in packets:
        emitted.extend(tracker.add_packet(packet))
    return emitted


def test_complete_tcp_session_emits_one_finished_flow():
    tracker = FlowTracker()
    flows = run_all(tracker, http_session())
    flows.extend(tracker.flush())

    assert len(flows) == 1
    flow = flows[0]
    features = flow["features"]

    assert flow["source_ip"] == CLIENT
    assert flow["destination_ip"] == SERVER
    assert flow["source_port"] == 52100
    assert flow["destination_port"] == 80
    assert flow["end_reason"] == "fin"

    assert features["proto"] == "tcp"
    assert features["service"] == "http"
    assert features["state"] == "FIN"
    # The trailing ACK of the teardown is swallowed, not a second flow.
    assert features["spkts"] == 5
    assert features["dpkts"] == 3
    assert features["sbytes"] == 60 + 52 + 350 + 52 + 52
    assert features["dbytes"] == 60 + 1200 + 52
    assert features["dur"] == pytest.approx(0.220, abs=1e-6)
    assert features["rate"] == pytest.approx((8 - 1) / 0.220, rel=1e-4)
    assert features["sload"] == pytest.approx(features["sbytes"] * 8 / 0.220, rel=1e-4)
    assert features["sinpkt"] > 0
    assert features["dinpkt"] > 0


def test_reset_ends_a_flow_with_rst_state():
    tracker = FlowTracker()
    flows = run_all(
        tracker,
        [
            tcp_packet(1.0, CLIENT, SERVER, 52101, 443, flags="S"),
            tcp_packet(1.1, SERVER, CLIENT, 443, 52101, flags="R"),
        ],
    )
    assert len(flows) == 1
    assert flows[0]["features"]["state"] == "RST"
    assert flows[0]["features"]["service"] == "ssl"


def test_udp_exchange_is_con_and_one_sided_udp_is_int():
    tracker = FlowTracker()
    flows = run_all(
        tracker,
        [
            udp_packet(1.0, CLIENT, SERVER, 40000, 53),
            udp_packet(1.1, SERVER, CLIENT, 53, 40000),
            udp_packet(2.0, CLIENT, "10.9.9.9", 40001, 9999),
        ],
    )
    flows.extend(tracker.flush())

    by_port = {flow["destination_port"]: flow for flow in flows}
    assert by_port[53]["features"]["state"] == "CON"
    assert by_port[53]["features"]["service"] == "dns"
    assert by_port[9999]["features"]["state"] == "INT"
    assert by_port[9999]["features"]["service"] == "-"


def test_idle_flows_expire_after_the_idle_timeout():
    tracker = FlowTracker()
    tracker.add_packet(udp_packet(1.0, CLIENT, SERVER, 40000, 53))
    late = tracker.add_packet(
        udp_packet(1.0 + IDLE_TIMEOUT_SECONDS + 1.0, CLIENT, "10.1.1.1", 40002, 53)
    )
    assert len(late) == 1
    assert late[0]["end_reason"] == "idle_timeout"
    assert late[0]["destination_ip"] == SERVER


def test_long_lived_flows_are_sliced_by_the_active_timeout():
    tracker = FlowTracker()
    emitted = []
    # Steady keepalive traffic: never idle, but alive past the active timeout.
    ts = 1.0
    while ts < ACTIVE_TIMEOUT_SECONDS + 12.0:
        emitted.extend(tracker.add_packet(tcp_packet(ts, CLIENT, SERVER, 52102, 80, flags="A")))
        ts += 10.0
    assert any(flow["end_reason"] == "active_timeout" for flow in emitted)


def test_idle_gap_on_the_same_five_tuple_splits_into_two_flows():
    tracker = FlowTracker()
    emitted = tracker.add_packet(udp_packet(1000.0, CLIENT, SERVER, 40000, 53))
    emitted.extend(
        tracker.add_packet(
            udp_packet(1000.0 + IDLE_TIMEOUT_SECONDS + 5.0, CLIENT, SERVER, 40000, 53)
        )
    )
    emitted.extend(tracker.flush())

    assert len(emitted) == 2
    assert emitted[0]["end_reason"] == "idle_timeout"
    assert emitted[0]["features"]["spkts"] == 1
    assert emitted[0]["features"]["dur"] == 0.0
    assert emitted[1]["features"]["spkts"] == 1
    assert emitted[1]["features"]["dur"] == 0.0


def test_active_timeout_slice_keeps_the_original_orientation():
    tracker = FlowTracker()
    emitted = []
    c2s = {"src": CLIENT, "dst": SERVER, "sport": 52104, "dport": 80, "ttl": 62}
    s2c = {"src": SERVER, "dst": CLIENT, "sport": 80, "dport": 52104, "ttl": 254}
    ts = 0.0
    while ts < ACTIVE_TIMEOUT_SECONDS:
        emitted.extend(tracker.add_packet(tcp_packet(ts, flags="A", length=52, **c2s)))
        emitted.extend(tracker.add_packet(tcp_packet(ts + 0.01, flags="A", length=1200, **s2c)))
        ts += 10.0
    # The packet that crosses the active timeout travels server-to-client.
    emitted.extend(
        tracker.add_packet(tcp_packet(ACTIVE_TIMEOUT_SECONDS + 5.0, flags="A", length=1200, **s2c))
    )
    emitted.extend(tracker.flush())

    assert emitted[0]["end_reason"] == "active_timeout"
    new_slice = emitted[1]
    assert new_slice["source_ip"] == CLIENT
    assert new_slice["source_port"] == 52104
    assert new_slice["features"]["spkts"] == 0
    assert new_slice["features"]["dpkts"] == 1
    assert new_slice["features"]["dbytes"] == 1200
    assert new_slice["features"]["sbytes"] == 0


def test_completed_connections_do_not_accumulate_drain_state():
    tracker = FlowTracker()
    for index in range(100):
        base = 1000.0 + index * 5.0
        sport = 52200 + index
        c2s = {"src": CLIENT, "dst": SERVER, "sport": sport, "dport": 80, "ttl": 62}
        s2c = {"src": SERVER, "dst": CLIENT, "sport": 80, "dport": sport, "ttl": 254}
        tracker.add_packet(tcp_packet(base + 0.00, flags="S", **c2s))
        tracker.add_packet(tcp_packet(base + 0.01, flags="SA", **s2c))
        tcp_teardown = [
            tcp_packet(base + 0.02, flags="FA", **c2s),
            tcp_packet(base + 0.03, flags="FA", **s2c),
            tcp_packet(base + 0.04, flags="A", **c2s),
        ]
        for packet in tcp_teardown:
            tracker.add_packet(packet)

    assert tracker.active_flows == 0
    # Idle sweeps clear expired drain markers; at most the newest may remain.
    assert len(tracker._draining) <= 1
    tracker.flush()
    assert len(tracker._draining) == 0


def test_direction_is_assigned_by_first_packet():
    tracker = FlowTracker()
    tracker.add_packet(udp_packet(1.0, SERVER, CLIENT, 53, 40010))
    tracker.add_packet(udp_packet(1.1, CLIENT, SERVER, 40010, 53))
    flows = tracker.flush()
    assert len(flows) == 1
    assert flows[0]["source_ip"] == SERVER
    assert flows[0]["features"]["spkts"] == 1
    assert flows[0]["features"]["dpkts"] == 1


def test_syn_without_answer_is_req():
    tracker = FlowTracker()
    tracker.add_packet(tcp_packet(1.0, CLIENT, SERVER, 52103, 80, flags="S"))
    flows = tracker.flush()
    assert flows[0]["features"]["state"] == "REQ"


def test_features_are_the_sixteen_deployed_columns():
    tracker = FlowTracker()
    tracker.add_packet(udp_packet(1.0, CLIENT, SERVER, 40000, 53))
    flow = tracker.flush()[0]
    assert set(flow["features"]) == set(FLOW_FEATURE_COLUMNS)
    assert set(FLOW_FEATURE_COLUMNS) == {
        "dur",
        "proto",
        "service",
        "state",
        "spkts",
        "dpkts",
        "sbytes",
        "dbytes",
        "rate",
        "sload",
        "dload",
        "sinpkt",
        "dinpkt",
        "ct_dst_src_ltm",
        "ct_dst_ltm",
        "ct_srv_dst",
    }


def _reset_flow(tracker, ts, src, dst, sport, dport):
    """One TCP connection that ends at once with RST, so it is emitted immediately."""
    return tracker.add_packet(tcp_packet(ts, src, dst, sport, dport, flags="R"))


def test_connection_counts_follow_destination_and_service_and_include_this_flow():
    tracker = FlowTracker()
    other = "10.0.0.7"
    first = _reset_flow(tracker, 1.0, CLIENT, SERVER, 50000, 80)[0]["features"]
    assert (first["ct_dst_src_ltm"], first["ct_dst_ltm"], first["ct_srv_dst"]) == (1, 1, 1)

    _reset_flow(tracker, 1.1, CLIENT, SERVER, 50001, 443)  # same pair, other service
    _reset_flow(tracker, 1.2, other, SERVER, 50002, 80)  # other source, same service
    last = _reset_flow(tracker, 1.3, CLIENT, SERVER, 50003, 80)[0]["features"]
    assert last["ct_dst_src_ltm"] == 3  # CLIENT -> SERVER three times
    assert last["ct_dst_ltm"] == 4  # every connection went to SERVER
    assert last["ct_srv_dst"] == 3  # http to SERVER three times

    elsewhere = _reset_flow(tracker, 1.4, CLIENT, other, 50004, 80)[0]["features"]
    assert (elsewhere["ct_dst_src_ltm"], elsewhere["ct_dst_ltm"], elsewhere["ct_srv_dst"]) == (
        1, 1, 1)


def test_connection_counts_look_back_one_window_only():
    tracker = FlowTracker()
    flows = [
        _reset_flow(tracker, 1.0 + index * 0.01, CLIENT, SERVER, 40000 + index, 80)[0]
        for index in range(CONNECTION_WINDOW + 20)
    ]
    features = flows[-1]["features"]
    assert features["ct_dst_ltm"] == CONNECTION_WINDOW
    assert features["ct_dst_src_ltm"] == CONNECTION_WINDOW


def test_flushed_flows_get_connection_counts():
    tracker = FlowTracker()
    tracker.add_packet(udp_packet(1.0, CLIENT, SERVER, 40000, 53))
    tracker.add_packet(udp_packet(1.1, CLIENT, SERVER, 40001, 53))
    flows = tracker.flush()
    assert [flow["features"]["ct_srv_dst"] for flow in flows] == [1, 2]


def test_zero_duration_flow_has_safe_rates():
    tracker = FlowTracker()
    tracker.add_packet(udp_packet(5.0, CLIENT, SERVER, 40000, 161))
    features = compute_features(next(iter(tracker._flows.values())))
    assert features["dur"] == 0.0
    assert features["rate"] == 0.0
    assert features["sload"] == 0.0
    assert features["sinpkt"] == 0.0
