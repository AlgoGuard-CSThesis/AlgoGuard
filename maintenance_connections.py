"""Narrow cross-process attribution for monitored maintenance database sockets.

A byte-transparent loopback relay binds and records the outbound socket before
connect(), including its SYN. libpq still negotiates TLS with the original
server and verifies the original hostname; the relay never interprets credentials.
Only exact connections and this relay's loopback listener are excluded. The
owner-restricted registry carries no credentials and is shared by installations
using the same state directory. No database driver is imported by capture code.
"""

from __future__ import annotations

import select
import socket
import sqlite3
import threading
import time
from pathlib import Path
from uuid import uuid4

LEASE_SECONDS = 120
MAX_LEASES = 4096


class SharedConnections:
    def __init__(self, state_dir):
        self.path = Path(state_dir) / "connections" / "leases.sqlite3"
        self.lock = threading.RLock()
        self.reader = None

    def _write(self, callback):
        from cloud_outbox import restrict_owner

        if not self.path.parent.exists():
            restrict_owner(self.path.parent)
        with sqlite3.connect(self.path, timeout=2) as conn:
            conn.execute("pragma journal_mode=WAL")
            conn.execute("create table if not exists lease (id text primary key, "
                         "local_ip text, local_port integer, remote_ip text, "
                         "remote_port integer, expires real)")
            conn.execute("delete from lease where expires < ?", (time.time(),))
            return callback(conn)

    def add(self, local_ip, local_port, remote_ip="", remote_port=0):
        identity = str(uuid4())

        def insert(conn):
            if conn.execute("select count(*) from lease").fetchone()[0] >= MAX_LEASES:
                raise OSError("Maintenance connection registry is full.")
            conn.execute("insert into lease values(?,?,?,?,?,?)", (
                identity, local_ip, local_port, remote_ip, remote_port,
                time.time() + LEASE_SECONDS))
        self._write(insert)
        return identity

    def renew(self, identities):
        self._write(lambda conn: conn.executemany(
            "update lease set expires=? where id=?",
            [(time.time() + LEASE_SECONDS, identity) for identity in identities]))

    def matches(self, protocol, src, sport, dst, dport):
        if protocol.lower() != "tcp" or not self.path.exists():
            return False
        # Reads have no polling delay: the writer commits before sending SYN.
        with self.lock:
            if self.reader is None:
                self.reader = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True,
                                              timeout=2, check_same_thread=False)
            query = (
                "select 1 from lease where expires >= ? and ("
                "(local_port=? and (local_ip=? or local_ip in ('0.0.0.0','::')) "
                "and remote_ip=? and remote_port=?) or "
                "(local_port=? and (local_ip=? or local_ip in ('0.0.0.0','::')) "
                "and remote_ip=? and remote_port=?) or "
                "(remote_port=0 and ((local_ip=? and local_port=?) or "
                "(local_ip=? and local_port=?)))) limit 1")
            try:
                row = self.reader.execute(query,
                    (time.time(), sport, src, dst, dport, dport, dst, src, sport,
                     src, sport, dst, dport)).fetchone()
            except sqlite3.OperationalError as error:
                if "no such table" in str(error):
                    # No maintenance SYN can precede the first schema commit.
                    return False
                raise
            return row is not None

    def close(self):
        with self.lock:
            if self.reader is not None:
                self.reader.close()
                self.reader = None


class DatabaseRelay:
    """Bounded transparent relay; the original database TLS remains end to end."""

    def __init__(self, host, port, registry):
        self.host, self.port, self.registry = host, port, registry
        self.stop = threading.Event()
        self.slots = threading.BoundedSemaphore(8)
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.local_port = self.listener.getsockname()[1]
        try:
            self.lease = registry.add("127.0.0.1", self.local_port)
            self.listener.listen(8)
        except Exception:
            self.listener.close()
            raise
        self.listener.settimeout(0.2)
        self.thread = threading.Thread(target=self._accept, daemon=True)
        self.thread.start()

    def _accept(self):
        renewed = time.monotonic()
        try:
            while not self.stop.is_set():
                if time.monotonic() - renewed >= 15:
                    self.registry.renew([self.lease])
                    renewed = time.monotonic()
                try:
                    client, _ = self.listener.accept()
                except socket.timeout:
                    continue
                if not self.slots.acquire(blocking=False):
                    client.close()
                    continue
                threading.Thread(target=self._forward, args=(client,), daemon=True).start()
        except (OSError, sqlite3.Error):
            self.stop.set()  # Attribution failure ends the relay, never bypasses it.

    def _forward(self, client):
        remote = None
        leases = []
        try:
            for family, kind, proto, _, address in socket.getaddrinfo(
                    self.host, self.port, type=socket.SOCK_STREAM):
                remote = socket.socket(family, kind, proto)
                remote.settimeout(10)
                wildcard = "::" if family == socket.AF_INET6 else "0.0.0.0"
                remote.bind((wildcard, 0))
                leases.append(self.registry.add(wildcard, remote.getsockname()[1],
                                                address[0], address[1]))
                try:
                    remote.connect(address)
                    break
                except OSError:
                    remote.close()
                    remote = None
            if remote is None:
                return
            client.settimeout(2)
            remote.settimeout(2)
            renewed = time.monotonic()
            while not self.stop.is_set():
                if time.monotonic() - renewed >= 15:
                    self.registry.renew(leases)
                    renewed = time.monotonic()
                readable, _, _ = select.select([client, remote], [], [], 0.2)
                for stream in readable:
                    data = stream.recv(65536)
                    if not data:
                        return
                    (remote if stream is client else client).sendall(data)
        except (OSError, sqlite3.Error):
            pass  # The caller receives a normal connection error without credentials.
        finally:
            client.close()
            if remote is not None:
                remote.close()
            # Retain the exact tuple for late FIN/RST and retransmitted packets.
            if leases:
                try:
                    self.registry.renew(leases)
                except (OSError, sqlite3.Error):
                    pass
            self.slots.release()

    def close(self):
        self.stop.set()
        self.listener.close()
        self.thread.join(timeout=0.5)


def connect_database(dsn, **kwargs):
    """Maintenance-only libpq connection with cross-process capture attribution."""
    import psycopg2
    from psycopg2.extensions import parse_dsn

    from config import get_config

    params = {**parse_dsn(dsn), **kwargs}
    host = params.get("hostaddr") or params.get("host", "localhost")
    if "," in host or str(host).startswith("/"):
        raise ValueError("Monitored maintenance needs one TCP database host.")
    port = int(params.get("port", 5432))
    # A DNS hostname remains `host` so verify-full keeps checking that name.
    # `hostaddr` selects only the relay's transport address.
    params.setdefault("host", host)
    registry = SharedConnections(get_config().state_dir)
    relay = DatabaseRelay(host, port, registry)

    class MonitoredConnection(psycopg2.extensions.connection):
        def close(self):
            try:
                super().close()
            finally:
                relay.close()

    params.update(hostaddr="127.0.0.1", port=relay.local_port,
                  connection_factory=MonitoredConnection)
    try:
        return psycopg2.connect(**params)
    except Exception:
        relay.close()
        raise
