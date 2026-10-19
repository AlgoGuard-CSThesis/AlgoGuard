"""Summarise the Lighthouse accessibility reports: score, failed audits, and the DEF-15 audit."""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.stdout.reconfigure(encoding="utf-8")
summary = {}
for name in ("login", "monitor", "simulation"):
    report = json.loads((HERE / f"{name}.report.json").read_text(encoding="utf-8"))
    audits = report["audits"]
    refs = report["categories"]["accessibility"]["auditRefs"]
    failed = []
    for ref in refs:
        audit = audits[ref["id"]]
        scored = audit.get("scoreDisplayMode") in ("binary", "numeric")
        if scored and audit.get("score") is not None and audit["score"] < 1:
            items = (audit.get("details") or {}).get("items") or []
            examples = [((i.get("node") or {}).get("snippet") or "")[:120] for i in items[:3]]
            failed.append({"id": ref["id"], "title": audit["title"], "weight": ref.get("weight"),
                           "items": len(items), "examples": examples})
    sr = audits.get("scrollable-region-focusable", {})
    summary[name] = {
        "url": report["finalDisplayedUrl"], "fetched": report["fetchTime"],
        "lighthouse": report["lighthouseVersion"],
        "accessibility_score": round(report["categories"]["accessibility"]["score"] * 100),
        "failed_audits": failed,
        "scrollable_region_focusable": {"score": sr.get("score"),
                                        "mode": sr.get("scoreDisplayMode")},
    }
(HERE / "lighthouse_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False),
                                              encoding="utf-8")
print(json.dumps(summary, indent=2, ensure_ascii=False))
