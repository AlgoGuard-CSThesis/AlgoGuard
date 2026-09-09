"""Packet-to-flow aggregation for AlgoGuard.

Turns individual data packets into bidirectional network flows and computes the
same 15 UNSW-NB15-style features the deployed model was trained on: ``dur``,
``proto``, ``service``, ``state``, ``spkts``, ``dpkts``, ``sbytes``, ``dbytes``,
``rate``, ``sttl``, ``dttl``, ``sload``, ``dload``, ``sinpkt`` and ``dinpkt``.

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

from dataclasses import dataclass, field

IDLE_TIMEOUT_SECONDS = 15.0
ACTIVE_TIMEOUT_SECONDS = 120.0
SWEEP_INTERVAL_SECONDS = 1.0

FLOW_FEATURE_COLUMNS = (
    "dur", "proto", "service", "state", "spkts", "dpkts", "sbytes", "dbytes",
    "rate", "sttl", "dttl", "sload", "dload", "sinpkt", "dinpkt",
)

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
    sttl: int = 0
    dttl: int = 0
    src_intervals: list = field(default_factory=list)
    dst_intervals: list = field(default_factory=list)
    last_src_ts: float | None = None
    last_dst_ts: float | None = None
    saw_syn: bool = False
    saw_fin: bool = False
    saw_rst: bool = False
    fin_forward: bool = False
    fin_reverse: bool = False


def packet_info_from_scapy(packet):
    """Convert a Scapy packet to :class:`PacketInfo`, or ``None`` if not IP traffic.

    Byte accounting uses the IP total length (header plus payload). UNSW-NB15
    counted link-layer bytes; the constant per-packet difference is a documented
    approximation measured by the research workspace.
    """
    ip = packet.getlayer("IP")
    if ip is None:
        return None

    proto = PROTO_NAMES.get(int(ip.proto), str(int(ip.proto)))
    src_port = dst_port = 0
    tcp_flags = ""
    layer4 = packet.getlayer("TCP") or packet.getlayer("UDP")
    if layer4 is not None:
        src_port = int(layer4.sport)
        dst_port = int(layer4.dport)
        if proto == "tcp":
            tcp_flags = str(layer4.flags)

    return PacketInfo(
        ts=float(packet.time),
        src_ip=str(ip.src),
        dst_ip=str(ip.dst),
        src_port=src_port,
        dst_port=dst_port,
        proto=proto,
        length=int(ip.len) if ip.len is not None else len(bytes(ip)),
        ttl=int(ip.ttl),
        tcp_flags=tcp_flags,
    )


def _service_for(flow):
    """Approximate the Bro service label from the better-known port of the pair."""
    return SERVICE_PORTS.get(flow.dst_port) or SERVICE_PORTS.get(flow.src_port) or "-"


def _state_for(flow):
    """Approximate the Argus connection state from what the packets showed.

    Argus derives richer states from its own state machine; this covers the
    values that dominate the bundled datasets: FIN (finished), CON (connected,
    both directions seen), REQ (request without an answer), INT (one-sided),
    and RST (reset without a finish).
    """
    if flow.proto == "tcp":
        if flow.saw_fin:
            return "FIN"
        if flow.saw_rst:
            return "RST"
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


def compute_features(flow):
    """Compute the 15 deployed feature values for one finished flow."""
    dur = max(flow.last_ts - flow.first_ts, 0.0)
    total_packets = flow.spkts + flow.dpkts
    rate = ((total_packets - 1) / dur) if dur > 0 and total_packets > 1 else 0.0
    sload = (flow.sbytes * 8.0 / dur) if dur > 0 else 0.0
    dload = (flow.dbytes * 8.0 / dur) if dur > 0 else 0.0

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
        "sttl": flow.sttl,
        "dttl": flow.dttl,
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

    Flows end on TCP teardown (FIN or RST), after ``idle_timeout`` seconds of
    silence, or in ``active_timeout``-second slices for long-lived flows so a
    large transfer is reported while it is still happening. All timing is
    packet-timestamp driven.
    """

    def __init__(
        self,
        idle_timeout=IDLE_TIMEOUT_SECONDS,
        active_timeout=ACTIVE_TIMEOUT_SECONDS,
    ):
        self.idle_timeout = float(idle_timeout)
        self.active_timeout = float(active_timeout)
        self._flows = {}
        self._draining = {}
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
        emitted = []
        self.packets_seen += 1

        key, is_forward = self._lookup(info)

        # Swallow the trailing ACK of a teardown whose flow was just emitted,
        # so every TCP session yields one flow instead of a one-packet stray.
        reverse_key = (info.dst_ip, info.dst_port, info.src_ip, info.src_port, info.proto)
        drain_key = key if key in self._draining else reverse_key
        drain_deadline = self._draining.get(drain_key)
        if drain_deadline is not None:
            if info.ts <= drain_deadline and self._is_pure_ack(info):
                emitted.extend(self._count(self.advance(info.ts)))
                return emitted
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

        flow.last_ts = max(flow.last_ts, info.ts)
        if is_forward:
            flow.spkts += 1
            flow.sbytes += info.length
            if flow.sttl == 0:
                flow.sttl = info.ttl
            if flow.last_src_ts is not None:
                flow.src_intervals.append(info.ts - flow.last_src_ts)
            flow.last_src_ts = info.ts
        else:
            flow.dpkts += 1
            flow.dbytes += info.length
            if flow.dttl == 0:
                flow.dttl = info.ttl
            if flow.last_dst_ts is not None:
                flow.dst_intervals.append(info.ts - flow.last_dst_ts)
            flow.last_dst_ts = info.ts

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

            teardown_complete = flow.fin_forward and flow.fin_reverse
            if flow.saw_rst or teardown_complete:
                emitted.append(_emit(flow, "fin" if teardown_complete else "rst"))
                del self._flows[key]
                self._draining[key] = info.ts + 2.0

        emitted = self._count(emitted)
        emitted.extend(self._count(self.advance(info.ts)))
        return emitted

    @staticmethod
    def _is_pure_ack(info):
        flags = info.tcp_flags or ""
        return info.proto == "tcp" and "A" in flags and not any(f in flags for f in "FSRP")

    def _count(self, events):
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
        return self._count(self.advance(now_ts))

    def flush(self):
        """Emit every in-progress flow; used when a capture or replay ends."""
        remaining = [_emit(flow, "flush") for flow in self._flows.values()]
        self._flows.clear()
        self._draining.clear()
        self.flows_emitted += len(remaining)
        return remaining

    @property
    def active_flows(self):
        return len(self._flows)
