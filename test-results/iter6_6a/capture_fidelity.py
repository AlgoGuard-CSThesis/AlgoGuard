"""Stage 6A.1: record a reference capture of generated web traffic only (protocol_6a1.md).

Resolves the test sites, captures only traffic to them, DNS and ICMP on the Wi-Fi adapter while
traffic_gen.py runs and the hosts are pinged, and writes a pcap outside git.

    python test-results/iter6_6a/capture_fidelity.py --seconds 120 --out <file.pcap>
"""
import argparse
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "test-results" / "def_fixes_20261023"))

from scapy.sendrecv import AsyncSniffer  # noqa: E402
from scapy.utils import wrpcap  # noqa: E402
from traffic_gen import SITES  # noqa: E402

from services.traffic_source_service import list_capture_interfaces  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=int, default=120)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    hosts = sorted({url.split("/")[2] for url in SITES})
    addresses = sorted({info[4][0] for host in hosts
                        for info in socket.getaddrinfo(host, 443, socket.AF_INET)})
    bpf = "(" + " or ".join(f"host {a}" for a in addresses) + ") or udp port 53 or icmp"
    interface = next(i for i in list_capture_interfaces() if "wi-fi 6" in i["label"].lower())
    sniffer = AsyncSniffer(iface=interface["value"], filter=bpf, store=True)
    sniffer.start()
    time.sleep(1)
    generator = subprocess.Popen([sys.executable, str(ROOT / "test-results/def_fixes_20261023/"
                                                      "traffic_gen.py"), "--seconds",
                                  str(args.seconds)])
    for host in hosts[:4]:
        subprocess.run(["ping", "-n", "3", host], capture_output=True)
    generator.wait()
    time.sleep(20)  # let idle flows end inside the capture
    packets = sniffer.stop()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    wrpcap(str(args.out), packets)
    print(f"{len(packets)} packets, {len(addresses)} site addresses -> {args.out}")


if __name__ == "__main__":
    main()
