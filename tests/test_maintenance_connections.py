import socket
import sqlite3
import ssl
import time

import pytest

from maintenance_connections import DatabaseRelay, SharedConnections
from tests.test_cloud_connections import tls_server  # noqa: F401 - shared TLS fixture


def test_shared_attribution_is_exact_and_expires(tmp_path):
    writer = SharedConnections(tmp_path)
    reader = SharedConnections(tmp_path)
    assert not reader.matches("tcp", "10.0.0.1", 51000, "203.0.113.2", 6543)
    lease = writer.add("10.0.0.1", 51000, "203.0.113.2", 6543)
    try:
        assert reader.matches("tcp", "10.0.0.1", 51000, "203.0.113.2", 6543)
        assert reader.matches("tcp", "203.0.113.2", 6543, "10.0.0.1", 51000)
        assert not reader.matches("tcp", "10.0.0.1", 51001, "203.0.113.2", 6543)
        assert not reader.matches("udp", "10.0.0.1", 51000, "203.0.113.2", 6543)
        with sqlite3.connect(writer.path) as conn:
            conn.execute("update lease set expires=? where id=?", (time.time() - 1, lease))
        assert not reader.matches("tcp", "10.0.0.1", 51000, "203.0.113.2", 6543)
    finally:
        reader.close()


def test_relay_preserves_end_to_end_tls_and_hostname_verification(
        tmp_path, tls_server):  # noqa: F811 - pytest injects the imported fixture
    port, certificate = tls_server
    registry = SharedConnections(tmp_path)
    relay = DatabaseRelay("127.0.0.1", port, registry)
    context = ssl.create_default_context(cafile=certificate)
    try:
        with socket.create_connection(("127.0.0.1", relay.local_port), timeout=5) as raw:
            with context.wrap_socket(raw, server_hostname="127.0.0.1") as secured:
                secured.sendall(b"GET / HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n")
                chunks = []
                while data := secured.recv(4096):
                    chunks.append(data)
                assert b'{"ok": true}' in b"".join(chunks)
        with socket.create_connection(("127.0.0.1", relay.local_port), timeout=5) as raw:
            with pytest.raises(ssl.SSLCertVerificationError):
                context.wrap_socket(raw, server_hostname="wrong-host.invalid")
    finally:
        relay.close()


def test_relay_cannot_start_when_attribution_cannot_be_saved(tmp_path, monkeypatch):
    registry = SharedConnections(tmp_path)

    def refused(*args):
        raise OSError("disk unavailable")

    monkeypatch.setattr(registry, "add", refused)
    with pytest.raises(OSError, match="disk unavailable"):
        DatabaseRelay("127.0.0.1", 5432, registry)
