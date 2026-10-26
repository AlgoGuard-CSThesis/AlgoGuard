"""DEF-19: record benign traffic from a real browser plus the PC's background traffic.

Headless Chrome, in a fresh temporary profile, loads the generator's public home pages one after
another while every IPv4 packet on the Wi-Fi adapter is recorded. The capture is written outside
git.

    python test-results/iter6_6a/capture_browsing.py --seconds 120 --out <file.pcap>
        [--sites unseen]

`--sites unseen` loads a second list of public home pages that no training capture used.
"""
import argparse
import random
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "test-results" / "def_fixes_20261023"))

from scapy.sendrecv import AsyncSniffer  # noqa: E402
from scapy.utils import wrpcap  # noqa: E402
from traffic_gen import SITES  # noqa: E402

from services.traffic_source_service import list_capture_interfaces  # noqa: E402

CHROME = Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe")
UNSEEN_SITES = [
    "https://www.bbc.com/", "https://github.com/", "https://stackoverflow.com/",
    "https://news.ycombinator.com/", "https://www.nasa.gov/", "https://www.who.int/",
    "https://www.un.org/", "https://www.cloudflare.com/", "https://www.microsoft.com/",
    "https://www.nytimes.com/", "https://www.reuters.com/", "https://www.imdb.com/",
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=int, default=120)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--sites", choices=("generator", "unseen"), default="generator")
    args = parser.parse_args()
    sites = UNSEEN_SITES if args.sites == "unseen" else SITES
    interface = next(i for i in list_capture_interfaces() if "wi-fi 6" in i["label"].lower())
    sniffer = AsyncSniffer(iface=interface["value"], filter="ip", store=True)
    sniffer.start()
    time.sleep(1)
    loads = failures = 0
    rng = random.Random(19)
    with tempfile.TemporaryDirectory() as profile:
        deadline = time.time() + args.seconds
        while time.time() < deadline:
            url = rng.choice(sites)
            try:
                subprocess.run([str(CHROME), "--headless=new", f"--user-data-dir={profile}",
                                "--disable-gpu", "--no-first-run", "--virtual-time-budget=3000",
                                "--dump-dom", url],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
                loads += 1
            except subprocess.TimeoutExpired:
                failures += 1
            time.sleep(rng.uniform(1, 4))
    time.sleep(20)  # let idle flows end inside the capture
    packets = sniffer.stop()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    wrpcap(str(args.out), packets)
    print(f"{len(packets)} packets, {loads} page loads ({failures} timed out) -> {args.out}")


if __name__ == "__main__":
    main()
