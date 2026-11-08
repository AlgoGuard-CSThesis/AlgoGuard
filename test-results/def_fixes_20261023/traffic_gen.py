"""Ordinary web traffic for the TC-11 live lag re-test: bursts of HTTPS page loads.

Every few seconds it fetches several public pages at once on fresh connections (like a browser
opening a page and its resources), so connections end by FIN or RST in bursts. Only GET
requests to public home pages; nothing is sent.

    python test-results/def_fixes_20261023/traffic_gen.py --seconds 90
"""
import argparse
import random
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

SITES = [
    "https://example.com/", "https://www.wikipedia.org/", "https://www.python.org/",
    "https://www.iana.org/", "https://www.w3.org/", "https://www.mozilla.org/",
    "https://www.debian.org/", "https://www.apache.org/", "https://www.kernel.org/",
    "https://www.gnu.org/", "https://www.rust-lang.org/", "https://www.php.net/",
]


def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": "AlgoGuard-TC11/1.0",
                                                   "Connection": "close"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            response.read(200_000)
        return True
    except Exception:
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=90.0)
    args = parser.parse_args()
    rng = random.Random(11)
    ok = failed = 0
    end = time.time() + args.seconds
    with ThreadPoolExecutor(max_workers=8) as pool:
        while time.time() < end:
            burst = rng.sample(SITES, rng.randint(3, 8))
            for result in pool.map(fetch, burst):
                ok, failed = ok + result, failed + (not result)
            time.sleep(rng.uniform(1.0, 4.0))
    print(f"requests ok {ok}, failed {failed}")


if __name__ == "__main__":
    main()
