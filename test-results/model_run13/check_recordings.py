"""Training run 13, step 4: did Chrome browse while a recording was made? Counts only.

    python test-results/model_run13/check_recordings.py <recording.pcap> [...]

Prints, per recording:
- packets;
- HTTPS connections opened (TCP SYN to port 443);
- QUIC packets (UDP port 443);
- bytes on port 443;
- TLS hellos that name a test site's host;
- the test sites that were tried but never answered.

A test host name in plain text, sent to port 443, can only be a TLS hello. Hellos that are split
differently, encrypted (ECH) or sent over QUIC are not counted, so the hello figures are lower
bounds.

"Never answered" means that connection attempts went to the site's current IPv4 addresses and
no SYN-ACK came back. The addresses are looked up when the check runs, so a site that has since
moved shows nothing.

No addresses and no other host names are printed, and nothing is scored. Written on 2026-11-18,
after the first pair of fresh recordings turned out to hold almost no page loads
(fresh_recordings_note.md).
"""
import socket
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "test-results" / "def_fixes_20261023"))
sys.path.insert(0, str(ROOT / "test-results" / "iter6_6a"))

from capture_browsing import UNSEEN_SITES  # noqa: E402
from scapy.layers import inet, l2  # noqa: E402,F401  (decoders, as the app loads them)
from scapy.utils import PcapReader  # noqa: E402
from traffic_gen import SITES  # noqa: E402

HOSTS = {"generator": [urlsplit(url).hostname.encode() for url in SITES],
         "unseen": [urlsplit(url).hostname.encode() for url in UNSEEN_SITES]}


def site_addresses():
    """Each test host's current IPv4 addresses (empty when the name does not resolve)."""
    found = {}
    for hosts in HOSTS.values():
        for host in hosts:
            try:
                infos = socket.getaddrinfo(host.decode(), 443, socket.AF_INET)
            except OSError:
                infos = []
            found[host.decode()] = {info[4][0] for info in infos}
    return found


def check(path, addresses):
    counts = {"packets": 0, "https_connections": 0, "quic_packets": 0, "port_443_bytes": 0,
              "hellos_generator": 0, "hellos_unseen": 0}
    owner = {address: host for host, found in addresses.items() for address in found}
    tried = dict.fromkeys(addresses, 0)
    answered = dict.fromkeys(addresses, 0)
    with PcapReader(str(path)) as reader:
        for packet in reader:
            counts["packets"] += 1
            ip = packet.getlayer(inet.IP)
            tcp, udp = packet.getlayer(inet.TCP), packet.getlayer(inet.UDP)
            if udp is not None and 443 in (udp.sport, udp.dport):
                counts["quic_packets"] += 1
                counts["port_443_bytes"] += len(packet)
            if tcp is None or 443 not in (tcp.sport, tcp.dport):
                continue
            counts["port_443_bytes"] += len(packet)
            if tcp.sport == 443 and tcp.flags.S and tcp.flags.A and ip.src in owner:
                answered[owner[ip.src]] += 1
            if tcp.dport != 443:
                continue
            if tcp.flags.S and not tcp.flags.A:
                counts["https_connections"] += 1
                if ip.dst in owner:
                    tried[owner[ip.dst]] += 1
            payload = bytes(tcp.payload)
            for group, hosts in HOSTS.items():
                if any(host in payload for host in hosts):
                    counts[f"hellos_{group}"] += 1
    counts["never_answered"] = sorted(host for host in tried if tried[host] and not answered[host])
    return counts


def main():
    addresses = site_addresses()
    for name in sys.argv[1:]:
        counts = check(Path(name), addresses)
        print(f"{Path(name).name}: {counts['packets']:,} packets, "
              f"{counts['https_connections']} HTTPS connections, "
              f"{counts['quic_packets']:,} QUIC packets, "
              f"{counts['port_443_bytes'] / 1e6:.2f} MB on port 443, TLS hellos naming a "
              f"generator site {counts['hellos_generator']}, an unseen site "
              f"{counts['hellos_unseen']}; tried but never answered: "
              f"{', '.join(counts['never_answered']) or 'none'}")


if __name__ == "__main__":
    main()
