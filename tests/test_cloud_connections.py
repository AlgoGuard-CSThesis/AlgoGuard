"""Stage 5D.4: AlgoGuard's own cloud connections are excluded narrowly."""

import http.server
import json
import socket
import threading
import urllib.request

import pytest

import cloud_connections
import model_delivery
from cloud_connections import REGISTRY, OwnedConnections, owned_opener
from cloud_repository import CloudRepository, UserNodeContext
from services import traffic_source_service as traffic_sources
from services.flow_tracker_service import PacketInfo


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture()
def json_server():
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - http.server API
            body = json.dumps({"keys": []}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture(autouse=True)
def clean_registry():
    REGISTRY.clear()
    yield
    REGISTRY.clear()


def test_socket_is_registered_before_its_first_packet(json_server):
    registry = OwnedConnections()
    seen = []
    original = registry.register

    def register(local_port, remote_ip, remote_port, owner=None):
        # Not connected yet: getpeername fails, so no SYN has been sent.
        with pytest.raises(OSError):
            assert owner is not None
            owner.getpeername()
        seen.append((local_port, remote_ip, remote_port))
        return original(local_port, remote_ip, remote_port, owner=owner)

    registry.register = register
    sock = registry.create_connection(("127.0.0.1", json_server), timeout=5)
    try:
        local_port = sock.getsockname()[1]
        assert seen == [(local_port, "127.0.0.1", json_server)]
        assert registry.matches("tcp", "127.0.0.1", local_port, "127.0.0.1", json_server)
        assert registry.matches("TCP", "127.0.0.1", json_server, "127.0.0.1", local_port)
    finally:
        sock.close()


def test_match_is_exact_connection_not_provider_or_protocol():
    registry = OwnedConnections()
    registry.register(51000, "203.0.113.10", 443, owner=socket.socket())
    assert registry.matches("tcp", "192.168.1.5", 51000, "203.0.113.10", 443)
    assert registry.matches("tcp", "203.0.113.10", 443, "192.168.1.5", 51000)
    # Unrelated HTTPS to the same provider address stays visible.
    assert not registry.matches("tcp", "192.168.1.5", 51001, "203.0.113.10", 443)
    assert not registry.matches("tcp", "192.168.1.5", 51000, "203.0.113.11", 443)
    assert not registry.matches("udp", "192.168.1.5", 51000, "203.0.113.10", 443)
    assert not registry.matches("tcp", "192.168.1.5", "bad", "203.0.113.10", 443)


def test_closed_connection_is_retained_for_late_segments_then_forgotten():
    clock = FakeClock()
    registry = OwnedConnections(retention=120, clock=clock)
    owner = socket.socket()
    registry.register(52000, "198.51.100.7", 443, owner=owner)
    assert registry.stats()["active"] == 1
    owner.close()
    # Closure is observed lazily (here by stats); retention starts then.
    assert registry.stats()["retained"] == 1
    clock.now += 119
    assert registry.matches("tcp", "10.0.0.2", 52000, "198.51.100.7", 443)
    assert registry.stats()["retained"] == 1
    clock.now += 2
    assert not registry.matches("tcp", "10.0.0.2", 52000, "198.51.100.7", 443)
    assert registry.stats() == {
        "registered_total": 1, "active": 0, "retained": 0, "evicted": 0,
    }


def test_registry_is_bounded():
    registry = OwnedConnections(limit=2)
    owners = [socket.socket() for _ in range(3)]
    try:
        for port, owner in zip((1, 2, 3), owners):
            registry.register(port, "192.0.2.1", 443, owner=owner)
        assert registry.stats()["evicted"] == 1
        assert not registry.matches("tcp", "10.0.0.1", 1, "192.0.2.1", 443)
        assert registry.matches("tcp", "10.0.0.1", 3, "192.0.2.1", 443)
    finally:
        for owner in owners:
            owner.close()


def test_owned_opener_registers_every_request_and_releases_after_close(json_server):
    url = f"http://127.0.0.1:{json_server}/keys"
    before = REGISTRY.registered_total
    with owned_opener().open(url, timeout=5) as response:
        (key,) = list(REGISTRY._entries)
        assert key[1:] == ("127.0.0.1", json_server)
        assert REGISTRY.stats()["active"] == 1  # descriptor held by the unread response
        assert REGISTRY.matches("tcp", "127.0.0.1", key[0], "127.0.0.1", json_server)
        assert json.loads(response.read()) == {"keys": []}
    stats = REGISTRY.stats()
    assert REGISTRY.registered_total - before == 1
    assert stats["active"] == 0 and stats["retained"] == 1
    assert REGISTRY.matches("tcp", "127.0.0.1", json_server, "127.0.0.1", key[0])
    # A second request is a new socket and a new registration (reconnect path).
    owned_opener().open(url, timeout=5).close()
    assert REGISTRY.registered_total - before == 2


def test_jwks_fetch_uses_the_owned_opener(json_server):
    from token_verification import _fetch_json

    before = REGISTRY.registered_total
    assert _fetch_json(f"http://127.0.0.1:{json_server}/jwks", 5) == {"keys": []}
    assert REGISTRY.registered_total - before == 1


def test_repository_and_model_download_never_use_unregistered_sockets():
    from uuid import uuid4

    context = UserNodeContext(uuid4(), 1, uuid4(), "token")
    repository = CloudRepository("https://example.supabase.co", "sb_publishable_x", context)
    cache = model_delivery.ModelCache(repository, "unused")
    for opener in (repository._opener, cache._opener):
        handlers = getattr(opener, "handlers")
        kinds = {type(handler) for handler in handlers}
        assert cloud_connections._OwnedHTTPSHandler in kinds
        assert cloud_connections._OwnedHTTPHandler in kinds
        assert urllib.request.HTTPSHandler not in kinds
        assert urllib.request.HTTPHandler not in kinds
        proxies = [h for h in handlers if isinstance(h, urllib.request.ProxyHandler)]
        # Environment proxies are never consulted: any ProxyHandler is empty.
        assert all(not getattr(h, "proxies") for h in proxies)


class _Captured:
    """What the sniffer hands over; its parsed facts are supplied directly."""

    def __init__(self, info):
        self.info = info
        self.time = info.ts


def _packet(src, sport, dst, dport, proto="tcp", ts=1000.0, flags="A"):
    return _Captured(PacketInfo(ts, src, dst, sport, dport, proto, 60, 64, flags))


def test_live_capture_excludes_owned_cloud_traffic_but_keeps_other_https(monkeypatch):
    registry = OwnedConnections()
    owner = socket.socket()
    registry.register(51000, "203.0.113.10", 443, owner=owner)
    monkeypatch.setattr(traffic_sources, "packet_info_from_scapy", lambda packet: packet.info)
    source = traffic_sources.LiveCaptureSource(
        "test interface", exclude_ports={5000}, owned_connections=registry
    )
    try:
        for packet in (
            _packet("192.168.1.5", 51000, "203.0.113.10", 443, flags="S"),
            _packet("203.0.113.10", 443, "192.168.1.5", 51000, flags="SA"),
            _packet("127.0.0.1", 50123, "127.0.0.1", 5000),  # browser to AlgoGuard
            _packet("192.168.1.5", 51001, "203.0.113.10", 443, flags="S"),  # other HTTPS
            _packet("192.168.1.5", 53000, "192.168.1.1", 53, proto="udp"),  # DNS
        ):
            source._on_packet(packet)
            source.next_event(timeout=0)
        stats = source.stats()
        assert stats["excluded"] == 3
        assert stats["excluded_cloud"] == 2
        assert stats["packets"] == 2
        assert source._tracker.active_flows == 2
    finally:
        owner.close()


def test_default_live_source_uses_the_process_registry():
    source = traffic_sources.LiveCaptureSource("test interface", exclude_ports={5000})
    assert source._owned is REGISTRY


@pytest.fixture()
def tls_server(tmp_path):
    import datetime
    import ipaddress
    import ssl

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.datetime.now(datetime.timezone.utc)
    certificate = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(hours=1))
        .add_extension(x509.SubjectAlternativeName(
            [x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()))

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - http.server API
            body = b'{"ok": true}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(cert_path, key_path)
    server.socket = server_context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1], cert_path
    finally:
        server.shutdown()
        server.server_close()


def test_tls_connection_stays_registered_after_wrapping(monkeypatch, tls_server):
    import ssl

    port, cert_path = tls_server
    trusted = ssl.create_default_context(cafile=str(cert_path))
    monkeypatch.setattr(cloud_connections.ssl, "create_default_context", lambda: trusted)
    with owned_opener().open(f"https://127.0.0.1:{port}/", timeout=5) as response:
        (key,) = list(REGISTRY._entries)
        # The raw socket was detached into an SSLSocket; the entry follows it.
        assert REGISTRY._entries[key].closed_at is None
        assert REGISTRY.stats()["active"] == 1
        assert REGISTRY.matches("tcp", "127.0.0.1", key[0], "127.0.0.1", port)
        assert response.read() == b'{"ok": true}'
    assert REGISTRY.stats()["retained"] == 1


def test_windows_evidence_summary_separates_owned_and_unrelated_flows():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "scripts" / "check_capture_exclusion.py"
    spec = importlib.util.spec_from_file_location("check_capture_exclusion", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    flows = [
        {"source_ip": "192.168.1.5", "source_port": 51001, "destination_ip": "203.0.113.10",
         "destination_port": 443},
        {"source_ip": "203.0.113.10", "source_port": 443, "destination_ip": "192.168.1.5",
         "destination_port": 51002},
        {"source_ip": "192.168.1.5", "source_port": 53000, "destination_ip": "192.168.1.1",
         "destination_port": 53},
    ]
    stats = {"packets": 30, "excluded": 12, "excluded_cloud": 12, "dropped": 0}
    report = module.summarize(flows, {50000}, {51001, 51002}, {"203.0.113.10"}, stats)
    assert report["passed"] and report["unrelated_flows_classified"] == 2
    assert report["owned_flows_classified"] == 0 and report["dns_flows_classified"] == 1
    leaked = module.summarize(flows, {51001}, {51002}, {"203.0.113.10"}, stats)
    assert not leaked["passed"] and leaked["owned_flows_classified"] == 1
