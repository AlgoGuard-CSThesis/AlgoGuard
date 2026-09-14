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
import threading
import time

import pandas as pd

from config import get_config
from services.flow_tracker_service import FLOW_FEATURE_COLUMNS, FlowTracker, packet_info_from_scapy
from services.preprocessing_service import encode_binary_target


class TrafficSourceError(RuntimeError):
    """Raised when a traffic source cannot be prepared or read."""


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


def build_capture_filter(exclude_ports=()):
    """Build the BPF filter, excluding AlgoGuard's own web traffic by default."""
    clauses = [BASE_BPF_FILTER]
    for port in sorted({int(port) for port in exclude_ports if port}):
        clauses.append(f"not port {port}")
    return " and ".join(clauses)


class LiveCaptureSource(TrafficSource):
    """Sniff data packets from a network interface and emit flows as they end.

    AlgoGuard's own web traffic is excluded from capture. Without this, running
    a live session on the interface the application serves from makes the
    monitor classify the browser's own status polling: the system watches
    itself and the feed fills with its own flows. The exclusion is applied
    twice on purpose - once in the BPF filter, and again per packet in Python,
    because a capture driver without a libpcap backend cannot compile the
    filter and falls back to unfiltered capture.
    """

    paced = False
    columns = FLOW_FEATURE_COLUMNS

    def __init__(self, interface, bpf_filter=None, idle_timeout=None, exclude_ports=None):
        self._interface = interface
        self.exclude_ports = (
            {algoguard_port()} if exclude_ports is None else {int(p) for p in exclude_ports if p}
        )
        self.bpf_filter = (
            bpf_filter if bpf_filter is not None else build_capture_filter(self.exclude_ports)
        )
        self._tracker = FlowTracker(**({"idle_timeout": idle_timeout} if idle_timeout else {}))
        self._packets = queue.Queue(maxsize=10000)
        self._sniffer = None
        self._lock = threading.Lock()
        self.packets_captured = 0
        self.packets_dropped = 0
        self.packets_excluded = 0
        self.flows_emitted = 0
        self._pending = []
        self._last_idle_check = 0.0

    def _is_own_traffic(self, info):
        return info.src_port in self.exclude_ports or info.dst_port in self.exclude_ports

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
            self._start_sniffer(self.bpf_filter)
        except TrafficSourceError as filtered_error:
            if "filter" not in str(filtered_error).lower():
                raise
            self.bpf_filter = ""
            try:
                self._start_sniffer(None)
            except TrafficSourceError:
                raise filtered_error from None

    def _start_sniffer(self, bpf_filter):
        from scapy.sendrecv import AsyncSniffer

        _ensure_scapy_layers()

        kwargs = {
            "iface": self._interface or None,
            "prn": self._on_packet,
            "store": False,
        }
        if bpf_filter:
            kwargs["filter"] = bpf_filter
        try:
            self._sniffer = AsyncSniffer(**kwargs)
            self._sniffer.start()
            time.sleep(0.3)
            # Surface immediate startup failures (bad interface, no privileges)
            # here, where the worker can report them, instead of dying silently.
            thread = getattr(self._sniffer, "thread", None)
            exception = getattr(self._sniffer, "exception", None)
            if exception is not None or (thread is not None and not thread.is_alive()):
                self.close()
                raise TrafficSourceError(
                    "Packet capture could not start on this interface. Check that "
                    "Npcap is installed and AlgoGuard is running with the "
                    f"required privileges. Details: {exception or 'capture thread exited'}"
                )
        except TrafficSourceError:
            raise
        except Exception as error:
            self.close()
            raise TrafficSourceError(
                "Packet capture could not start. Check that Npcap is installed "
                f"and the interface name is valid. Details: {error}"
            ) from error

    def next_event(self, timeout=0.2):
        if self._sniffer is not None:
            exception = getattr(self._sniffer, "exception", None)
            thread = getattr(self._sniffer, "thread", None)
            if exception is not None or (thread is not None and not thread.is_alive()):
                raise TrafficSourceError(
                    "Packet capture stopped unexpectedly. Check the interface and "
                    f"restart monitoring. Details: {exception or 'capture thread exited'}"
                )
        if self._pending:
            self.flows_emitted += 1
            return self._pending.pop(0)

        try:
            packet = self._packets.get(timeout=timeout)
        except queue.Empty:
            now = time.time()
            if now - self._last_idle_check >= 1.0:
                self._last_idle_check = now
                self._pending.extend(_flow_event(flow) for flow in self._tracker.expire_idle(now))
            return None

        info = packet_info_from_scapy(packet)
        if info is not None and self._is_own_traffic(info):
            # The BPF filter normally keeps these out; this catches them when
            # the driver could not compile it and capture ran unfiltered.
            with self._lock:
                self.packets_excluded += 1
            info = None
        if info is not None:
            with self._lock:
                self.packets_captured += 1
            self._pending.extend(_flow_event(flow) for flow in self._tracker.add_packet(info))
        else:
            # Ignored packets still advance capture time. Otherwise a steady
            # stream of excluded traffic prevents quiet flows from expiring.
            self._pending.extend(
                _flow_event(flow) for flow in self._tracker.expire_idle(float(packet.time))
            )
        return self.next_event(timeout=0) if self._pending else None

    def close(self):
        if self._sniffer is not None:
            try:
                self._sniffer.stop()
            except Exception:
                pass
            self._sniffer = None

    def stats(self):
        with self._lock:
            return {
                "packets": self.packets_captured,
                "dropped": self.packets_dropped,
                "excluded": self.packets_excluded,
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


BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CAPTURE_FOLDER = os.path.join(BASE_DIR, "captures")


def list_capture_files(folder=None):
    """List replayable .pcap/.pcapng recordings from the captures folder."""
    target = folder or CAPTURE_FOLDER
    if not os.path.isdir(target):
        return []
    names = [
        name
        for name in sorted(os.listdir(target))
        if name.lower().endswith((".pcap", ".pcapng", ".cap"))
    ]
    return [{"value": name, "label": name} for name in names]
