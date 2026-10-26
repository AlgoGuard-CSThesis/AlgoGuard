"""Traffic sources for the AlgoGuard Live Monitor.

Every monitoring session consumes one :class:`TrafficSource`. Three kinds
exist, and everything after the source — flow classification, the event feed,
alerting, persistence — is shared:

- :class:`CsvReplaySource`: the original behaviour; replays labelled flow
  records from a bundled CSV, paced by the chosen speed.
- :class:`PcapReplaySource`: reads a recorded packet capture, aggregates the
  data packets into flows with :class:`~services.flow_tracker_service.FlowTracker`,
  and replays the resulting flows, paced. Reproducible packet-level input.
- :class:`LiveCaptureSource`: sniffs data packets from a network interface in
  real time (Scapy over Npcap/libpcap), aggregates them into flows, and emits
  each flow the moment it ends. Runs at wire pace; no ordering or speed.

Scapy is imported lazily so the web application still starts, and replay modes
still work, on machines without a capture stack. ``live_capture_available``
reports whether live mode can run and why not when it cannot.
"""

import os
import queue
import random
import struct
import threading
import time

import pandas as pd

from cloud_connections import REGISTRY as OWNED_CLOUD_CONNECTIONS
from config import get_config
from maintenance_connections import SharedConnections
from services.capture_worker import CaptureWorker, CaptureWorkerError
from services.flow_tracker_service import FLOW_FEATURE_COLUMNS, FlowTracker, packet_info_from_scapy
from services.preprocessing_service import encode_binary_target


class TrafficSourceError(RuntimeError):
    """Raised when a traffic source cannot be prepared or read."""


# Classic pcap magic numbers (microsecond and nanosecond, both byte orders) -> struct endianness.
_PCAP_MAGIC = {b"\xd4\xc3\xb2\xa1": "<", b"\xa1\xb2\xc3\xd4": ">",
               b"\x4d\x3c\xb2\xa1": "<", b"\xa1\xb2\x3c\x4d": ">"}


def _pcap_is_truncated(path):
    """True when a classic pcap file ends part-way through a packet record.

    Scapy reads a cut-off final packet with only a log warning, so a truncated capture would
    otherwise replay partially without the user knowing. pcapng files are not checked here.
    """
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as handle:
            endian = _PCAP_MAGIC.get(handle.read(4))
            if endian is None or size < 24:
                return False
            position = 24  # global header
            while position < size:
                handle.seek(position)
                header = handle.read(16)
                if len(header) < 16:
                    return True
                position += 16 + struct.unpack(endian + "IIII", header)[2]
            return position > size
    except OSError:
        return False


class TrafficSourceCancelled(TrafficSourceError):
    """Raised when source preparation is cancelled by the monitor."""


def _ensure_scapy_layers():
    """Load Scapy's core dissectors so captured frames decode to Ether/IP.

    Scapy registers link-layer bindings when its layer modules import; code
    that only imports the sniffer would otherwise receive raw undissected
    packets. Import errors are reported through live_capture_available /
    prepare instead of here.
    """
    from scapy.layers import inet, l2  # noqa: F401


class TrafficSource:
    """One stream of classifiable flow events.

    ``prepare()`` is called once inside the worker thread before the loop, and
    may raise :class:`TrafficSourceError`. ``next_event(timeout)`` returns the
    next event dict, ``None`` when nothing is available yet (live capture
    between flows), or raises :class:`StopIteration` when the source is
    exhausted. ``paced`` says whether the worker should apply the replay-speed
    delay between events. ``close()`` always runs, exactly once, at the end.
    """

    paced = True
    row_total = None
    has_ground_truth = False

    def prepare(self):
        raise NotImplementedError

    def next_event(self, timeout=0.2):
        raise NotImplementedError

    def backlog(self) -> bool:
        """Whether more events can be read now without waiting, so they can be batched."""
        return False

    def close(self):
        """Release any capture handles; overridden where needed."""

    def stats(self):
        """Source-side counters for logging; overridden where meaningful."""
        return {}


def _flow_event(flow, actual=None):
    """Shape one FlowTracker emission into the monitor's event contract."""
    return {
        "record": dict(flow["features"]),
        "actual": actual,
        "source_ip": flow["source_ip"],
        "destination_ip": flow["destination_ip"],
        "source_port": flow["source_port"],
        "destination_port": flow["destination_port"],
        "protocol": flow["protocol"],
        "flow_last_ts": flow["last_ts"],
        "end_reason": flow["end_reason"],
        # When the tracker finished the flow: splits detection lag into tracking and scoring.
        "finished_at": time.time(),
    }


class CsvReplaySource(TrafficSource):
    """Replay labelled flow rows from a CSV — the original Live Monitor input.

    Rows already are flows, so no tracker is involved. Endpoints are synthetic
    private-range addresses, kept purely so stored replay flows stay readable;
    packet-based sources carry their real 5-tuple instead.
    """

    has_ground_truth = True

    def __init__(self, path, order="sequential"):
        self._path = path
        self._order = order
        self._records = []
        self._labels = []
        self._index = 0
        self._rng = random.Random()

    def prepare(self):
        try:
            frame = pd.read_csv(self._path)
        except Exception as error:
            raise TrafficSourceError(f"Unable to read the traffic sample: {error}") from error
        if self._order == "random":
            frame = frame.sample(frac=1).reset_index(drop=True)
        labels = frame["label"] if "label" in frame.columns else None
        self._records = frame.to_dict("records")
        self._labels = None
        if labels is not None:
            try:
                encoded, _ = encode_binary_target(labels, allow_single_class=True)
            except ValueError as error:
                raise TrafficSourceError(f"Invalid traffic sample labels: {error}") from error
            self._labels = encoded.map({0: "Normal", 1: "Attack"}).tolist()
        self.columns = list(frame.columns)
        self.row_total = len(self._records)

    def _synthetic_endpoints(self):
        rng = self._rng
        source = f"10.{rng.randint(0, 40)}.{rng.randint(0, 255)}.{rng.randint(2, 254)}"
        destination = f"192.168.{rng.randint(0, 20)}.{rng.randint(2, 254)}"
        return source, destination

    def next_event(self, timeout=0.2):
        if self._index >= len(self._records):
            raise StopIteration
        record = self._records[self._index]
        actual = self._labels[self._index] if self._labels is not None else None
        self._index += 1
        source_ip, destination_ip = self._synthetic_endpoints()
        return {
            "record": record,
            "actual": actual,
            "source_ip": source_ip,
            "destination_ip": destination_ip,
            "source_port": 0,
            "destination_port": 0,
            "protocol": str(record.get("proto", "")),
            "flow_last_ts": None,
            "end_reason": "replay",
        }

    def backlog(self):
        return self._index < len(self._records)


class PcapReplaySource(TrafficSource):
    """Aggregate a recorded packet capture into replayable network flows.

    Sequential replay reads packets incrementally and keeps only active and
    immediately pending flows in memory. Random replay must buffer the complete
    capture because shuffling inherently requires the full flow set.
    """

    columns = FLOW_FEATURE_COLUMNS

    def __init__(
        self,
        path,
        order="sequential",
        idle_timeout=None,
        active_timeout=None,
        cancel_event=None,
    ):
        self._path = path
        self._order = order
        self._cancel_event = cancel_event
        self._flows = []
        self._index = 0
        self._reader = None
        self._tracker = None
        self._pending_flows = []
        self._exhausted = False
        self._tracker_kwargs = {}
        if idle_timeout is not None:
            self._tracker_kwargs["idle_timeout"] = idle_timeout
        if active_timeout is not None:
            self._tracker_kwargs["active_timeout"] = active_timeout
        self.packets_read = 0
        self.flows_emitted = 0

    def prepare(self):
        if self._cancel_event is not None and self._cancel_event.is_set():
            raise TrafficSourceCancelled("Packet capture preparation was cancelled.")

        try:
            from scapy.utils import PcapReader

            _ensure_scapy_layers()
        except ImportError as error:
            raise TrafficSourceError(
                "Reading packet captures requires the scapy package "
                "(python -m pip install -r requirements.txt)."
            ) from error

        if _pcap_is_truncated(self._path):
            raise TrafficSourceError(
                "The packet capture is incomplete: its last packet is cut off. "
                "Use a complete capture file."
            )

        if self._order == "random":
            self._prepare_buffered(PcapReader)
            return

        self._tracker = FlowTracker(**self._tracker_kwargs)
        try:
            self._reader = PcapReader(self._path)
            first_flow = self._next_sequential_flow()
        except TrafficSourceError:
            self.close()
            raise
        except StopIteration:
            self.close()
            raise TrafficSourceError(
                "The packet capture contains no classifiable IP flows."
            ) from None
        except Exception as error:
            self.close()
            raise TrafficSourceError(f"Unable to read the packet capture: {error}") from error
        self._pending_flows.append(first_flow)

    def _prepare_buffered(self, reader_type):
        tracker = FlowTracker(**self._tracker_kwargs)
        flows = []
        try:
            with reader_type(self._path) as reader:
                for packet in reader:
                    self._raise_if_cancelled()
                    info = packet_info_from_scapy(packet)
                    if info is None:
                        continue
                    self.packets_read += 1
                    flows.extend(tracker.add_packet(info))
        except TrafficSourceError:
            raise
        except Exception as error:
            raise TrafficSourceError(f"Unable to read the packet capture: {error}") from error
        self._raise_if_cancelled()
        flows.extend(tracker.flush())

        if not flows:
            raise TrafficSourceError("The packet capture contains no classifiable IP flows.")
        random.shuffle(flows)
        self._flows = flows
        self.row_total = len(flows)

    def _raise_if_cancelled(self):
        if self._cancel_event is not None and self._cancel_event.is_set():
            raise TrafficSourceCancelled("Packet capture preparation was cancelled.")

    def _next_sequential_flow(self):
        if self._pending_flows:
            return self._pending_flows.pop(0)
        if self._exhausted:
            self.row_total = self.flows_emitted
            raise StopIteration

        if self._reader is None or self._tracker is None:
            raise TrafficSourceError("Prepare the packet capture before reading flows.")
        while True:
            self._raise_if_cancelled()
            try:
                packet = next(self._reader)
            except StopIteration:
                self._pending_flows.extend(self._tracker.flush())
                self._exhausted = True
                self._close_reader()
                if self._pending_flows:
                    return self._pending_flows.pop(0)
                self.row_total = self.flows_emitted
                raise

            info = packet_info_from_scapy(packet)
            if info is None:
                continue
            self.packets_read += 1
            self._pending_flows.extend(self._tracker.add_packet(info))
            if self._pending_flows:
                return self._pending_flows.pop(0)

    def next_event(self, timeout=0.2):
        if self._order == "random":
            if self._index >= len(self._flows):
                raise StopIteration
            flow = self._flows[self._index]
            self._index += 1
        else:
            try:
                flow = self._next_sequential_flow()
            except (StopIteration, TrafficSourceError):
                raise
            except Exception as error:
                self.close()
                raise TrafficSourceError(f"Unable to read the packet capture: {error}") from error
        self.flows_emitted += 1
        return _flow_event(flow)

    def backlog(self):
        if self._order == "random":
            return self._index < len(self._flows)
        return bool(self._pending_flows) or not self._exhausted

    def _close_reader(self):
        if self._reader is not None:
            self._reader.close()
            self._reader = None

    def close(self):
        self._close_reader()

    def stats(self):
        return {"packets": self.packets_read, "flows": self.flows_emitted}


BASE_BPF_FILTER = "ip and (tcp or udp)"


def algoguard_port():
    """The TCP port the web application serves on, as app.py resolves it."""
    return get_config().port


def build_capture_filter(exclude_ports=None):
    """Build the BPF filter, excluding AlgoGuard's own web traffic by default.

    ``None`` means "apply the default exclusion", which is AlgoGuard's own web
    port read from current configuration. Pass an explicit empty collection to
    build an unfiltered capture.

    The default used to be ``()``, so calling this with no arguments produced a
    filter that excluded nothing — the docstring's "by default" was only true
    because :class:`LiveCaptureSource` passed the port in itself. Reading the
    port here keeps the promise at the level that makes it.
    """
    if exclude_ports is None:
        exclude_ports = {algoguard_port()}
    clauses = [BASE_BPF_FILTER]
    for port in sorted({int(port) for port in exclude_ports if port}):
        clauses.append(f"not port {port}")
    return " and ".join(clauses)


# While a closed flow is held for its last packets, a quiet capture checks this often.
CLOSE_POLL_SECONDS = 0.02


class LiveCaptureSource(TrafficSource):
    """Sniff data packets from a network interface and emit flows as they end.

    AlgoGuard's own web traffic is excluded from capture. Without this, running
    a live session on the interface the application serves from makes the
    monitor classify the browser's own status polling: the system watches
    itself and the feed fills with its own flows. The exclusion is applied
    twice on purpose - once in the BPF filter, and again per packet in Python,
    because a capture driver without a libpcap backend cannot compile the
    filter and falls back to unfiltered capture.

    Stage 5D.4: the application's own cloud API/Auth/Storage connections are
    excluded per packet by exact TCP connection (see ``cloud_connections``).
    Their ephemeral ports are unknown when the BPF filter is compiled, so this
    check runs in Python for filtered and fallback captures alike. Other HTTPS
    to the same provider, including the same IP address, stays visible.
    """

    paced = False
    columns = FLOW_FEATURE_COLUMNS

    def __init__(
        self,
        interface,
        bpf_filter=None,
        idle_timeout=None,
        exclude_ports=None,
        owned_connections=None,
    ):
        self._interface = interface
        self._owned = OWNED_CLOUD_CONNECTIONS if owned_connections is None else owned_connections
        self._maintenance = SharedConnections(get_config().state_dir)
        self.exclude_ports = (
            {algoguard_port()} if exclude_ports is None else {int(p) for p in exclude_ports if p}
        )
        self.bpf_filter = (
            bpf_filter if bpf_filter is not None else build_capture_filter(self.exclude_ports)
        )
        self._tracker = FlowTracker(**({"idle_timeout": idle_timeout} if idle_timeout else {}))
        self._packets = queue.Queue(maxsize=10000)
        self._flows = queue.Queue()
        self._capture = None
        self._capture_error = None
        # Live capture turns packets into flows on this thread, so a flow is finished the
        # moment its last packet arrives even while the monitor is busy classifying.
        self._worker = None
        self._worker_error = None
        self._stopping = threading.Event()
        self._lock = threading.Lock()
        self.packets_captured = 0
        self.packets_dropped = 0
        self.packets_excluded = 0
        self.packets_excluded_cloud = 0
        self.flows_emitted = 0
        self._last_idle_check = 0.0

    def _is_own_traffic(self, info):
        return self._own_traffic_kind(info) is not None

    def _own_traffic_kind(self, info):
        if info.src_port in self.exclude_ports or info.dst_port in self.exclude_ports:
            return "web"
        if self._owned.matches(info.proto, info.src_ip, info.src_port, info.dst_ip, info.dst_port):
            return "cloud"
        flags = info.tcp_flags or ""
        if self._maintenance.matches(
                info.proto, info.src_ip, info.src_port, info.dst_ip, info.dst_port,
                new_connection="S" in flags and "A" not in flags):
            return "cloud"
        return None

    def _on_packet(self, packet):
        try:
            self._packets.put_nowait(packet)
        except queue.Full:
            with self._lock:
                self.packets_dropped += 1

    def prepare(self):
        available, reason = live_capture_available()
        if not available:
            raise TrafficSourceError(reason)

        # BPF filtering is an optimisation; a driver that cannot compile the
        # filter (no libpcap backend) still captures fine unfiltered, and the
        # tracker ignores non-IP packets in Python anyway.
        try:
            self._start_capture(self.bpf_filter)
        except TrafficSourceError as filtered_error:
            if "filter" not in str(filtered_error).lower():
                raise
            self.bpf_filter = ""
            try:
                self._start_capture(None)
            except TrafficSourceError:
                raise filtered_error from None
        self._worker = threading.Thread(target=self._track, name="algoguard-flow-tracker",
                                        daemon=True)
        self._worker.start()

    def _start_capture(self, bpf_filter):
        """Capture and decode packets in a child process, off this interpreter (DEF-17)."""
        try:
            self._capture = CaptureWorker(self._interface, bpf_filter)
        except CaptureWorkerError as error:
            raise TrafficSourceError(
                "Packet capture could not start on this interface. Check that "
                "Npcap is installed and AlgoGuard is running with the "
                f"required privileges. Details: {error}"
            ) from None

    def _track(self):
        """Worker loop: turn the capture process's packets into flows until the source closes."""
        capture = self._capture
        if capture is None:
            return
        try:
            while not self._stopping.is_set():
                _, infos, ignored_ts, dropped = capture.receive()
                if dropped:
                    with self._lock:
                        self.packets_dropped += dropped
                for info in infos:
                    self._process_info(info)
                if ignored_ts is not None:
                    # Non-IP packets still advance capture time.
                    self._emit(self._tracker.expire_idle(ignored_ts))
                self._expire(time.time())
        except CaptureWorkerError as error:
            if not self._stopping.is_set():
                self._capture_error = str(error)
        except Exception as error:  # surfaced to the monitor by next_event
            self._worker_error = error

    def next_event(self, timeout=0.2):
        if self._capture_error is not None:
            raise TrafficSourceError(
                "Packet capture stopped unexpectedly. Check the interface and "
                f"restart monitoring. Details: {self._capture_error}"
            )
        if self._worker_error is not None:
            raise TrafficSourceError(
                "Flow tracking stopped unexpectedly. Restart monitoring. "
                f"Details: {self._worker_error}"
            )
        if self._worker is None and self._flows.empty():
            # Without the worker (before prepare, as in tests) one packet is processed here.
            self._process(timeout)
            timeout = 0
        try:
            event = self._flows.get(timeout=timeout)
        except queue.Empty:
            return None
        self.flows_emitted += 1
        return event

    def _process(self, timeout):
        """Turn at most one queued Scapy packet into finished flows, or expire flows when quiet."""
        if self._tracker.closing_flows:
            timeout = min(timeout, CLOSE_POLL_SECONDS)
        try:
            packet = self._packets.get(timeout=timeout)
        except queue.Empty:
            self._expire(time.time())
            return
        info = packet_info_from_scapy(packet)
        if info is None:
            # Ignored packets still advance capture time. Otherwise a steady
            # stream of non-IP traffic prevents quiet flows from expiring.
            self._emit(self._tracker.expire_idle(float(packet.time)))
            return
        self._process_info(info)

    def _process_info(self, info):
        own = self._own_traffic_kind(info)
        if own is not None:
            # The BPF filter normally keeps web-port packets out; this catches
            # them when the driver could not compile it and capture ran
            # unfiltered. Cloud connections are only recognisable here.
            with self._lock:
                self.packets_excluded += 1
                if own == "cloud":
                    self.packets_excluded_cloud += 1
            self._emit(self._tracker.expire_idle(info.ts))
            return
        with self._lock:
            self.packets_captured += 1
        self._emit(self._tracker.add_packet(info))

    def _expire(self, now):
        """Release held flows whose hold has passed and, once a second, idle flows."""
        self._emit(self._tracker.close_due(now))
        if now - self._last_idle_check >= 1.0:
            self._last_idle_check = now
            self._emit(self._tracker.expire_idle(now))
    def _emit(self, flows):
        for flow in flows:
            self._flows.put(_flow_event(flow))

    def backlog(self):
        if self._worker is not None:
            # Only finished flows count: waiting for packets would delay the batch.
            return not self._flows.empty()
        return not self._flows.empty() or not self._packets.empty()

    def close(self, wait=True):
        self._stopping.set()
        if self._capture is not None:
            self._capture.close(wait)
            self._capture = None
        if self._worker is not None and wait and self._worker is not threading.current_thread():
            self._worker.join(timeout=1.0)
        self._maintenance.close()

    def stats(self):
        with self._lock:
            return {
                "packets": self.packets_captured,
                "dropped": self.packets_dropped,
                "excluded": self.packets_excluded,
                "excluded_cloud": self.packets_excluded_cloud,
                "flows": self.flows_emitted,
                "active_flows": self._tracker.active_flows,
            }


def live_capture_available():
    """Report whether live packet capture can run here, and why not if not."""
    try:
        from scapy.arch import get_if_list
        from scapy.config import conf
    except ImportError:
        return False, (
            "Live capture requires the scapy package. Install project "
            "dependencies with python -m pip install -r requirements.txt."
        )
    except OSError as error:
        return False, (
            "Live capture needs a packet capture driver. On Windows, install "
            f"Npcap from npcap.com and restart AlgoGuard. Details: {error}"
        )

    if os.name == "nt" and not conf.use_pcap:
        return False, (
            "Live capture needs the Npcap packet capture driver on Windows. "
            "Install Npcap from npcap.com, then restart AlgoGuard."
        )

    try:
        interfaces = get_if_list()
    except Exception as error:
        return False, f"Network interfaces could not be listed: {error}"
    if not interfaces:
        return False, "No network interfaces are visible to the capture driver."
    return True, None


def list_capture_interfaces():
    """Return selectable interfaces as value/label pairs, best guess first."""
    available, _ = live_capture_available()
    if not available:
        return []

    try:
        from scapy.config import conf

        entries = []
        for iface in conf.ifaces.values():
            name = getattr(iface, "name", None) or str(iface)
            description = getattr(iface, "description", "") or name
            ip = getattr(iface, "ip", "") or ""
            label = description if description != name else name
            if ip:
                label = f"{label} ({ip})"
            entries.append({"value": name, "label": label, "ip": ip})
        entries.sort(key=lambda item: (item["ip"] in ("", "127.0.0.1"), item["label"]))
        return [{"value": item["value"], "label": item["label"]} for item in entries]
    except Exception:
        from scapy.arch import get_if_list

        return [{"value": name, "label": name} for name in get_if_list()]


def list_capture_files(folder=None):
    """List replayable .pcap/.pcapng recordings from the captures folder."""
    target = folder or get_config().capture_folder
    if not os.path.isdir(target):
        return []
    names = [
        name
        for name in sorted(os.listdir(target))
        if name.lower().endswith((".pcap", ".pcapng", ".cap"))
    ]
    return [{"value": name, "label": name} for name in names]
