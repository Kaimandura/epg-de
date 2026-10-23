#!/usr/bin/env python3
"""Read-only, streaming measurements of actual XMLTV release files.

Logo presence, asset reachability and brand identity are separate evidence.
A passing integrity gate alone never certifies 100% product quality.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import re
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from enrich_epg_logos import valid_logo_url, load_logos, load_channel_name_index, split_feed_id
from release_policy import timestamp

AVAILABILITY_REASONS = {"broadcast_area=DE", "iptv-country=DE", "manual_override"}
PLACEHOLDER_TITLE = re.compile(r"^(?:no (?:information|programme?|epg data)(?: available)?|to be announced|tba|tbd|unknown|[-?]+)$", re.I)


def parse_xmltv_time(value):
    try:
        return timestamp(value or "")
    except (ValueError, TypeError):
        return None


def display_name(node):
    return next(((n.text or "").strip() for n in node.findall("display-name") if (n.text or "").strip()), "")


def coverage_rows(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        return {r["xmltv_id"]: r for r in csv.DictReader(stream) if r.get("xmltv_id")}


def audit_guide(path, now=None, coverage=None, stale_hours=12, large_gap_hours=6, probes=None,
                logo_owners=None, known_ids=None):
    """Measure input without changing sender IDs, programme records or files."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("audit time requires an explicit timezone")
    now = now.astimezone(timezone.utc)
    probes = probes or {}
    channels, counts, schedules, seen, issues = {}, Counter(), defaultdict(list), set(), []
    counters = Counter()

    def add(severity, check, cid="", value="", details=""):
        issues.append({"file": path.name, "severity": severity, "check": check,
                       "xmltv_id": cid, "name": channels.get(cid, {}).get("name", ""),
                       "value": value, "details": details})
        counters["severity_" + severity.lower()] += 1
        counters["check_" + check] += 1

    total = 0
    try:
        opener = gzip.open if path.suffix == ".gz" else open
        with opener(path, "rb") as stream:
            iterator = ET.iterparse(stream, events=("start", "end"))
            _, root = next(iterator)
            if root.tag != "tv":
                raise ValueError("expected tv root")
            for event, node in iterator:
                if event != "end" or node.tag not in {"channel", "programme"}:
                    continue
                cid = (node.get("id" if node.tag == "channel" else "channel") or "").strip()
                if node.tag == "channel":
                    if not cid or cid in channels:
                        add("ERROR", "empty_or_duplicate_channel_id", cid)
                    icons = [n.get("src", "").strip() for n in node.findall("icon") if n.get("src", "").strip()]
                    channels[cid] = {"name": display_name(node), "icons": icons}
                    if not channels[cid]["name"]:
                        add("ERROR", "missing_display_name", cid)
                    if not icons:
                        add("WARNING", "missing_icon", cid)
                    for url in icons:
                        if not valid_logo_url(url):
                            add("WARNING", "invalid_icon_url", cid, url)
                        elif probes.get(url, {}).get("status") == "failed":
                            add("WARNING", "failed_icon_asset", cid, url, probes[url].get("reason", ""))
                        base = split_feed_id(cid)[0]
                        if (known_ids and base in known_ids and logo_owners and url in logo_owners
                                and base not in logo_owners[url]):
                            add("REVIEW", "logo_identity_conflict", cid, url,
                                "Catalog associates asset with: " + ";".join(sorted(logo_owners[url])))
                    if ".bossdummy" in cid.lower():
                        add("REVIEW", "synthetic_channel_candidate", cid,
                            details="Review source identity; no automatic deletion")
                else:
                    total += 1
                    if cid not in channels:
                        add("ERROR", "unknown_channel_reference", cid)
                    counts[cid] += 1
                    title = (node.findtext("title") or "").strip()
                    if not title:
                        add("ERROR", "missing_programme_title", cid)
                    elif PLACEHOLDER_TITLE.fullmatch(title):
                        add("REVIEW", "placeholder_title_candidate", cid, title)
                    start, stop = parse_xmltv_time(node.get("start")), parse_xmltv_time(node.get("stop"))
                    if start is None:
                        add("ERROR", "invalid_start_time", cid, node.get("start", ""))
                    if not node.get("stop"):
                        # Stop is optional in XMLTV; preserve it and expose the
                        # unproven end time rather than deleting valid metadata.
                        add("WARNING", "missing_stop_time", cid)
                    elif stop is None:
                        add("ERROR", "invalid_stop_time", cid, node.get("stop", ""))
                    if start is not None and stop is not None:
                        if stop <= start:
                            add("ERROR", "non_positive_duration", cid, title)
                        else:
                            schedules[cid].append((start, stop, title))
                    signature = (cid, start, stop, title.casefold())
                    if signature in seen:
                        add("ERROR", "duplicate_programme", cid, title)
                    seen.add(signature)
                root.remove(node)
    except (OSError, ValueError, ET.ParseError, StopIteration) as exc:
        add("ERROR", "invalid_xml_or_gzip", details=str(exc))
    if not channels or not total:
        add("ERROR", "empty_guide")
    measured = []
    for cid, channel in channels.items():
        items = sorted(schedules[cid], key=lambda row: (row[0], row[1], row[2]))
        if not counts[cid]:
            add("ERROR", "channel_without_programmes", cid)
        latest = max((row[1] for row in items), default=None)
        current = any(start <= now < stop for start, stop, _ in items)
        future = sum(stop > now for _, stop, _ in items)
        if latest is not None and latest <= now:
            add("WARNING", "expired_schedule", cid, latest.isoformat())
        elif latest is not None and latest < now + timedelta(hours=stale_hours):
            add("WARNING", "short_future_coverage", cid, latest.isoformat())
        if future and not current:
            add("REVIEW", "no_current_programme", cid,
                details="May be an upcoming event channel; no automatic time repair")
        previous_stop, previous_title = None, ""
        for start, stop, title in items:
            if previous_stop is not None:
                if start < previous_stop:
                    add("WARNING", "schedule_overlap", cid,
                        str(int((previous_stop-start).total_seconds())),
                        f"{previous_title!r} overlaps {title!r}")
                elif start - previous_stop > timedelta(hours=large_gap_hours):
                    add("WARNING", "large_schedule_gap", cid,
                        str(int((start-previous_stop).total_seconds())))
            if previous_stop is None or stop > previous_stop:
                previous_stop, previous_title = stop, title
        if coverage is not None:
            row = coverage.get(cid)
            if row is None:
                add("REVIEW", "missing_coverage_row", cid)
            else:
                reasons = set(row.get("reasons", "").split(";"))
                if "lang=de" in reasons and not reasons & AVAILABILITY_REASONS:
                    add("REVIEW", "language_only_selection", cid, details=";".join(sorted(reasons)))
                if not row.get("selected_site") and not row.get("source") and not row.get("site"):
                    add("REVIEW", "unproven_programme_source", cid)
        verified = any(probes.get(u, {}).get("status") == "passed"
                       for u in channel["icons"] if valid_logo_url(u))
        measured.append({"file": path.name, "xmltv_id": cid, "name": channel["name"],
                         "programmes": counts[cid], "with_logo": bool(channel["icons"]),
                         "image_asset_verified": verified, "future_programmes": future,
                         "current_programme": current,
                         "first_start_utc": items[0][0].isoformat() if items else "",
                         "last_stop_utc": latest.isoformat() if latest else ""})
    with_logos = sum(row["with_logo"] for row in measured)
    asset_verified = sum(row["image_asset_verified"] for row in measured)
    summary = {"file": path.name, "generated_at_utc": now.isoformat(),
               "channels": len(channels), "programmes": total,
               "hard_errors": counters["severity_error"],
               "warnings": counters["severity_warning"], "review_items": counters["severity_review"],
               "with_logos": with_logos, "missing_icons": len(channels)-with_logos,
               "logo_presence_percent": round(with_logos / len(channels)*100, 2) if channels else 0,
               "image_assets_verified_channels": asset_verified,
               "image_assets_unverified_channels": len(channels)-asset_verified,
               "logo_identity_verification": "requires_source_identity_evidence",
               "source_provenance": "coverage_rows_checked" if coverage is not None else "not_checked",
               "expired_channels": counters["check_expired_schedule"],
               "schedule_overlaps": counters["check_schedule_overlap"],
               "large_schedule_gaps": counters["check_large_schedule_gap"],
               "short_future_coverage": counters["check_short_future_coverage"],
               "check_counts": {k.removeprefix("check_"): v for k,v in sorted(counters.items())
                                if k.startswith("check_")}}
    blocked = issues or asset_verified != len(channels) or coverage is None
    summary["qualification_status"] = "blocked" if blocked else "requires_identity_review"
    return summary, issues, measured


def write_csv(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--xml", required=True, type=Path, nargs="+")
    parser.add_argument("--coverage", type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--summary", required=True, type=Path)
    parser.add_argument("--channel-report", type=Path)
    parser.add_argument("--logo-probes", type=Path, help="URL-to-image-probe JSON evidence")
    parser.add_argument("--logos-csv", type=Path, help="Detect asset-to-channel catalog conflicts")
    parser.add_argument("--channels-csv", type=Path)
    parser.add_argument("--now", help="Reproducible ISO-8601 audit time including timezone")
    parser.add_argument("--strict", action="store_true",
                        help="Fail on warnings, unresolved reviews, missing asset or source verification")
    parser.add_argument("--max-hard-errors", type=int, default=0)
    parser.add_argument("--stale-hours", type=int, default=12)
    parser.add_argument("--file-future-hours", action="append", default=[], metavar="FILE=HOURS",
                        help="Documented rolling-guide minimum future horizon for a specific file")
    parser.add_argument("--large-gap-hours", type=int, default=6)
    args = parser.parse_args()
    now = datetime.fromisoformat(args.now) if args.now else datetime.now(timezone.utc)
    if now.tzinfo is None:
        parser.error("--now requires an explicit timezone")
    probes = json.loads(args.logo_probes.read_text(encoding="utf-8")) if args.logo_probes else {}
    probes = probes.get("image_probes", probes)
    coverage = coverage_rows(args.coverage) if args.coverage else None
    known_ids = load_channel_name_index(args.channels_csv)[0] if args.channels_csv else set()
    logo_owners = defaultdict(set)
    if args.logos_csv:
        for candidates in load_logos(args.logos_csv).values():
            for candidate in candidates:
                logo_owners[candidate.url].add(candidate.channel_id)
    outputs, issues, rows = [], [], []
    horizons = {}
    for spec in args.file_future_hours:
        filename, separator, hours = spec.partition("=")
        if not separator or not filename or not hours.isdigit() or int(hours) < 1:
            parser.error("--file-future-hours requires FILE=positive integer")
        horizons[filename] = int(hours)
    for path in args.xml:
        result, found, measured = audit_guide(path, now, coverage,
                                             horizons.get(path.name, args.stale_hours), args.large_gap_hours,
                                             probes, logo_owners, known_ids)
        outputs.append(result)
        issues.extend(found)
        rows.extend(measured)
        print(f"{path.name}: {result['channels']} channels, {result['programmes']} programmes, "
              f"logos={result['with_logos']}/{result['channels']}, errors={result['hard_errors']}, "
              f"warnings={result['warnings']}, review={result['review_items']}", flush=True)
    summary = {"generated_at_utc": now.isoformat(), "outputs": outputs,
               **{key: sum(r[key] for r in outputs)
                  for key in ("channels", "programmes", "hard_errors", "warnings", "review_items",
                              "missing_icons", "schedule_overlaps", "large_schedule_gaps",
                              "short_future_coverage")},
               "qualification_status": "blocked" if any(r["qualification_status"] == "blocked" for r in outputs)
                                       else "requires_identity_review",
               "max_hard_errors": args.max_hard_errors,
               "limitation": "Reachability alone does not certify logo identity or factual programme content."}
    write_csv(args.report, issues, ["file", "severity", "check", "xmltv_id", "name", "value", "details"])
    if args.channel_report:
        write_csv(args.channel_report, rows,
                  ["file", "xmltv_id", "name", "programmes", "with_logo", "image_asset_verified",
                   "future_programmes", "current_programme", "first_start_utc", "last_stop_utc"])
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    failed = (summary["hard_errors"] > args.max_hard_errors or
              (args.strict and summary["qualification_status"] == "blocked"))
    print("QUALITY QUALIFICATION BLOCKED" if failed else "INTEGRITY OK; inspect unresolved quality findings")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
