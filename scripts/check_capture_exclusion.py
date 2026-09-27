"""Stage 5D.4 evidence: AlgoGuard's own cloud connections are excluded from capture,
while unrelated HTTPS to the same provider remains visible.

Run on the Windows pilot machine, from an Administrator terminal with Npcap:

    python scripts/check_capture_exclusion.py --interface "Wi-Fi" --requests 5

It reads only NEXT_PUBLIC_SUPABASE_URL from .env and sends unauthenticated GET
requests for the project's public JWKS document: half through AlgoGuard's owned
opener (the path every app request uses), half over plain unregistered sockets
to the same host and port. It prints a JSON report and exits 0 only when every
owned connection was excluded and at least one unrelated connection was
classified. Record the report in docs/migration/05d-integration.md.
"""

from __future__ import annotations

import argparse
import json
import socket
import ssl
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def summarize(flows, owned_ports, other_ports, remote_ips, stats):
    """Classify captured flows by which local socket produced them."""

    def local_port(flow):
        if flow.get("destination_ip") in remote_ips:
            return flow.get("source_port")
        if flow.get("source_ip") in remote_ips:
            return flow.get("destination_port")
        return None

    provider = [flow for flow in flows if local_port(flow) is not None]
    owned_visible = [flow for flow in provider if local_port(flow) in owned_ports]
    other_visible = [flow for flow in provider if local_port(flow) in other_ports]
    dns = [flow for flow in flows
           if 53 in (flow.get("source_port"), flow.get("destination_port"))]
    report = {
        "owned_connections": len(owned_ports),
        "unrelated_connections": len(other_ports),
        "packets_captured": stats.get("packets", 0),
        "packets_excluded_total": stats.get("excluded", 0),
        "packets_excluded_cloud": stats.get("excluded_cloud", 0),
        "packets_dropped": stats.get("dropped", 0),
        "owned_flows_classified": len(owned_visible),
        "unrelated_flows_classified": len(other_visible),
        "other_provider_flows_classified": len(provider) - len(owned_visible)
        - len(other_visible),
        "dns_flows_classified": len(dns),
    }
    report["passed"] = bool(
        report["packets_excluded_cloud"] > 0
        and report["owned_flows_classified"] == 0
        and report["unrelated_flows_classified"] >= 1
    )
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--interface", default=None, help="capture interface (default: Npcap's)")
    parser.add_argument("--requests", type=int, default=5)
    parser.add_argument("--settle", type=float, default=20.0,
                        help="seconds to wait for flows to close (idle timeout is 15 s)")
    args = parser.parse_args(argv)

    from cloud_connections import REGISTRY, owned_opener
    from config import get_config
    from services.traffic_source_service import LiveCaptureSource, live_capture_available
    from token_verification import jwks_url_for

    url = get_config().supabase_url
    if not url:
        parser.error("Set NEXT_PUBLIC_SUPABASE_URL in .env first.")
    available, reason = live_capture_available()
    if not available:
        parser.error(reason)
    parts = urlsplit(url)
    host = parts.hostname
    port = parts.port or (443 if parts.scheme == "https" else 80)
    target = jwks_url_for(url)
    remote_ips = {item[4][0] for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)}

    source = LiveCaptureSource(args.interface)
    source.prepare()
    flows = []

    def drain(seconds):
        end = time.time() + seconds
        while time.time() < end:
            event = source.next_event(timeout=0.2)
            if event is not None:
                flows.append(event)

    owned_ports, other_ports = set(), set()
    try:
        drain(1.0)
        for _ in range(max(1, args.requests)):
            before = set(REGISTRY._entries)
            with owned_opener().open(target, timeout=10) as response:
                response.read()
            owned_ports |= {key[0] for key in set(REGISTRY._entries) - before}
            raw = socket.create_connection((host, port), timeout=10)
            other_ports.add(raw.getsockname()[1])
            stream = (ssl.create_default_context().wrap_socket(raw, server_hostname=host)
                      if parts.scheme == "https" else raw)
            request = (f"GET {urlsplit(target).path} HTTP/1.1\r\nHost: {host}\r\n"
                       "Connection: close\r\n\r\n").encode()
            stream.sendall(request)
            while stream.recv(65536):
                pass
            stream.close()
            drain(0.5)
        drain(args.settle)
    finally:
        stats = source.stats()
        source.close()
    report = summarize(flows, owned_ports, other_ports, remote_ips, stats)
    report.update(interface=args.interface or "default", host=host, remote_ips=sorted(remote_ips),
                  attribution="bind-before-connect registration of AlgoGuard's own sockets")
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
