#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
import unicodedata
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


def load_overrides(path: Path | None) -> list[dict[str, str]]:
    if not path or not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("channels", [])
    if not isinstance(rows, list):
        raise ValueError("override file: 'channels' must be a list")
    result: list[dict[str, str]] = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"override #{index} must be an object")
        normalized = {
            str(key): str(value).strip()
            for key, value in row.items()
            if value is not None
        }
        missing = [
            key
            for key in ("xmltv_id", "site", "site_id")
            if not normalized.get(key)
        ]
        if missing:
            raise ValueError(
                f"override #{index} missing required fields: {', '.join(missing)}"
            )
        result.append(normalized)
    return result


def language_is_german(value: str) -> bool:
    tokens = {
        part.strip().lower()
        for part in re.split(r"[,;\s]+", value or "")
        if part.strip()
    }
    return bool(tokens & GERMAN_LANG_CODES)


def normalize_channel_name(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.casefold().replace("&", " und ")
    text = re.sub(r"\bhigh\s*definition\b", " hd ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def candidate_key(
    candidate: dict[str, Any],
    priorities: dict[str, int],
) -> tuple[Any, ...]:
    site = candidate["attrs"].get("site", "")
    lang = candidate["attrs"].get("lang", "")
    return (
        priorities.get(site, 10_000),
        0 if language_is_german(lang) else 1,
        site,
        candidate["attrs"].get("site_id", ""),
    )


def write_unmapped_report(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "name",
                "site",
                "site_id",
                "lang",
                "status",
                "possible_xmltv_ids",
                "source_file",
            ]
        )
        for row in sorted(
            rows,
            key=lambda item: (
                item["site"],
                item["name"].casefold(),
                item["site_id"],
            ),
        ):
            writer.writerow(
                [
                    row["name"],
                    row["site"],
                    row["site_id"],
                    row["lang"],
                    row["status"],
                    ";".join(row["possible_xmltv_ids"]),
                    row["source_file"],
                ]
            )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Collect German and Germany-available EPG channel candidates."
    )
    parser.add_argument("--epg-root", required=True, type=Path)
    parser.add_argument("--feeds-csv", required=True, type=Path)
    parser.add_argument("--playlist", type=Path)
    parser.add_argument("--priority-file", type=Path)
    parser.add_argument("--override-file", type=Path)
    parser.add_argument("--unmapped-report", type=Path)
    parser.add_argument("--candidates", required=True, type=Path)
    parser.add_argument("--channels", required=True, type=Path)
    args = parser.parse_args()

    germany_feed_ids = load_germany_feed_ids(args.feeds_csv)
    germany_playlist_ids = load_playlist_ids(args.playlist)
    priorities = load_priorities(args.priority_file)
    overrides = load_overrides(args.override_file)

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    upstream_entries: dict[tuple[str, str], dict[str, Any]] = {}
    name_index: dict[str, set[str]] = defaultdict(set)
    empty_german_entries: list[dict[str, Any]] = []
    files_seen = entries_seen = 0

    for path in sorted(args.epg_root.glob("sites/**/*.channels.xml")):
        files_seen += 1
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError:
            continue

        source_file = str(path.relative_to(args.epg_root))
        for element in root.findall("channel"):
            entries_seen += 1
            attrs = dict(element.attrib)
            name = (element.text or "").strip()
            site = (attrs.get("site") or "").strip()
            site_id = (attrs.get("site_id") or "").strip()
            xmltv_id = (attrs.get("xmltv_id") or "").strip()

            entry = {
                "attrs": attrs,
                "name": name,
                "source_file": source_file,
            }
            if site and site_id:
                upstream_entries.setdefault((site, site_id), entry)

            if xmltv_id and name:
                name_index[normalize_channel_name(name)].add(xmltv_id)

            if not xmltv_id:
                if language_is_german(attrs.get("lang", "")) and site and site_id:
                    empty_german_entries.append(entry)
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

            grouped[xmltv_id].append(
                {
                    "xmltv_id": xmltv_id,
                    "name": name or xmltv_id,
                    "attrs": attrs,
                    "source_file": source_file,
                    "reasons": sorted(set(reasons)),
                }
            )

    override_keys = {
        (item["site"], item["site_id"])
        for item in overrides
    }

    auto_mapped = 0
    unresolved_rows: list[dict[str, Any]] = []
    for entry in empty_german_entries:
        attrs = entry["attrs"]
        site = (attrs.get("site") or "").strip()
        site_id = (attrs.get("site_id") or "").strip()
        name = entry["name"]
        key = (site, site_id)

        if key in override_keys:
            continue

        matches = sorted(name_index.get(normalize_channel_name(name), set()))
        if len(matches) == 1:
            xmltv_id = matches[0]
            mapped_attrs = dict(attrs)
            mapped_attrs["xmltv_id"] = xmltv_id
            grouped[xmltv_id].append(
                {
                    "xmltv_id": xmltv_id,
                    "name": name or xmltv_id,
                    "attrs": mapped_attrs,
                    "source_file": entry["source_file"],
                    "reasons": ["auto_name_match", "lang=de"],
                }
            )
            auto_mapped += 1
            continue

        unresolved_rows.append(
            {
                "name": name,
                "site": site,
                "site_id": site_id,
                "lang": attrs.get("lang", ""),
                "status": "AMBIGUOUS" if len(matches) > 1 else "UNMATCHED",
                "possible_xmltv_ids": matches,
                "source_file": entry["source_file"],
            }
        )

    overrides_loaded = 0
    missing_overrides: list[str] = []
    for override in overrides:
        key = (override["site"], override["site_id"])
        upstream = upstream_entries.get(key)
        if upstream is None:
            missing_overrides.append(
                f"{override['xmltv_id']} <- "
                f"{override['site']}:{override['site_id']}"
            )
            continue

        xmltv_id = override["xmltv_id"]
        attrs = dict(upstream["attrs"])
        attrs["xmltv_id"] = xmltv_id
        if override.get("lang"):
            attrs["lang"] = override["lang"]

        grouped[xmltv_id].append(
            {
                "xmltv_id": xmltv_id,
                "name": override.get("name") or upstream["name"] or xmltv_id,
                "attrs": attrs,
                "source_file": upstream["source_file"],
                "reasons": ["manual_override"],
            }
        )
        overrides_loaded += 1

    for missing in missing_overrides:
        print(f"WARNING: override source not found upstream: {missing}")

    result: dict[str, list[dict[str, Any]]] = {}
    preferred_root = ET.Element("channels")
    for xmltv_id in sorted(grouped):
        unique: dict[tuple[str, str, str], dict[str, Any]] = {}
        for candidate in grouped[xmltv_id]:
            attrs = candidate["attrs"]
            signature = (
                attrs.get("site", ""),
                attrs.get("site_id", ""),
                attrs.get("provider", ""),
            )
            if signature not in unique:
                unique[signature] = candidate
            else:
                unique[signature]["reasons"] = sorted(
                    set(unique[signature]["reasons"])
                    | set(candidate["reasons"])
                )

        candidates = sorted(
            unique.values(),
            key=lambda item: candidate_key(item, priorities),
        )
        result[xmltv_id] = candidates
        preferred = candidates[0]
        node = ET.SubElement(preferred_root, "channel", preferred["attrs"])
        node.text = preferred["name"]

    args.candidates.parent.mkdir(parents=True, exist_ok=True)
    args.channels.parent.mkdir(parents=True, exist_ok=True)
    args.candidates.write_text(
        json.dumps(
            {
                "meta": {
                    "site_files_scanned": files_seen,
                    "channel_entries_scanned": entries_seen,
                    "selected_xmltv_ids": len(result),
                    "germany_feed_ids": len(germany_feed_ids),
                    "germany_playlist_ids": len(germany_playlist_ids),
                    "empty_german_entries": len(empty_german_entries),
                    "auto_mapped_empty_ids": auto_mapped,
                    "unresolved_empty_ids": len(unresolved_rows),
                    "overrides_loaded": overrides_loaded,
                    "overrides_missing": len(missing_overrides),
                },
                "channels": result,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    if args.unmapped_report:
        write_unmapped_report(args.unmapped_report, unresolved_rows)

    tree = ET.ElementTree(preferred_root)
    ET.indent(tree, space="  ")
    tree.write(args.channels, encoding="utf-8", xml_declaration=True)

    print(
        f"Collected {len(result)} XMLTV IDs from {files_seen} site files "
        f"({entries_seen} entries; {auto_mapped} empty IDs auto-mapped; "
        f"{overrides_loaded} overrides; {len(unresolved_rows)} unresolved)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
