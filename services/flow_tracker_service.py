"""Packet-to-flow aggregation for AlgoGuard.

Turns individual data packets into bidirectional network flows and computes the
same 16 UNSW-NB15-style features the deployed model was trained on: ``dur``,
``proto``, ``service``, ``state``, ``spkts``, ``dpkts``, ``sbytes``, ``dbytes``,
``rate``, ``sload``, ``dload``, ``sinpkt`` and ``dinpkt`` describe the flow
itself; ``ct_dst_src_ltm``, ``ct_dst_ltm`` and ``ct_srv_dst`` count, among the
last 100 finished connections including this one, those with the same source and
destination address, the same destination address, and the same service and
destination address. Only destination-keyed counts are used: on a single
capturing PC almost every connection shares the source address, so source-keyed
counts would sit at the window size and mean nothing.

UNSW-NB15's TTL features (``sttl``, ``dttl``) are not used. In the dataset they
mostly identify the lab's own hosts; a Windows PC sends with TTL 128, a value the
dataset never contains, and models trained with TTLs flagged most benign live
traffic as attacks (test-results/def_fixes_20261023/ttl_experiment.json).

The tracker is deliberately independent of any capture library: it consumes
lightweight :class:`PacketInfo` values, so unit tests can drive it with
hand-made packets and both live capture and PCAP replay share one code path.
Time is always the packet's own timestamp, never the wall clock, which keeps
replayed captures byte-for-byte reproducible.

Approximations versus the original UNSW-NB15 generation (Argus/Bro) are
documented inline; ``service`` and ``state`` are the two features where the
approximation is largest. The research workspace quantifies that gap before
live verdicts are trusted.
"""

from collections import deque
from dataclasses import dataclass, field

IDLE_TIMEOUT_SECONDS = 15.0
ACTIVE_TIMEOUT_SECONDS = 120.0
SWEEP_INTERVAL_SECONDS = 1.0
# A closed TCP flow is held this long so packets already in flight are counted, as Argus counts
# them (18 of 20 such tails arrived within 50 ms in the Stage 6A.1 capture).
CLOSE_HOLD_SECONDS = 0.05
# Trailing ACKs and resets this long after the hold are swallowed rather than becoming new flows.
DRAIN_SECONDS = 2.0
# UNSW-NB15's connection-history features look at the last 100 connections.
CONNECTION_WINDOW = 100
# Largest IP packet an Ethernet link carries; a bigger captured TCP packet is a
# large-send buffer the network card splits into segments of this size.
ETHERNET_MTU = 1500

CONNECTION_COUNT_COLUMNS = ("ct_dst_src_ltm", "ct_dst_ltm", "ct_srv_dst")
FLOW_FEATURE_COLUMNS = (
    "dur", "proto", "service", "state", "spkts", "dpkts", "sbytes", "dbytes",
    "rate", "sload", "dload", "sinpkt", "dinpkt",
) + CONNECTION_COUNT_COLUMNS

# UNSW-NB15 uses Bro-derived service names; this port map covers the services
# present in the bundled datasets. Unknown ports report "-", exactly as the
# dataset does for unidentified traffic.
SERVICE_PORTS = {
    20: "ftp-data",
    21: "ftp",
    22: "ssh",
    25: "smtp",
    53: "dns",
    67: "dhcp",
    68: "dhcp",
    80: "http",
    110: "pop3",
    161: "snmp",
    443: "ssl",
    587: "smtp",
    1812: "radius",
    6667: "irc",
    8080: "http",
}

PROTO_NAMES = {1: "icmp", 6: "tcp", 17: "udp"}


@dataclass
class PacketInfo:
    """The minimal per-packet facts the tracker needs, capture-library agnostic."""

    ts: float
    src_ip: str
    dst_ip: str
    src_port: int
    dst_port: int
    proto: str
    length: int
    ttl: int
    tcp_flags: str = ""
    # Packets this capture becomes on the wire (more than one for a large send).
    segments: int = 1
    icmp_type: int | None = None


@dataclass
class _FlowState:
    """Accumulators for one bidirectional flow keyed by its first packet's 5-tuple."""

    src_ip: str
    dst_ip: str
    src_port: int
    dst_port: int
    proto: str
    first_ts: float
    last_ts: float
    spkts: int = 0
    dpkts: int = 0
    sbytes: int = 0
    dbytes: int = 0
    src_intervals: list = field(default_factory=list)
    dst_intervals: list = field(default_factory=list)
    last_src_ts: float | None = None
    last_dst_ts: float | None = None
    saw_syn: bool = False
    saw_fin: bool = False
    saw_rst: bool = False
    saw_echo: bool = False
    fin_forward: bool = False
    fin_reverse: bool = False


def packet_info_from_scapy(packet):
    """Convert a Scapy packet to :class:`PacketInfo`, or ``None`` if not IP traffic.

    Bytes are counted at the IP layer, as UNSW-NB15 counts them: 15% of its TCP
    rows average fewer bytes per packet than an Ethernet header would allow. Windows
    captures packets on either side of the network card's segmentation: a large
    send (IP total length 0) or several received segments merged into one TCP
    packet bigger than the MTU. It is counted as the MTU-sized segments a network
    tap such as UNSW-NB15's saw (Stage 6A.1 feature-fidelity comparison).
    """
    ip = packet.getlayer("IP")
    if ip is None:
        return None
    ip_bytes = int(ip.len) if ip.len else len(bytes(ip))

    proto = PROTO_NAMES.get(int(ip.proto), str(int(ip.proto)))
    src_port = dst_port = 0
    tcp_flags = ""
    segments = 1
    length = ip_bytes
    layer4 = packet.getlayer("TCP") or packet.getlayer("UDP")
    if layer4 is not None:
        src_port = int(layer4.sport)
        dst_port = int(layer4.dport)
        if proto == "tcp":
            tcp_flags = str(layer4.flags)
            if ip_bytes > ETHERNET_MTU:
                headers = int(ip.ihl) * 4 + int(layer4.dataofs) * 4
                payload = ip_bytes - headers
                segments = -(-payload // (ETHERNET_MTU - headers))
                length = payload + segments * headers
    icmp = packet.getlayer("ICMP") if proto == "icmp" else None

    return PacketInfo(
        ts=float(packet.time),
        src_ip=str(ip.src),
        dst_ip=str(ip.dst),
        src_port=src_port,
        dst_port=dst_port,
        proto=proto,
        length=length,
        ttl=int(ip.ttl),
        tcp_flags=tcp_flags,
        segments=segments,
        icmp_type=int(icmp.type) if icmp is not None else None,
    )


def _service_for(flow):
    """Approximate the Bro service label from the better-known port of the pair."""
    return SERVICE_PORTS.get(flow.dst_port) or SERVICE_PORTS.get(flow.src_port) or "-"


def _state_for(flow):
    """Approximate the Argus connection state from what the packets showed.

    Argus derives richer states from its own state machine; this covers the
    values that dominate the bundled datasets: RST (reset, also after a finish,
    as Argus reports it), FIN (finished), CON (connected, both directions seen),
    REQ (request without an answer), INT (one-sided) and ECO (ICMP echo, the
    state UNSW-NB15 gives pings).
    """
    if flow.proto == "icmp" and flow.saw_echo:
        return "ECO"
    if flow.proto == "tcp":
        if flow.saw_rst:
            return "RST"
        if flow.saw_fin:
            return "FIN"
        if flow.spkts and flow.dpkts:
            return "CON"
        if flow.saw_syn:
            return "REQ"
        return "INT"
    if flow.spkts and flow.dpkts:
        return "CON"
    return "INT"


def _mean_interval_ms(intervals):
    if not intervals:
        return 0.0
    return (sum(intervals) / len(intervals)) * 1000.0


def _load_bps(byte_count, packets, dur):
    """Bits per second as UNSW-NB15 defines load: the first packet's bytes do not count.

    The dataset's ``sload`` equals ``sbytes * 8 * (spkts - 1) / spkts / dur`` in
    over 99.99% of its training rows (Stage 6A.1 feature-fidelity comparison), so a
    one-packet direction has load 0.
    """
    if dur <= 0 or packets <= 1:
        return 0.0
    return byte_count * 8.0 * (packets - 1) / packets / dur


def compute_features(flow):
    """Compute the 13 per-flow feature values; the tracker adds the connection counts."""
    dur = max(flow.last_ts - flow.first_ts, 0.0)
    total_packets = flow.spkts + flow.dpkts
    rate = ((total_packets - 1) / dur) if dur > 0 and total_packets > 1 else 0.0
    sload = _load_bps(flow.sbytes, flow.spkts, dur)
    dload = _load_bps(flow.dbytes, flow.dpkts, dur)

    return {
        "dur": round(dur, 6),
        "proto": flow.proto,
        "service": _service_for(flow),
        "state": _state_for(flow),
        "spkts": flow.spkts,
        "dpkts": flow.dpkts,
        "sbytes": flow.sbytes,
        "dbytes": flow.dbytes,
        "rate": round(rate, 6),
        "sload": round(sload, 6),
        "dload": round(dload, 6),
        "sinpkt": round(_mean_interval_ms(flow.src_intervals), 6),
        "dinpkt": round(_mean_interval_ms(flow.dst_intervals), 6),
    }


def _emit(flow, reason):
    """Package one finished flow as the event the monitoring pipeline consumes."""
    return {
        "features": compute_features(flow),
        "source_ip": flow.src_ip,
        "destination_ip": flow.dst_ip,
        "source_port": flow.src_port,
        "destination_port": flow.dst_port,
        "protocol": flow.proto,
        "first_ts": flow.first_ts,
        "last_ts": flow.last_ts,
        "end_reason": reason,
    }


class FlowTracker:
    """Track live packets into flows and emit them as they finish.

    Flows end on TCP teardown (FIN or RST, held ``CLOSE_HOLD_SECONDS`` for packets still
    in flight), after ``idle_timeout`` seconds of silence, or in ``active_timeout``-second
    slices for long-lived flows so a large transfer is reported while it is still happening.
    All timing is packet-timestamp driven.
    """

    def __init__(
        self,
        idle_timeout=IDLE_TIMEOUT_SECONDS,
        active_timeout=ACTIVE_TIMEOUT_SECONDS,
    ):
        self.idle_timeout = float(idle_timeout)
        self.active_timeout = float(active_timeout)
        self._flows = {}
        self._closing = {}  # key -> (flow, end reason, hold deadline)
        self._draining = {}
        self._recent = deque(maxlen=CONNECTION_WINDOW)
        self._last_sweep_ts = 0.0
        self.packets_seen = 0
        self.flows_emitted = 0

    def _lookup(self, info):
        forward = (info.src_ip, info.src_port, info.dst_ip, info.dst_port, info.proto)
        if forward in self._flows:
            return forward, True
        reverse = (info.dst_ip, info.dst_port, info.src_ip, info.src_port, info.proto)
        if reverse in self._flows:
            return reverse, False
        return forward, True

    def add_packet(self, info):
        """Feed one packet in; return the list of flows this packet finished."""
        self.packets_seen += 1
        # Flows whose closing hold has passed end before this packet is counted.
        emitted = self._close_due(info.ts, info)

        key, is_forward = self._lookup(info)
        reverse_key = (info.dst_ip, info.dst_port, info.src_ip, info.src_port, info.proto)

        # A packet within the closing hold still belongs to the connection: Argus counts the
        # server's last data or reset that arrive just after the client's reset or final FIN.
        closing_key = key if key in self._closing else (
            reverse_key if reverse_key in self._closing else None)
        if closing_key is not None:
            flow = self._closing[closing_key][0]
            self._add_to_flow(flow, info, (info.src_ip, info.src_port) == (flow.src_ip,
                                                                            flow.src_port))
            emitted.extend(self.advance(info.ts))
            return self._count(emitted)

        # Swallow the trailing ACK or reset of a teardown whose flow was already
        # emitted, so every TCP session yields one flow instead of a one-packet
        # stray (Argus keeps them in the same connection).
        drain_key = key if key in self._draining else reverse_key
        drain_deadline = self._draining.get(drain_key)
        if drain_deadline is not None:
            if info.ts <= drain_deadline and self._is_teardown_tail(info):
                emitted.extend(self.advance(info.ts))
                return self._count(emitted)
            del self._draining[drain_key]
        flow = self._flows.get(key)

        # A 5-tuple reappearing after the idle timeout is a new flow: emit the
        # stale one instead of merging two conversations into a single record.
        if flow is not None and (info.ts - flow.last_ts) >= self.idle_timeout:
            emitted.append(_emit(flow, "idle_timeout"))
            del self._flows[key]
            flow = None
            key, is_forward = (
                (info.src_ip, info.src_port, info.dst_ip, info.dst_port, info.proto),
                True,
            )

        if flow is not None and (info.ts - flow.first_ts) >= self.active_timeout:
            emitted.append(_emit(flow, "active_timeout"))
            del self._flows[key]
            # The next slice continues the same conversation, so it keeps the
            # original orientation even when this packet travels dst-to-src;
            # ``key`` and ``is_forward`` from the lookup stay valid.
            flow = _FlowState(
                src_ip=flow.src_ip,
                dst_ip=flow.dst_ip,
                src_port=flow.src_port,
                dst_port=flow.dst_port,
                proto=flow.proto,
                first_ts=info.ts,
                last_ts=info.ts,
            )
            self._flows[key] = flow

        if flow is None:
            flow = _FlowState(
                src_ip=info.src_ip,
                dst_ip=info.dst_ip,
                src_port=info.src_port,
                dst_port=info.dst_port,
                proto=info.proto,
                first_ts=info.ts,
                last_ts=info.ts,
            )
            self._flows[key] = flow
            is_forward = True

        self._add_to_flow(flow, info, is_forward)

        if info.proto == "tcp":
            teardown_complete = flow.fin_forward and flow.fin_reverse
            if flow.saw_rst or teardown_complete:
                # Held for CLOSE_HOLD_SECONDS so the connection's last packets are counted.
                del self._flows[key]
                self._closing[key] = (flow, "fin" if teardown_complete else "rst",
                                      info.ts + CLOSE_HOLD_SECONDS)

        emitted.extend(self.advance(info.ts))
        return self._count(emitted)

    @staticmethod
    def _add_to_flow(flow, info, is_forward):
        flow.last_ts = max(flow.last_ts, info.ts)
        # The segments of a large send leave back to back.
        extra_gaps = [0.0] * (info.segments - 1)
        if is_forward:
            flow.spkts += info.segments
            flow.sbytes += info.length
            if flow.last_src_ts is not None:
                flow.src_intervals.append(info.ts - flow.last_src_ts)
            flow.src_intervals.extend(extra_gaps)
            flow.last_src_ts = info.ts
        else:
            flow.dpkts += info.segments
            flow.dbytes += info.length
            if flow.last_dst_ts is not None:
                flow.dst_intervals.append(info.ts - flow.last_dst_ts)
            flow.dst_intervals.extend(extra_gaps)
            flow.last_dst_ts = info.ts

        if info.icmp_type in (0, 8):  # echo reply, echo request
            flow.saw_echo = True

        if info.proto == "tcp":
            flags = info.tcp_flags or ""
            if "S" in flags and "A" not in flags:
                flow.saw_syn = True
            if "F" in flags:
                flow.saw_fin = True
                if is_forward:
                    flow.fin_forward = True
                else:
                    flow.fin_reverse = True
            if "R" in flags:
                flow.saw_rst = True

    def _close_due(self, now_ts, info=None):
        """Emit closing flows whose hold has passed, or that a new SYN on their ports replaces."""
        due = []
        for key, (flow, reason, deadline) in list(self._closing.items()):
            new_connection = (
                info is not None and info.proto == "tcp" and "S" in (info.tcp_flags or "")
                and "A" not in (info.tcp_flags or "")
                and {(info.src_ip, info.src_port), (info.dst_ip, info.dst_port)}
                == {(flow.src_ip, flow.src_port), (flow.dst_ip, flow.dst_port)})
            if now_ts > deadline or new_connection:
                del self._closing[key]
                due.append(_emit(flow, reason))
                self._draining[key] = deadline + DRAIN_SECONDS
        return due
    @staticmethod
    def _is_teardown_tail(info):
        """A pure ACK or a reset after the flow closed; a new SYN starts a new flow."""
        flags = info.tcp_flags or ""
        if info.proto != "tcp" or "S" in flags:
            return False
        return "R" in flags or ("A" in flags and not any(f in flags for f in "FP"))

    def _count(self, events):
        """Count emitted flows and add their connection-history features, in emission order.

        UNSW-NB15 orders connections by their last packet; emission order follows it closely
        (idle-timeout flows are reported up to one sweep late).
        """
        for event in events:
            features = event["features"]
            src, dst, service = event["source_ip"], event["destination_ip"], features["service"]
            self._recent.append((src, dst, service))
            features["ct_dst_src_ltm"] = sum(1 for s, d, _ in self._recent if s == src and d == dst)
            features["ct_dst_ltm"] = sum(1 for _, d, _ in self._recent if d == dst)
            features["ct_srv_dst"] = sum(
                1 for _, d, sv in self._recent if d == dst and sv == service
            )
        self.flows_emitted += len(events)
        return events

    def advance(self, now_ts):
        """Expire idle flows as of ``now_ts``; called per packet and by timers."""
        if (now_ts - self._last_sweep_ts) < SWEEP_INTERVAL_SECONDS:
            return []
        self._last_sweep_ts = now_ts

        # Drop drain markers whose grace window has passed, so completed
        # connections do not accumulate for the lifetime of the tracker.
        for key in list(self._draining):
            if now_ts > self._draining[key]:
                del self._draining[key]

        expired = []
        for key in list(self._flows):
            flow = self._flows[key]
            if (now_ts - flow.last_ts) >= self.idle_timeout:
                expired.append(_emit(flow, "idle_timeout"))
                del self._flows[key]
        return expired

    def expire_idle(self, now_ts):
        """Public idle sweep for timer-driven callers (live capture)."""
        return self._count(self._close_due(now_ts) + self.advance(now_ts))

    def close_due(self, now_ts):
        """Emit only the closing flows whose hold has passed (frequent timer in live capture)."""
        return self._count(self._close_due(now_ts))

    @property
    def closing_flows(self):
        return len(self._closing)

    def flush(self):
        """Emit every in-progress flow; used when a capture or replay ends."""
        remaining = [_emit(flow, reason) for flow, reason, _ in self._closing.values()]
        remaining += [_emit(flow, "flush") for flow in self._flows.values()]
        self._closing.clear()
        self._flows.clear()
        self._draining.clear()
        return self._count(remaining)

    @property
    def active_flows(self):
        return len(self._flows)
