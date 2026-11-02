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
USA_PRODUCTS = {"USA-MASTER", "USA-LOCAL", "USA-FAST", "USA-SPORTS"}


def load_usa_logo_debt(path):
    """Allow only previously measured USA logo absences, never invalid XMLTV."""
    if path is None:
        return {}
    policy = json.loads(Path(path).read_text(encoding="utf-8"))
    limits = policy.get("max_missing_logos")
    if (policy.get("schema_version") != 1 or not isinstance(limits, dict)
            or set(limits) != USA_PRODUCTS
            or any(type(value) is not int or value < 0 for value in limits.values())):
        raise ValueError("invalid USA logo-debt policy")
    return limits

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
            issues.append({"type": "missing_logo", "channel": cid})
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
    if not ids or counts["programmes"] == 0:
        issues.append({"type": "empty_guide", "channel": ""})
    # Must survive producer, staging and Pages packaging.
    if path.name in {"de.xml.gz", "DE-MASTER.xml.gz"}:
        critical = {
            "SkyCinemaBlockbuster.de@HD",
            "SkyCinemaBlockbuster.de@Plus24HD",
            "SkyCinemaFeelgood.de@HD",
            "SkyCinemaFeelgood.de@Plus24HD",
        }
        scheduled = Counter(p.get("channel", "") for p in root.findall("programme"))
        for cid in sorted(critical):
            if cid not in ids:
                issues.append({"type": "missing_required_channel", "channel": cid})
            elif scheduled[cid] == 0:
                issues.append({"type": "missing_required_programmes", "channel": cid})
    return {"channels": len(ids), "programmes": counts["programmes"],
            "missing_logos": counts["missing_logo"],
            "issues": issues, "issue_count": len(issues)}


def audit(directory, layout="source", usa_logo_debt=None):
    if layout not in {"source", "pages"}:
        raise ValueError(f"unknown XMLTV layout: {layout}")
    limits = load_usa_logo_debt(usa_logo_debt)
    result = {}
    failures = []
    deferred = {}
    for stem, label in PRODUCTS.items():
        path = directory / (f"{stem}.xml.gz" if layout == "source" else f"{label}.xml.gz")
        if not path.is_file():
            if stem in OPTIONAL:
                result[label] = {"status": "optional_absent"}
                continue
            failures.append(f"{label}: missing file")
            result[label] = {"status": "missing"}
            continue
        try:
            record = inspect(path)
            issues = record["issues"]
            hard_issues = [issue for issue in issues
                           if not (label in limits and issue["type"] == "missing_logo")]
            missing = record["missing_logos"]
            if label in limits:
                deferred[label] = {"missing_logos": missing, "ceiling": limits[label]}
                if missing > limits[label]:
                    hard_issues.append({"type": "usa_logo_debt_increased", "channel": ""})
            record["hard_issue_count"] = len(hard_issues)
            record["deferred_logo_issues"] = missing if label in limits else 0
            record["status"] = ("invalid" if hard_issues
                                else "logo_debt" if label in limits and missing else "ok")
            result[label] = record
            if hard_issues:
                failures.append(f"{label}: {len(hard_issues)} release-blocking errors")
        except (OSError, EOFError, ET.ParseError, ValueError) as exc:
            result[label] = {"status": "corrupt", "error": str(exc)}
            failures.append(f"{label}: {exc}")
    return {"products": result, "failures": failures, "usa_logo_debt": deferred,
            "status": "failed" if failures else "passed"}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, default=Path("epg"))
    parser.add_argument("--layout", choices=("source", "pages"), default="source")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--usa-logo-debt", type=Path, help="Pin acceptable historical USA-only logo deficits")
    args = parser.parse_args()
    report = audit(args.directory, args.layout, args.usa_logo_debt)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({name: {k: v for k, v in row.items() if k != "issues"}
                      for name, row in report["products"].items()}, indent=2))
    if report["failures"]:
        raise SystemExit("\n".join(report["failures"]))


if __name__ == "__main__":
    main()
