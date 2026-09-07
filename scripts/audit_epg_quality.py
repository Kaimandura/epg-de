#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

TARGET_COUNTRIES = {"de", "at", "ch"}
AVAILABILITY_REASONS = {"broadcast_area=DE", "iptv-country=DE", "manual_override"}
COUNTRY_RE = re.compile(r"\.([a-z]{2})(?:@|$)", re.IGNORECASE)
XMLTV_TIME_RE = re.compile(r"^(\d{14}|\d{12})(?:\s*([+-]\d{4}))?")


def parse_xmltv_time(value: str) -> datetime | None:
    value = (value or "").strip()
    match = XMLTV_TIME_RE.match(value)
    if not match:
        return None
    stamp, offset = match.groups()
    fmt = "%Y%m%d%H%M%S" if len(stamp) == 14 else "%Y%m%d%H%M"
    try:
        dt = datetime.strptime(stamp, fmt)
        if offset:
            sign = 1 if offset[0] == "+" else -1
            hours = int(offset[1:3])
            minutes = int(offset[3:5])
            tz = timezone(sign * timedelta(hours=hours, minutes=minutes))
            return dt.replace(tzinfo=tz).astimezone(timezone.utc)
        return dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def display_name(node: ET.Element) -> str:
    for item in node.findall("display-name"):
        text = (item.text or "").strip()
        if text:
            return text
    return ""


def coverage_rows(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return {
            (row.get("xmltv_id") or "").strip(): row
            for row in csv.DictReader(handle)
            if (row.get("xmltv_id") or "").strip()
        }


def reason_set(row: dict[str, str] | None) -> set[str]:
    if not row:
        return set()
    return {
        item.strip()
        for item in (row.get("reasons") or "").split(";")
        if item.strip()
    }


def country_from_id(xmltv_id: str) -> str:
    match = COUNTRY_RE.search(xmltv_id)
    return match.group(1).lower() if match else ""


def write_report(path: Path, rows: Iterable[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["severity", "check", "xmltv_id", "name", "value", "details"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit published XMLTV quality without changing the guide.")
    parser.add_argument("--xml", required=True, type=Path)
    parser.add_argument("--coverage", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--summary", required=True, type=Path)
    parser.add_argument("--max-hard-errors", type=int, default=0)
    parser.add_argument("--stale-hours", type=int, default=12)
    parser.add_argument("--large-gap-hours", type=int, default=6)
    args = parser.parse_args()

    try:
        root = ET.parse(args.xml).getroot()
    except (ET.ParseError, OSError) as exc:
        print(f"QUALITY GATE: invalid XML: {exc}", file=sys.stderr)
        return 2
    if root.tag != "tv":
        print("QUALITY GATE: root element is not <tv>.", file=sys.stderr)
        return 2

    issues: list[dict[str, str]] = []
    counters: Counter[str] = Counter()
    channels: dict[str, ET.Element] = {}

    def add(severity: str, check: str, xmltv_id: str = "", name: str = "", value: str = "", details: str = "") -> None:
        issues.append({"severity": severity, "check": check, "xmltv_id": xmltv_id, "name": name, "value": value, "details": details})
        counters[f"severity_{severity.lower()}"] += 1
        counters[f"check_{check}"] += 1

    for node in root.findall("channel"):
        channel_id = (node.attrib.get("id") or "").strip()
        if not channel_id:
            add("ERROR", "empty_channel_id", details="Channel element has no id")
            continue
        if channel_id in channels:
            add("ERROR", "duplicate_channel_id", channel_id, display_name(node))
            continue
        channels[channel_id] = node
        name = display_name(node)
        if not name:
            add("ERROR", "missing_display_name", channel_id)
        icon = node.find("icon")
        if icon is None or not (icon.attrib.get("src") or "").strip():
            add("WARNING", "missing_icon", channel_id, name)

    programme_seen: set[tuple[str, str, str, str]] = set()
    schedules: dict[str, list[tuple[datetime, datetime, str]]] = defaultdict(list)
    programme_counts: Counter[str] = Counter()

    for programme in root.findall("programme"):
        channel_id = (programme.attrib.get("channel") or "").strip()
        start_raw = (programme.attrib.get("start") or "").strip()
        stop_raw = (programme.attrib.get("stop") or "").strip()
        title = (programme.findtext("title") or "").strip()
        name = display_name(channels[channel_id]) if channel_id in channels else ""

        if channel_id not in channels:
            add("ERROR", "unknown_channel_reference", channel_id, value=title)
            continue
        programme_counts[channel_id] += 1
        if not title:
            add("ERROR", "missing_programme_title", channel_id, name, start_raw)

        start = parse_xmltv_time(start_raw)
        stop = parse_xmltv_time(stop_raw)
        if start is None:
            add("ERROR", "invalid_start_time", channel_id, name, start_raw, title)
        if stop is None:
            add("ERROR", "invalid_stop_time", channel_id, name, stop_raw, title)
        if start is not None and stop is not None:
            if stop <= start:
                add("ERROR", "non_positive_duration", channel_id, name, f"{start_raw} -> {stop_raw}", title)
            else:
                schedules[channel_id].append((start, stop, title))

        signature = (channel_id, start_raw, stop_raw, title)
        if signature in programme_seen:
            add("ERROR", "duplicate_programme", channel_id, name, start_raw, title)
        programme_seen.add(signature)

    for channel_id, node in channels.items():
        if programme_counts[channel_id] == 0:
            add("ERROR", "channel_without_programmes", channel_id, display_name(node))

    now = datetime.now(timezone.utc)
    stale_cutoff = now + timedelta(hours=args.stale_hours)
    gap_limit = timedelta(hours=args.large_gap_hours)

    for channel_id, items in schedules.items():
        name = display_name(channels[channel_id])
        items.sort(key=lambda item: (item[0], item[1], item[2]))
        latest_stop = max(item[1] for item in items)
        if latest_stop < stale_cutoff:
            add("WARNING", "short_future_coverage", channel_id, name, latest_stop.isoformat(), f"EPG ends before now + {args.stale_hours}h")

        _, previous_stop, previous_title = items[0]
        for start, stop, title in items[1:]:
            if start < previous_stop:
                overlap = previous_stop - start
                add("WARNING", "schedule_overlap", channel_id, name, str(int(overlap.total_seconds())), f"{previous_title!r} overlaps {title!r}")
            elif start - previous_stop > gap_limit:
                gap = start - previous_stop
                add("WARNING", "large_schedule_gap", channel_id, name, str(int(gap.total_seconds())), f"gap exceeds {args.large_gap_hours}h")
            if stop > previous_stop:
                previous_stop, previous_title = stop, title

    coverage = coverage_rows(args.coverage)
    for channel_id, node in channels.items():
        name = display_name(node)
        row = coverage.get(channel_id)
        reasons = reason_set(row)
        if not row:
            add("WARNING", "missing_coverage_row", channel_id, name)
            continue

        availability = reasons & AVAILABILITY_REASONS
        language_only = "lang=de" in reasons and not availability
        if language_only:
            country = country_from_id(channel_id)
            add("REVIEW", "language_only_selection", channel_id, name, country or "unknown", ";".join(sorted(reasons)))
            if country and country not in TARGET_COUNTRIES:
                add("REVIEW", "foreign_language_only_selection", channel_id, name, country, "No explicit DE/AT/CH availability evidence in coverage reasons")

    hard_errors = counters["severity_error"]
    hard_error_breakdown = dict(
        sorted(
            Counter(
                issue["check"]
                for issue in issues
                if issue["severity"] == "ERROR"
            ).items()
        )
    )
    summary = {
        "generated_at_utc": now.isoformat(),
        "channels": len(channels),
        "programmes": sum(programme_counts.values()),
        "hard_errors": hard_errors,
        "hard_error_breakdown": hard_error_breakdown,
        "warnings": counters["severity_warning"],
        "review_items": counters["severity_review"],
        "missing_icons": counters["check_missing_icon"],
        "schedule_overlaps": counters["check_schedule_overlap"],
        "large_schedule_gaps": counters["check_large_schedule_gap"],
        "short_future_coverage": counters["check_short_future_coverage"],
        "language_only_selection": counters["check_language_only_selection"],
        "foreign_language_only_selection": counters["check_foreign_language_only_selection"],
        "max_hard_errors": args.max_hard_errors,
    }

    write_report(args.report, issues)
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print("EPG quality audit:")
    for key, value in summary.items():
        print(f"  {key}: {value}")

    if hard_errors:
        print("Hard error details (first 50):")
        for issue in [item for item in issues if item["severity"] == "ERROR"][:50]:
            print(
                "  "
                f"{issue['check']} | {issue['xmltv_id']} | {issue['name']} | "
                f"{issue['value']} | {issue['details']}"
            )

    if hard_errors > args.max_hard_errors:
        print(f"QUALITY GATE FAILED: {hard_errors} hard error(s) > allowed {args.max_hard_errors}.", file=sys.stderr)
        return 1

    print("QUALITY GATE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
