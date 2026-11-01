#!/usr/bin/env python3
"""Read-only quality inventory of every published XMLTV product.

Does not modify, rename or remove channels. Network logo availability is a
separate live check; this inventory validates logo presence and URL syntax.
"""
import argparse
import gzip
import json
import re
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

PRODUCTS = {
    "de": "DE-MASTER", "magenta": "DE-MAGENTA",
    "samsung": "DE-SAMSUNG", "pluto": "DE-PLUTO",
    "amazon": "DE-AMAZON", "usa": "USA-MASTER",
    "usa-local": "USA-LOCAL", "usa-fast": "USA-FAST",
    "usa-sports": "USA-SPORTS",
}
OPTIONAL = {"amazon"}
STAMP = re.compile(r"^\d{14} [+-]\d{4}$")


def inspect(path):
    with gzip.open(path, "rb") as stream:
        root = ET.parse(stream).getroot()
    if root.tag != "tv":
        raise ValueError(f"{path}: expected XMLTV <tv>")
    ids = set()
    issues = []
    counts = Counter()
    for ch in root.findall("channel"):
        cid = ch.get("id", "").strip()
        if not cid or cid in ids:
            issues.append({"type": "duplicate_or_empty_id", "channel": cid})
        ids.add(cid)
        if not any((n.text or "").strip() for n in ch.findall("display-name")):
            issues.append({"type": "missing_name", "channel": cid})
        icons = [(n.get("src") or "").strip() for n in ch.findall("icon")]
        if not icons or not any(icons):
            counts["missing_logo"] += 1
        else:
            for url in icons:
                parsed = urlsplit(url)
                if parsed.scheme != "https" or not parsed.netloc:
                    issues.append({"type": "invalid_logo_url", "channel": cid})
    for p in root.findall("programme"):
        cid = p.get("channel", "")
        counts["programmes"] += 1
        if cid not in ids:
            issues.append({"type": "orphan_programme", "channel": cid})
        if not (p.findtext("title") or "").strip():
            issues.append({"type": "missing_title", "channel": cid})
        start, stop = p.get("start", ""), p.get("stop", "")
        if not STAMP.fullmatch(start) or not STAMP.fullmatch(stop):
            issues.append({"type": "invalid_time_format", "channel": cid})
        else:
            try:
                a = datetime.strptime(start, "%Y%m%d%H%M%S %z").astimezone(timezone.utc)
                b = datetime.strptime(stop, "%Y%m%d%H%M%S %z").astimezone(timezone.utc)
                if b <= a:
                    issues.append({"type": "nonpositive_duration", "channel": cid})
            except ValueError:
                issues.append({"type": "invalid_time", "channel": cid})
    return {"channels": len(ids), "programmes": counts["programmes"],
            "missing_logos": counts["missing_logo"],
            "issues": issues, "issue_count": len(issues)}


def audit(directory):
    result = {}
    failures = []
    for stem, label in PRODUCTS.items():
        path = directory / f"{stem}.xml.gz"
        if not path.is_file():
            if stem in OPTIONAL:
                result[label] = {"status": "optional_absent"}
                continue
            failures.append(f"{label}: missing file")
            result[label] = {"status": "missing"}
            continue
        try:
            record = inspect(path)
            record["status"] = "invalid" if record["issues"] else "ok"
            result[label] = record
            if record["issues"]:
                failures.append(f"{label}: {len(record['issues'])} structural errors")
        except (OSError, EOFError, ET.ParseError, ValueError) as exc:
            result[label] = {"status": "corrupt", "error": str(exc)}
            failures.append(f"{label}: {exc}")
    return {"products": result, "failures": failures,
            "status": "failed" if failures else "passed"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, default=Path("epg"))
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = audit(args.directory)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({name: {k: v for k, v in row.items() if k != "issues"}
                      for name, row in report["products"].items()}, indent=2))
    if report["failures"]:
        raise SystemExit("\n".join(report["failures"]))


if __name__ == "__main__":
    main()
