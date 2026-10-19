"""TC-08: flip the lowest bit of one byte in the cached model (a backup must already exist)."""
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

path = Path(sys.argv[1])
offset = int(sys.argv[2])
before_hash = hashlib.sha256(path.read_bytes()).hexdigest()
with open(path, "r+b") as handle:
    handle.seek(offset)
    old = handle.read(1)[0]
    handle.seek(offset)
    handle.write(bytes([old ^ 0x01]))
after = path.read_bytes()
print(json.dumps({
    "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    "file": str(path), "size": len(after), "offset": offset,
    "byte_before": f"0x{old:02x}", "byte_after": f"0x{old ^ 0x01:02x}",
    "sha256_before": before_hash, "sha256_after": hashlib.sha256(after).hexdigest(),
}, indent=2))
