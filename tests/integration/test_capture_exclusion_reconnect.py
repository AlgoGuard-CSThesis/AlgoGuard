"""Stage 6C.4 on Windows with Npcap: AlgoGuard's own connections stay excluded across reconnects
and a DNS change, while unrelated connections to the same addresses are still captured.

The project host name is made to resolve to 127.0.0.1 and then, after the "DNS change", to
127.0.0.2; both reach the local stack. Each owned request opens a new connection (a reconnect).
Unrelated plain sockets go to the same addresses and port. Run with RUN_WINDOWS_CAPTURE_TESTS=1.
"""

import os
import socket
import time
from urllib.parse import urlsplit

import pytest

from cloud_connections import REGISTRY, owned_opener

pytestmark = pytest.mark.integration
HOST = "algoguard-dns-change.test"


def test_owned_connections_stay_excluded_after_reconnects_and_a_dns_change(local_stack,
                                                                          monkeypatch):
    if os.environ.get("RUN_WINDOWS_CAPTURE_TESTS") != "1":
        pytest.skip("Set RUN_WINDOWS_CAPTURE_TESTS=1 on Windows with Npcap.")
    from services.traffic_source_service import LiveCaptureSource

    port = urlsplit(local_stack["api_url"]).port
    resolved = {"address": "127.0.0.1"}
    original = socket.getaddrinfo

    def resolver(host, *args, **kwargs):
        if host == HOST:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (resolved["address"], port))]
        return original(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", resolver)
    source = LiveCaptureSource(r"\Device\NPF_Loopback")
    source.prepare()
    flows, owned, unrelated = [], set(), set()

    def drain(seconds):
        end = time.time() + seconds
        while time.time() < end:
            event = source.next_event(timeout=0.2)
            if event is not None:
                flows.append(event)

    try:
        drain(1.0)
        for address in ("127.0.0.1", "127.0.0.2"):
            resolved["address"] = address  # the DNS change
            for _ in range(3):
                before = set(REGISTRY._entries)
                with owned_opener().open(f"http://{HOST}:{port}/auth/v1/.well-known/jwks.json",
                                         timeout=10) as response:
                    assert response.status == 200
                    response.read()
                owned |= {(key[0], key[1]) for key in set(REGISTRY._entries) - before}
                with socket.create_connection((address, port), timeout=10) as plain:
                    unrelated.add((plain.getsockname()[1], address))
                    plain.sendall(b"GET /auth/v1/.well-known/jwks.json HTTP/1.1\r\nHost: "
                                  + f"127.0.0.1:{port}".encode() + b"\r\nConnection: close\r\n\r\n")
                    while plain.recv(65536):
                        pass
                drain(0.5)
        drain(5.0)
    finally:
        stats = source.stats()
        source.close()

    def endpoint(flow):
        """(local port, remote address) of a flow to the local stack's port."""
        if flow.get("destination_port") == port:
            return flow.get("source_port"), flow.get("destination_ip")
        if flow.get("source_port") == port:
            return flow.get("destination_port"), flow.get("source_ip")
        return None

    seen = {pair for pair in map(endpoint, flows) if pair is not None}
    assert {address for _, address in owned} == {"127.0.0.1", "127.0.0.2"}
    assert len(owned) == 6  # every request was a new, registered connection
    assert not seen & owned, "an owned connection was classified"
    assert {address for _, address in seen & unrelated} == {"127.0.0.1", "127.0.0.2"}
    assert stats["excluded_cloud"] > 0 and stats["dropped"] == 0
    print({"owned": len(owned), "unrelated_seen": len(seen & unrelated), **stats})
