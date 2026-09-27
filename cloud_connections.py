"""AlgoGuard's own cloud sockets, registered before they send a packet (Stage 5D.4).

Live capture must not classify the application's own Supabase API, Auth, and
Storage traffic, or the monitor would feed on its own uploads. Excluding "all
HTTPS" or the provider's shared IP addresses would also hide unrelated traffic
that an analyst needs to see, so exclusion is by exact TCP connection instead.

Attribution method: every HTTP(S) connection the analyst application opens goes
through :func:`owned_opener`. Its connection factory binds the socket to an
ephemeral local port *before* ``connect()``, records ``(local port, remote IP,
remote port)`` here, and only then connects. The SYN is therefore sent after the
registration exists, so there is no startup race. A reconnect or a DNS change
creates a new socket and a new registration by the same path.

A closed socket stays registered for ``retention`` seconds so late FIN, RST,
and retransmitted segments are still recognised. Closure is observed lazily:
the entry holds a weak reference to the object that owns the file descriptor,
and a descriptor of -1 (or a collected object) starts the retention clock.

This does not use a Windows process lookup, so it needs no extra privileges and
has no polling interval. Its limits are recorded in
``docs/migration/05d-integration.md``: DNS lookups are performed by the
operating system resolver and remain visible, sockets opened outside this
opener (none on the analyst path) are not covered, and maintainer tools run in
their own processes and are deliberately left visible.
"""

from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
import threading
import time
import urllib.request
import weakref
from dataclasses import dataclass

RETENTION_SECONDS = 120.0  # Bounded late-packet retention, independent of OS TCP timers.
MAX_ENTRIES = 4096
_DEFAULT_TIMEOUT = getattr(socket, "_GLOBAL_DEFAULT_TIMEOUT")


def _canonical_ip(value):
    try:
        address = ipaddress.ip_address(str(value).split("%", 1)[0])
    except ValueError:
        return str(value)
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return address.compressed


@dataclass
class _Entry:
    owner: object
    registered_at: float
    closed_at: float | None = None


class OwnedConnections:
    """Bounded, thread-safe set of the application's own TCP connections."""

    def __init__(self, *, retention=RETENTION_SECONDS, limit=MAX_ENTRIES, clock=time.monotonic):
        self.retention = float(retention)
        self.limit = int(limit)
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: dict[tuple[int, str, int], _Entry] = {}
        self.registered_total = 0
        self.evicted = 0

    @staticmethod
    def _key(local_port, remote_ip, remote_port):
        return int(local_port), _canonical_ip(remote_ip), int(remote_port)

    def register(self, local_port, remote_ip, remote_port, owner=None):
        key = self._key(local_port, remote_ip, remote_port)
        reference = weakref.ref(owner) if owner is not None else None
        with self._lock:
            self._prune_locked()
            if key not in self._entries and len(self._entries) >= self.limit:
                oldest = min(self._entries, key=lambda item: self._entries[item].registered_at)
                del self._entries[oldest]
                self.evicted += 1
            self._entries[key] = _Entry(reference, self._clock())
            self.registered_total += 1
        return key

    def adopt(self, key, owner):
        """Point an entry at the object that now owns its descriptor (e.g. TLS)."""
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                entry.owner = weakref.ref(owner)
                entry.closed_at = None

    def release(self, key):
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None and entry.closed_at is None:
                entry.owner = None
                entry.closed_at = self._clock()

    def _live_locked(self, entry, now):
        owner = entry.owner() if entry.owner is not None else None
        if owner is not None:
            try:
                if owner.fileno() != -1:
                    entry.closed_at = None
                    return True
            except (OSError, ValueError):
                pass
        if entry.closed_at is None:
            entry.closed_at = now
        return now - entry.closed_at <= self.retention

    def _prune_locked(self):
        now = self._clock()
        expired = [key for key, entry in self._entries.items() if not self._live_locked(entry, now)]
        for key in expired:
            del self._entries[key]

    def matches(self, protocol, source_ip, source_port, destination_ip, destination_port):
        """True only for a packet on one of AlgoGuard's own TCP connections."""
        if str(protocol).lower() != "tcp":
            return False
        try:
            outbound = self._key(source_port, destination_ip, destination_port)
            inbound = self._key(destination_port, source_ip, source_port)
        except (TypeError, ValueError):
            return False
        with self._lock:
            now = self._clock()
            for key in (outbound, inbound):
                entry = self._entries.get(key)
                if entry is not None:
                    if self._live_locked(entry, now):
                        return True
                    del self._entries[key]
        return False

    def stats(self):
        with self._lock:
            self._prune_locked()
            active = sum(1 for entry in self._entries.values() if entry.closed_at is None)
            return {
                "registered_total": self.registered_total,
                "active": active,
                "retained": len(self._entries) - active,
                "evicted": self.evicted,
            }

    def clear(self):
        with self._lock:
            self._entries.clear()

    def create_connection(
        self,
        address,
        timeout=_DEFAULT_TIMEOUT,
        source_address=None,
    ):
        """``socket.create_connection`` that registers the socket before connecting."""
        host, port = address
        errors = []
        for family, kind, proto, _, target in socket.getaddrinfo(
            host, port, 0, socket.SOCK_STREAM
        ):
            sock = None
            key = None
            try:
                sock = socket.socket(family, kind, proto)
                if timeout is not _DEFAULT_TIMEOUT:
                    sock.settimeout(timeout)
                wildcard = "::" if family == socket.AF_INET6 else "0.0.0.0"
                sock.bind(source_address or (wildcard, 0))
                key = self.register(sock.getsockname()[1], target[0], target[1], owner=sock)
                sock.connect(target)
                return sock
            except OSError as error:
                errors.append(error)
                if key is not None:
                    self.release(key)
                if sock is not None:
                    sock.close()
        if not errors:
            raise OSError("getaddrinfo returned no addresses")
        raise errors[-1]


REGISTRY = OwnedConnections()


def _adopt_tls_socket(registry, sock):
    try:
        local = sock.getsockname()
        peer = sock.getpeername()
    except OSError:
        return
    registry.adopt(registry._key(local[1], peer[0], peer[1]), sock)


class _OwnedHTTPConnection(http.client.HTTPConnection):
    registry = REGISTRY

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = self.registry.create_connection


class _OwnedHTTPSConnection(http.client.HTTPSConnection):
    registry = REGISTRY

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = self.registry.create_connection

    def connect(self):
        super().connect()
        # TLS wrapping moves the descriptor to a new SSLSocket object.
        _adopt_tls_socket(self.registry, self.sock)


class _OwnedHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_OwnedHTTPConnection, req)


class _OwnedHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self):
        self._tls_context = ssl.create_default_context()
        super().__init__(context=self._tls_context)

    def https_open(self, req):
        return self.do_open(_OwnedHTTPSConnection, req, context=self._tls_context)


def owned_opener(*handlers):
    """A proxy-free urllib opener whose every socket is registered in REGISTRY."""
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}), _OwnedHTTPHandler(), _OwnedHTTPSHandler(), *handlers
    )
