"""Unit tests for packet-to-flow aggregation and UNSW-style feature extraction."""

import pytest

from services.flow_tracker_service import (
    ACTIVE_TIMEOUT_SECONDS,
    CLOSE_HOLD_SECONDS,
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
    # The final ACK arrives within the closing hold, so it is counted, as Argus counts it.
    assert features["spkts"] == 6
    assert features["dpkts"] == 3
    assert features["sbytes"] == 60 + 52 + 350 + 52 + 52 + 52
    assert features["dbytes"] == 60 + 1200 + 52
    assert features["dur"] == pytest.approx(0.240, abs=1e-6)
    assert features["rate"] == pytest.approx((9 - 1) / 0.240, rel=1e-4)
    # UNSW-NB15's load leaves out the first packet's share of the bytes.
    assert features["sload"] == pytest.approx(features["sbytes"] * 8 * 5 / 6 / 0.240, rel=1e-4)
    assert features["dload"] == pytest.approx(features["dbytes"] * 8 * 2 / 3 / 0.240, rel=1e-4)
    assert features["sinpkt"] > 0
    assert features["dinpkt"] > 0


def test_a_one_packet_direction_has_no_load():
    tracker = FlowTracker()
    run_all(
        tracker,
        [
            udp_packet(1.0, CLIENT, SERVER, 40000, 53, length=70),
            udp_packet(1.5, SERVER, CLIENT, 53, 40000, length=120),
            udp_packet(2.0, CLIENT, SERVER, 40000, 53, length=70),
        ],
    )
    features = tracker.flush()[0]["features"]
    assert features["sload"] == pytest.approx(140 * 8 * 1 / 2 / 1.0, rel=1e-4)
    assert features["dload"] == 0.0


def test_a_reset_after_a_finish_reports_rst():
    tracker = FlowTracker()
    c2s = {"src": CLIENT, "dst": SERVER, "sport": 52102, "dport": 443}
    s2c = {"src": SERVER, "dst": CLIENT, "sport": 443, "dport": 52102}
    flows = run_all(
        tracker,
        [
            tcp_packet(1.0, flags="S", **c2s),
            tcp_packet(1.1, flags="SA", **s2c),
            tcp_packet(1.2, flags="FA", **c2s),
            tcp_packet(1.3, flags="R", **s2c),
        ],
    )
    flows.extend(tracker.flush())
    assert [flow["features"]["state"] for flow in flows] == ["RST"]


def test_a_reset_trailing_a_closed_flow_is_not_a_new_flow():
    tracker = FlowTracker()
    flows = run_all(
        tracker,
        [
            tcp_packet(1.0, CLIENT, SERVER, 52103, 443, flags="S"),
            tcp_packet(1.1, SERVER, CLIENT, 443, 52103, flags="R"),
            tcp_packet(1.2, CLIENT, SERVER, 52103, 443, flags="R"),
        ],
    )
    flows.extend(tracker.flush())
    assert len(flows) == 1


def test_a_large_send_counts_as_its_wire_segments():
    tracker = FlowTracker()
    flows = run_all(
        tracker,
        [
            tcp_packet(1.0, CLIENT, SERVER, 52104, 443, flags="S", length=52),
            PacketInfo(ts=1.1, src_ip=CLIENT, dst_ip=SERVER, src_port=52104, dst_port=443,
                       proto="tcp", length=2980, ttl=128, tcp_flags="PA", segments=2),
            tcp_packet(1.3, CLIENT, SERVER, 52104, 443, flags="R", length=40),
        ],
    )
    flows.extend(tracker.flush())
    features = flows[0]["features"]
    assert features["spkts"] == 4
    assert features["sbytes"] == 52 + 2980 + 40
    # Gaps 100 ms, 0 (back-to-back segment) and 200 ms.
    assert features["sinpkt"] == pytest.approx(100.0, rel=1e-4)


@pytest.mark.parametrize("ip_len", [0, None])
def test_scapy_packets_bigger_than_the_mtu_become_mtu_sized_segments(ip_len):
    """An outgoing large send (IP length 0) or merged received segments (real length)."""
    from scapy.layers.inet import IP, TCP
    from scapy.layers.l2 import Ether
    from scapy.packet import Raw

    from services.flow_tracker_service import packet_info_from_scapy

    built = Ether() / IP(src=CLIENT, dst=SERVER, len=ip_len) / TCP(
        sport=52105, dport=443, flags="PA") / Raw(b"x" * 2900)
    packet = Ether(bytes(built))
    packet.time = 5.0

    info = packet_info_from_scapy(packet)

    assert info is not None
    assert info.segments == 2  # 2900 payload bytes in 1460-byte segments
    assert info.length == 2900 + 2 * 40
    assert info.tcp_flags == "PA"


def test_a_ping_is_an_echo_flow_like_in_unsw_nb15():
    def icmp(ts, src, dst, icmp_type):
        return PacketInfo(ts=ts, src_ip=src, dst_ip=dst, src_port=0, dst_port=0, proto="icmp",
                          length=60, ttl=64, icmp_type=icmp_type)

    tracker = FlowTracker()
    run_all(tracker, [icmp(1.0, CLIENT, SERVER, 8), icmp(1.02, SERVER, CLIENT, 0)])
    run_all(tracker, [icmp(2.0, SERVER, "192.168.1.11", 3)])  # unreachable, not an echo

    states = sorted(flow["features"]["state"] for flow in tracker.flush())
    assert states == ["ECO", "INT"]


def test_scapy_packets_count_ip_layer_bytes():
    from scapy.layers.inet import IP, UDP
    from scapy.layers.l2 import Ether
    from scapy.packet import Raw

    from services.flow_tracker_service import packet_info_from_scapy

    packet = Ether(bytes(Ether() / IP(src=CLIENT, dst=SERVER) / UDP(sport=40000, dport=53)
                         / Raw(b"q" * 30)))
    packet.time = 5.0

    info = packet_info_from_scapy(packet)

    assert info is not None
    assert (info.length, info.segments) == (20 + 8 + 30, 1)


def test_reset_ends_a_flow_with_rst_state():
    tracker = FlowTracker()
    flows = run_all(
        tracker,
        [
            tcp_packet(1.0, CLIENT, SERVER, 52101, 443, flags="S"),
            tcp_packet(1.1, SERVER, CLIENT, 443, 52101, flags="R"),
        ],
    )
    assert flows == []  # held for packets still in flight
    flows = tracker.close_due(1.1 + CLOSE_HOLD_SECONDS + 0.001)
    assert len(flows) == 1
    assert flows[0]["features"]["state"] == "RST"
    # HTTPS reports "-": the dataset's "ssl" occurs only in attack rows (DEF-19).
    assert flows[0]["features"]["service"] == "-"


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
    """One TCP connection that ends at once with RST, taken out after its closing hold."""
    tracker.add_packet(tcp_packet(ts, src, dst, sport, dport, flags="R"))
    return tracker.close_due(ts + 1.0)


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


def test_the_closing_hold_counts_packets_in_flight_and_then_releases_the_flow():
    """Argus counts the server's last data and reset that arrive just after the client's reset."""
    tracker = FlowTracker()
    c2s = {"src": CLIENT, "dst": SERVER, "sport": 52110, "dport": 443}
    s2c = {"src": SERVER, "dst": CLIENT, "sport": 443, "dport": 52110}
    run_all(tracker, [
        tcp_packet(1.0, flags="S", **c2s),
        tcp_packet(1.02, flags="SA", **s2c),
        tcp_packet(1.04, flags="R", **c2s),
        tcp_packet(1.06, flags="PA", length=500, **s2c),  # in flight, inside the hold
    ])
    # A later packet of another connection, after the hold, releases the flow.
    flows = tracker.add_packet(udp_packet(1.2, CLIENT, SERVER, 40000, 53))
    assert len(flows) == 1
    features = flows[0]["features"]
    assert (features["spkts"], features["dpkts"], features["dbytes"]) == (2, 2, 60 + 500)
    assert features["dur"] == pytest.approx(0.06, abs=1e-6)
    # A trailing reset after the hold is swallowed, not a new flow.
    assert tracker.add_packet(tcp_packet(1.3, flags="R", **s2c)) == []
    assert len(tracker.flush()) == 1  # only the DNS flow


def test_a_new_connection_on_the_same_ports_ends_the_hold_at_once():
    tracker = FlowTracker()
    c2s = {"src": CLIENT, "dst": SERVER, "sport": 52111, "dport": 443}
    run_all(tracker, [tcp_packet(1.0, flags="S", **c2s), tcp_packet(1.01, flags="R", **c2s)])
    flows = tracker.add_packet(tcp_packet(1.02, flags="S", **c2s))
    assert [flow["end_reason"] for flow in flows] == ["rst"]
    assert tracker.active_flows == 1
