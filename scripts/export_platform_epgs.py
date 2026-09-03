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

DE_ALIAS_PREFIX = "DE - "


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


def display_name_variants(name: str) -> list[str]:
    variants = [name.strip()]
    if "+" in name:
        variants.extend(
            [
                name.replace("+", " Plus").strip(),
                name.replace("+", "Plus").strip(),
            ]
        )

    result: list[str] = []
    seen: set[str] = set()
    for value in variants:
        normalized = value.casefold()
        if value and normalized not in seen:
            seen.add(normalized)
            result.append(value)
    return result


def candidate_display_names(candidate: dict[str, Any]) -> list[str]:
    names = display_name_variants(str(candidate.get("name", "")))
    for alias in as_list(candidate.get("display_aliases")):
        names.extend(display_name_variants(alias))

    result: list[str] = []
    seen: set[str] = set()
    for value in names:
        normalized = value.casefold()
        if value and normalized not in seen:
            seen.add(normalized)
            result.append(value)
    return result


def clean_de_base_name(value: str) -> str:
    value = value.strip()
    value = re.sub(r"^DE\s*(?:-|:|\|)\s*", "", value, flags=re.IGNORECASE)
    return value.strip()


def existing_display_names(channel: ET.Element) -> list[str]:
    return [
        (node.text or "").strip()
        for node in channel.findall("display-name")
        if (node.text or "").strip()
    ]


def ensure_de_alias(channel: ET.Element, preferred_name: str, lang: str = "de") -> bool:
    names = existing_display_names(channel)
    existing = {name.casefold() for name in names}
    if any(name.casefold().startswith(DE_ALIAS_PREFIX.casefold()) for name in names):
        return False

    base = clean_de_base_name(preferred_name)
    if not base:
        for name in names:
            candidate = clean_de_base_name(name)
            if candidate:
                base = candidate
                break
    if not base:
        return False

    alias = f"{DE_ALIAS_PREFIX}{base}"
    if alias.casefold() in existing:
        return False

    attrs = {"lang": lang} if lang else {"lang": "de"}
    node = ET.Element("display-name", attrs)
    node.text = alias
    channel.append(node)
    return True


def ensure_master_display_names(
    master_root: ET.Element,
    candidate_data: dict[str, list[dict[str, Any]]],
) -> tuple[int, int]:
    channels_by_id = {
        channel.attrib.get("id", ""): channel
        for channel in master_root.findall("channel")
        if channel.attrib.get("id", "")
    }

    added_names = 0
    added_de_aliases = 0
    processed: set[str] = set()

    for xmltv_id, candidates in candidate_data.items():
        channel = channels_by_id.get(xmltv_id)
        if channel is None or not candidates:
            continue
        processed.add(xmltv_id)

        existing = {
            (node.text or "").strip().casefold()
            for node in channel.findall("display-name")
            if (node.text or "").strip()
        }
        lang = str(candidates[0].get("attrs", {}).get("lang", "")).strip() or "de"

        insert_at = 0
        for value in candidate_display_names(candidates[0]):
            if value.casefold() in existing:
                continue
            node = ET.Element("display-name", {"lang": lang})
            node.text = value
            channel.insert(insert_at, node)
            insert_at += 1
            existing.add(value.casefold())
            added_names += 1

        preferred = str(candidates[0].get("name", ""))
        if ensure_de_alias(channel, preferred, lang):
            added_de_aliases += 1

    for xmltv_id, channel in channels_by_id.items():
        if xmltv_id in processed:
            continue
        names = existing_display_names(channel)
        preferred = names[0] if names else xmltv_id
        if ensure_de_alias(channel, preferred, "de"):
            added_de_aliases += 1

    missing = [
        channel_id
        for channel_id, channel in channels_by_id.items()
        if not any(
            name.casefold().startswith(DE_ALIAS_PREFIX.casefold())
            for name in existing_display_names(channel)
        )
    ]
    if missing:
        raise ValueError(
            "DE compatibility alias missing for channel IDs: " + ", ".join(sorted(missing)[:20])
        )

    return added_names, added_de_aliases


def prune_empty_channels(root: ET.Element) -> int:
    active_ids = {
        programme.attrib.get("channel", "").strip()
        for programme in root.findall("programme")
        if programme.attrib.get("channel", "").strip()
    }
    removed = 0
    for channel in list(root.findall("channel")):
        channel_id = channel.attrib.get("id", "").strip()
        if channel_id and channel_id in active_ids:
            continue
        root.remove(channel)
        removed += 1

    remaining_ids = {
        channel.attrib.get("id", "").strip()
        for channel in root.findall("channel")
        if channel.attrib.get("id", "").strip()
    }
    orphan_programmes = {
        programme.attrib.get("channel", "").strip()
        for programme in root.findall("programme")
        if programme.attrib.get("channel", "").strip() not in remaining_ids
    }
    if orphan_programmes:
        raise ValueError(
            "Pruning created programme references to missing channels: "
            + ", ".join(sorted(orphan_programmes)[:20])
        )
    return removed


def write_xml_and_gzip(root: ET.Element, xml_path: Path) -> None:
    xml_path.parent.mkdir(parents=True, exist_ok=True)
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(xml_path, encoding="utf-8", xml_declaration=True)

    gzip_path = xml_path.with_suffix(xml_path.suffix + ".gz")
    with xml_path.open("rb") as source, gzip_path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as target:
            shutil.copyfileobj(source, target)


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
        if channel_id:
            programmes_by_channel.setdefault(channel_id, []).append(programme)

    selected_ids = sorted(
        channel_id
        for channel_id in (wanted_ids & set(channels_by_id))
        if programmes_by_channel.get(channel_id)
    )
    root = ET.Element("tv", {"generator-info-name": "Kaimandura/epg-de"})

    for channel_id in selected_ids:
        root.append(deepcopy(channels_by_id[channel_id]))

    programme_count = 0
    for channel_id in selected_ids:
        for programme in programmes_by_channel[channel_id]:
            root.append(deepcopy(programme))
            programme_count += 1

    write_xml_and_gzip(root, xml_path)

    gzip_path = xml_path.with_suffix(xml_path.suffix + ".gz")
    with gzip.open(gzip_path, "rb") as handle:
        roundtrip_root = ET.fromstring(handle.read())
    known = {node.attrib.get("id", "") for node in roundtrip_root.findall("channel")}
    active = {
        node.attrib.get("channel", "")
        for node in roundtrip_root.findall("programme")
        if node.attrib.get("channel", "")
    }
    unknown_refs = active - known
    if unknown_refs:
        raise ValueError(
            f"{gzip_path}: programme references unknown channel IDs: {sorted(unknown_refs)[:10]}"
        )
    empty_channels = known - active
    if empty_channels:
        raise ValueError(
            f"{gzip_path}: empty channels must not be published: {sorted(empty_channels)[:10]}"
        )

    missing_de_alias = [
        node.attrib.get("id", "")
        for node in roundtrip_root.findall("channel")
        if not any(
            (display.text or "").strip().casefold().startswith(DE_ALIAS_PREFIX.casefold())
            for display in node.findall("display-name")
        )
    ]
    if missing_de_alias:
        raise ValueError(
            f"{gzip_path}: channels without DE compatibility alias: {missing_de_alias[:10]}"
        )

    return len(selected_ids), len(active), programme_count


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

    added_names, added_de_aliases = ensure_master_display_names(master_root, candidate_data)
    pruned_master_channels = prune_empty_channels(master_root)
    write_xml_and_gzip(master_root, args.master)
    print(
        "Master display-name normalization: "
        f"added={added_names} de_aliases_added={added_de_aliases} "
        f"empty_channels_removed={pruned_master_channels}"
    )

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
        skipped_no_epg = max(0, len(wanted) - channel_count)
        print(
            f"Platform {platform}: matched={len(wanted)} channels={channel_count} "
            f"active={active_channel_count} programmes={programme_count} "
            f"skipped_no_epg={skipped_no_epg}"
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
