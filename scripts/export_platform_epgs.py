#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import gzip
import json
import re
import shutil
import xml.etree.ElementTree as ET
from copy import deepcopy
from pathlib import Path
from typing import Any


def as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def any_equals(actual: str, expected: Any) -> bool:
    values = as_list(expected)
    return not values or actual in values


def any_prefix(actual: str, expected: Any) -> bool:
    values = as_list(expected)
    return not values or any(actual.startswith(value) for value in values)


def any_contains(actual: str, expected: Any) -> bool:
    values = as_list(expected)
    return not values or any(value in actual for value in values)


def candidate_matches(candidate: dict[str, Any], rule: dict[str, Any]) -> bool:
    attrs = candidate.get("attrs", {})
    site = str(attrs.get("site", ""))
    site_id = str(attrs.get("site_id", ""))
    lang = str(attrs.get("lang", ""))
    source_file = str(candidate.get("source_file", ""))
    name = str(candidate.get("name", ""))

    if "site" in rule and not any_equals(site, rule["site"]):
        return False
    if "site_id_prefix" in rule and not any_prefix(site_id, rule["site_id_prefix"]):
        return False
    if "site_id_contains" in rule and not any_contains(site_id, rule["site_id_contains"]):
        return False
    if "source_file_contains" in rule and not any_contains(source_file, rule["source_file_contains"]):
        return False
    if "lang" in rule and not any_equals(lang, rule["lang"]):
        return False
    if "name_regex" in rule:
        patterns = as_list(rule["name_regex"])
        if patterns and not any(re.search(pattern, name, flags=re.IGNORECASE) for pattern in patterns):
            return False
    return True


def platform_ids(
    candidate_data: dict[str, list[dict[str, Any]]],
    definition: dict[str, Any],
) -> set[str]:
    selected = set(as_list(definition.get("explicit_xmltv_ids")))
    rules = definition.get("rules", [])
    if not isinstance(rules, list):
        raise ValueError("platform rules must be a list")

    for xmltv_id, candidates in candidate_data.items():
        for candidate in candidates:
            if any(candidate_matches(candidate, rule) for rule in rules):
                selected.add(xmltv_id)
                break
    return selected


def write_subset(
    master_root: ET.Element,
    wanted_ids: set[str],
    xml_path: Path,
) -> tuple[int, int, int]:
    channels_by_id: dict[str, ET.Element] = {}
    programmes_by_channel: dict[str, list[ET.Element]] = {}

    for channel in master_root.findall("channel"):
        channel_id = channel.attrib.get("id", "")
        if channel_id:
            channels_by_id[channel_id] = channel

    for programme in master_root.findall("programme"):
        channel_id = programme.attrib.get("channel", "")
        if not channel_id:
            continue
        programmes_by_channel.setdefault(channel_id, []).append(programme)

    selected_ids = sorted(wanted_ids & set(channels_by_id))
    root = ET.Element("tv", {"generator-info-name": "Kaimandura/epg-de"})

    for channel_id in selected_ids:
        root.append(deepcopy(channels_by_id[channel_id]))

    programme_count = 0
    active_channels = 0
    for channel_id in selected_ids:
        programmes = programmes_by_channel.get(channel_id, [])
        if programmes:
            active_channels += 1
        for programme in programmes:
            root.append(deepcopy(programme))
            programme_count += 1

    xml_path.parent.mkdir(parents=True, exist_ok=True)
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(xml_path, encoding="utf-8", xml_declaration=True)

    gzip_path = xml_path.with_suffix(xml_path.suffix + ".gz")
    with xml_path.open("rb") as source, gzip_path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as target:
            shutil.copyfileobj(source, target)

    with gzip.open(gzip_path, "rb") as handle:
        roundtrip_root = ET.fromstring(handle.read())
    known = {node.attrib.get("id", "") for node in roundtrip_root.findall("channel")}
    unknown_refs = {
        node.attrib.get("channel", "")
        for node in roundtrip_root.findall("programme")
        if node.attrib.get("channel", "") not in known
    }
    if unknown_refs:
        raise ValueError(
            f"{gzip_path}: programme references unknown channel IDs: {sorted(unknown_refs)[:10]}"
        )

    return len(selected_ids), active_channels, programme_count


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export platform-specific XMLTV subsets from the validated Germany master guide."
    )
    parser.add_argument("--master", required=True, type=Path)
    parser.add_argument("--candidates", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()

    master_root = ET.parse(args.master).getroot()
    if master_root.tag != "tv":
        raise ValueError(f"{args.master}: expected <tv> root")

    candidate_payload = json.loads(args.candidates.read_text(encoding="utf-8"))
    candidate_data: dict[str, list[dict[str, Any]]] = candidate_payload["channels"]

    config_payload = json.loads(args.config.read_text(encoding="utf-8"))
    platforms = config_payload.get("platforms", {})
    if not isinstance(platforms, dict) or not platforms:
        raise ValueError("platform config must contain a non-empty 'platforms' object")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)

    rows: list[list[Any]] = []
    for platform in sorted(platforms):
        definition = platforms[platform]
        if not isinstance(definition, dict):
            raise ValueError(f"platform '{platform}' must be an object")
        output_name = str(definition.get("output", f"{platform}.xml.gz"))
        if not output_name.endswith(".xml.gz"):
            raise ValueError(f"platform '{platform}' output must end with .xml.gz")

        xml_path = args.output_dir / output_name[:-3]
        wanted = platform_ids(candidate_data, definition)
        channel_count, active_channel_count, programme_count = write_subset(
            master_root=master_root,
            wanted_ids=wanted,
            xml_path=xml_path,
        )
        rows.append(
            [platform, output_name, len(wanted), channel_count, active_channel_count, programme_count]
        )
        print(
            f"Platform {platform}: matched={len(wanted)} channels={channel_count} "
            f"active={active_channel_count} programmes={programme_count}"
        )

    with args.report.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "platform",
                "output",
                "matched_xmltv_ids",
                "channel_count",
                "active_channel_count",
                "programme_count",
            ]
        )
        writer.writerows(rows)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
