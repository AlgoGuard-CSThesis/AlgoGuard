"""SYS-02 inputs: the runbook's synthetic PCAP (TC-04) and three bad files, written to captures/.

Run from the repository folder with the venv:  python test-results/sys02_files/make_sys02_files.py
Synthetic traffic only (documentation addresses); no real traffic is recorded.
"""
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tests.test_traffic_sources import write_sample_pcap  # noqa: E402

captures = ROOT / "captures"
captures.mkdir(exist_ok=True)
sample = captures / "cycle1-tc04-sample.pcap"
write_sample_pcap(sample, sessions=10)  # same call as 05f-two-computer-runbook.md section B

empty = captures / "cycle1-bad-empty.pcap"
empty.write_bytes(b"")
text = captures / "cycle1-bad-text.pcap"
text.write_text("This is a plain text file renamed to .pcap for SYS-02 bad-file testing.\n",
                encoding="utf-8")
truncated = captures / "cycle1-bad-truncated.pcap"
data = sample.read_bytes()
truncated.write_bytes(data[: len(data) * 6 // 10])  # cut part-way through a packet record

manifest = {"created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "files": []}
for path, purpose in ((sample, "TC-04 PCAP replay (write_sample_pcap, sessions=10)"),
                      (empty, "bad file: 0 bytes"), (text, "bad file: text renamed .pcap"),
                      (truncated, "bad file: first 60% of the sample PCAP")):
    raw = path.read_bytes()
    manifest["files"].append({"file": f"captures/{path.name}", "bytes": len(raw),
                              "sha256": hashlib.sha256(raw).hexdigest(), "purpose": purpose})
out = Path(__file__).with_name("sys02_files_manifest.json")
out.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
print(json.dumps(manifest, indent=2))
