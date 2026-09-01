#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from typing import Any

GERMAN_LANG_CODES = {"de", "deu", "ger"}


def split_values(value: str) -> list[str]:
    return [part.strip() for part in re.split(r"[;,]", value or "") if part.strip()]


def is_german_area(value: str) -> bool:
    area = value.strip()
    return area == "c/DE" or area.startswith("s/DE-") or area.startswith("ct/DE-")


def load_germany_feed_ids(path: Path) -> set[str]:
    ids: set[str] = set()
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            areas = split_values(row.get("broadcast_area", ""))
            if not any(is_german_area(area) for area in areas):
                continue
            channel = (row.get("channel") or "").strip()
            feed_id = (row.get("id") or "").strip()
            if not channel:
                continue
            ids.add(channel)
            if feed_id:
                ids.add(f"{channel}@{feed_id}")
    return ids


def load_playlist_ids(path: Path | None) -> set[str]:
    if not path or not path.exists():
        return set()
    text = path.read_text(encoding="utf-8", errors="ignore")
    return set(re.findall(r'tvg-id="([^"]+)"', text))


def load_priorities(path: Path | None) -> dict[str, int]:
    if not path or not path.exists():
        return {}
    priorities: dict[str, int] = {}
    for index, raw in enumerate(path.read_text(encoding="utf-8").splitlines()):
        site = raw.strip()
        if site and not site.startswith("#"):
            priorities[site] = index
    return priorities


def language_is_german(value: str) -> bool:
    tokens = {part.strip().lower() for part in re.split(r"[,;\s]+", value or "") if part.strip()}
    return bool(tokens & GERMAN_LANG_CODES)


def candidate_key(candidate: dict[str, Any], priorities: dict[str, int]) -> tuple[Any, ...]:
    site = candidate["attrs"].get("site", "")
    lang = candidate["attrs"].get("lang", "")
    return (priorities.get(site, 10_000), 0 if language_is_german(lang) else 1, site, candidate["attrs"].get("site_id", ""))


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect German and Germany-available EPG channel candidates.")
    parser.add_argument("--epg-root", required=True, type=Path)
    parser.add_argument("--feeds-csv", required=True, type=Path)
    parser.add_argument("--playlist", type=Path)
    parser.add_argument("--priority-file", type=Path)
    parser.add_argument("--candidates", required=True, type=Path)
    parser.add_argument("--channels", required=True, type=Path)
    args = parser.parse_args()

    germany_feed_ids = load_germany_feed_ids(args.feeds_csv)
    germany_playlist_ids = load_playlist_ids(args.playlist)
    priorities = load_priorities(args.priority_file)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    files_seen = entries_seen = 0

    for path in sorted(args.epg_root.glob("sites/**/*.channels.xml")):
        files_seen += 1
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError:
            continue
        for element in root.findall("channel"):
            entries_seen += 1
            attrs = dict(element.attrib)
            xmltv_id = (attrs.get("xmltv_id") or "").strip()
            if not xmltv_id:
                continue
            base_id = xmltv_id.split("@", 1)[0]
            reasons: list[str] = []
            if language_is_german(attrs.get("lang", "")):
                reasons.append("lang=de")
            if xmltv_id in germany_feed_ids or base_id in germany_feed_ids:
                reasons.append("broadcast_area=DE")
            if xmltv_id in germany_playlist_ids or base_id in germany_playlist_ids:
                reasons.append("iptv-country=DE")
            if not reasons:
                continue
            grouped[xmltv_id].append({"xmltv_id": xmltv_id, "name": (element.text or "").strip() or xmltv_id, "attrs": attrs, "source_file": str(path.relative_to(args.epg_root)), "reasons": sorted(set(reasons))})

    result: dict[str, list[dict[str, Any]]] = {}
    preferred_root = ET.Element("channels")
    for xmltv_id in sorted(grouped):
        unique: dict[tuple[str, str, str], dict[str, Any]] = {}
        for candidate in grouped[xmltv_id]:
            attrs = candidate["attrs"]
            signature = (attrs.get("site", ""), attrs.get("site_id", ""), attrs.get("provider", ""))
            if signature not in unique:
                unique[signature] = candidate
            else:
                unique[signature]["reasons"] = sorted(set(unique[signature]["reasons"]) | set(candidate["reasons"]))
        candidates = sorted(unique.values(), key=lambda item: candidate_key(item, priorities))
        result[xmltv_id] = candidates
        preferred = candidates[0]
        node = ET.SubElement(preferred_root, "channel", preferred["attrs"])
        node.text = preferred["name"]

    args.candidates.parent.mkdir(parents=True, exist_ok=True)
    args.channels.parent.mkdir(parents=True, exist_ok=True)
    args.candidates.write_text(json.dumps({"meta": {"site_files_scanned": files_seen, "channel_entries_scanned": entries_seen, "selected_xmltv_ids": len(result), "germany_feed_ids": len(germany_feed_ids), "germany_playlist_ids": len(germany_playlist_ids)}, "channels": result}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tree = ET.ElementTree(preferred_root)
    ET.indent(tree, space="  ")
    tree.write(args.channels, encoding="utf-8", xml_declaration=True)
    print(f"Collected {len(result)} XMLTV IDs from {files_seen} site files ({entries_seen} channel entries scanned).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
