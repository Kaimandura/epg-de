#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import gzip
import json
import re
import shutil
import xml.etree.ElementTree as ET
from collections import defaultdict
from copy import deepcopy
from pathlib import Path
from typing import Any


def as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def safe_token(value: str) -> str:
    token = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip("-.")
    if not token:
        raise ValueError(f"Cannot create XMLTV ID token from {value!r}")
    return token


def display_names(channel: ET.Element) -> list[str]:
    return [
        (node.text or "").strip()
        for node in channel.findall("display-name")
        if (node.text or "").strip()
    ]


def normalized_names(channel: ET.Element) -> set[str]:
    return {name.casefold() for name in display_names(channel)}


def write_gzip(xml_path: Path) -> Path:
    gzip_path = xml_path.with_suffix(xml_path.suffix + ".gz")
    with xml_path.open("rb") as source, gzip_path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as target:
            shutil.copyfileobj(source, target)
    return gzip_path


def choose_source(
    definition: dict[str, Any],
    channels_by_id: dict[str, ET.Element],
    programmes_by_channel: dict[str, list[ET.Element]],
) -> tuple[str | None, int]:
    explicit_ids = as_list(definition.get("source_ids"))
    for source_id in explicit_ids:
        if source_id in channels_by_id and programmes_by_channel.get(source_id):
            return source_id, 1

    wanted_names = {
        value.strip().casefold()
        for value in (
            as_list(definition.get("match_names"))
            + [str(definition.get("name", ""))]
            + as_list(definition.get("aliases"))
        )
        if value.strip()
    }

    candidates: list[str] = []
    if wanted_names:
        for channel_id, channel in channels_by_id.items():
            if not programmes_by_channel.get(channel_id):
                continue
            if normalized_names(channel) & wanted_names:
                candidates.append(channel_id)

    prefer_prefixes = as_list(definition.get("prefer_prefixes"))
    if prefer_prefixes:
        preferred = [
            channel_id
            for channel_id in candidates
            if any(channel_id.startswith(prefix) for prefix in prefer_prefixes)
        ]
        if preferred:
            candidates = preferred

    exclude_prefixes = as_list(definition.get("exclude_prefixes"))
    if exclude_prefixes:
        candidates = [
            channel_id
            for channel_id in candidates
            if not any(channel_id.startswith(prefix) for prefix in exclude_prefixes)
        ]

    if not candidates:
        return None, 0

    candidates.sort(
        key=lambda channel_id: (-len(programmes_by_channel[channel_id]), channel_id)
    )
    return candidates[0], len(candidates)


def add_display_name(channel: ET.Element, value: str, seen: set[str]) -> None:
    value = value.strip()
    if not value or value.casefold() in seen:
        return
    node = ET.SubElement(channel, "display-name", {"lang": "de"})
    node.text = value
    seen.add(value.casefold())


def validate_roundtrip(xml_path: Path, gzip_path: Path) -> tuple[int, int, int]:
    xml_bytes = xml_path.read_bytes()
    with gzip.open(gzip_path, "rb") as handle:
        gzip_bytes = handle.read()
    if gzip_bytes != xml_bytes:
        raise SystemExit("Amazon gzip payload does not exactly match XML output.")

    root = ET.fromstring(gzip_bytes)
    if root.tag != "tv":
        raise SystemExit("Amazon XMLTV root is not <tv>.")

    channels = root.findall("channel")
    channel_ids = [node.attrib.get("id", "") for node in channels]
    if not all(channel_ids) or len(channel_ids) != len(set(channel_ids)):
        raise SystemExit("Amazon XMLTV contains empty or duplicate channel IDs.")

    known_ids = set(channel_ids)
    programmes = root.findall("programme")
    active_ids: set[str] = set()
    for programme in programmes:
        channel_id = programme.attrib.get("channel", "")
        if channel_id not in known_ids:
            raise SystemExit(
                f"Amazon programme references unknown channel ID: {channel_id}"
            )
        active_ids.add(channel_id)

    return len(channels), len(active_ids), len(programmes)


def update_platform_report(
    report_path: Path,
    channel_count: int,
    active_count: int,
    programme_count: int,
) -> None:
    if not report_path.exists():
        raise SystemExit(f"Platform coverage report is missing: {report_path}")

    with report_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames or []
        rows = list(reader)

    required_fields = [
        "platform",
        "output",
        "matched_xmltv_ids",
        "channel_count",
        "active_channel_count",
        "programme_count",
    ]
    if fieldnames != required_fields:
        raise SystemExit(
            f"Unexpected platform coverage columns: {fieldnames!r}; expected {required_fields!r}"
        )

    replacement = {
        "platform": "amazon",
        "output": "amazon.xml.gz",
        "matched_xmltv_ids": str(channel_count),
        "channel_count": str(channel_count),
        "active_channel_count": str(active_count),
        "programme_count": str(programme_count),
    }

    found = False
    for index, row in enumerate(rows):
        if row.get("platform") == "amazon":
            rows[index] = replacement
            found = True
            break
    if not found:
        rows.append(replacement)

    rows.sort(key=lambda row: row.get("platform", ""))
    with report_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=required_fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export an isolated Prime Video Germany XMLTV guide from the validated master EPG."
    )
    parser.add_argument("--master", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--platform-report", required=True, type=Path)
    args = parser.parse_args()

    master_root = ET.parse(args.master).getroot()
    if master_root.tag != "tv":
        raise SystemExit(f"{args.master}: expected <tv> root")

    config = json.loads(args.config.read_text(encoding="utf-8"))
    definitions = config.get("channels", [])
    if not isinstance(definitions, list) or not definitions:
        raise SystemExit("Amazon config must contain a non-empty 'channels' list.")

    namespace = str(config.get("namespace", "AmazonPrime.de")).strip().rstrip(".")
    label = str(config.get("label", "Prime Video")).strip() or "Prime Video"

    channels_by_id = {
        node.attrib["id"]: node
        for node in master_root.findall("channel")
        if node.attrib.get("id")
    }
    programmes_by_channel: dict[str, list[ET.Element]] = defaultdict(list)
    for programme in master_root.findall("programme"):
        channel_id = programme.attrib.get("channel", "")
        if channel_id:
            programmes_by_channel[channel_id].append(programme)

    output_root = ET.Element(
        "tv", {"generator-info-name": "Kaimandura/epg-de Prime Video"}
    )
    report_rows: list[list[Any]] = []
    selected: list[tuple[str, str, str, list[ET.Element]]] = []
    output_ids: set[str] = set()

    for definition in definitions:
        if not isinstance(definition, dict):
            raise SystemExit("Every Amazon channel definition must be an object.")

        key = safe_token(str(definition.get("key", "")))
        name = str(definition.get("name", key)).strip() or key
        required = bool(definition.get("required", False))
        amazon_id = f"{namespace}.{key}"
        if amazon_id in output_ids:
            raise SystemExit(f"Duplicate Amazon output ID: {amazon_id}")

        source_id, candidate_count = choose_source(
            definition, channels_by_id, programmes_by_channel
        )
        if source_id is None:
            status = "MISSING_REQUIRED" if required else "MISSING_OPTIONAL"
            report_rows.append(
                [amazon_id, name, status, "", "", 0, candidate_count]
            )
            if required:
                raise SystemExit(
                    f"Required Prime Video channel cannot be mapped: {name}"
                )
            print(f"Amazon optional channel not mapped: {name}")
            continue

        source_channel = channels_by_id[source_id]
        source_programmes = programmes_by_channel[source_id]
        if not source_programmes:
            if required:
                raise SystemExit(
                    f"Required Prime Video channel has no programmes: {name}"
                )
            report_rows.append(
                [amazon_id, name, "NO_EPG", source_id, "", 0, candidate_count]
            )
            continue

        channel = ET.Element("channel", {"id": amazon_id})
        seen_names: set[str] = set()
        add_display_name(channel, f"{name} [{label}]", seen_names)
        add_display_name(channel, name, seen_names)
        for alias in as_list(definition.get("aliases")):
            add_display_name(channel, alias, seen_names)
        for source_name in display_names(source_channel):
            add_display_name(channel, source_name, seen_names)

        output_root.append(channel)
        output_ids.add(amazon_id)
        selected.append((amazon_id, source_id, name, source_programmes))
        names = display_names(source_channel)
        source_name = names[0] if names else source_id
        report_rows.append(
            [
                amazon_id,
                name,
                "OK",
                source_id,
                source_name,
                len(source_programmes),
                candidate_count,
            ]
        )
        print(
            f"Amazon map: {name} -> {source_id} "
            f"({len(source_programmes)} programmes, candidates={candidate_count})"
        )

    for amazon_id, _source_id, _name, source_programmes in selected:
        for source_programme in sorted(
            source_programmes,
            key=lambda node: (
                node.attrib.get("start", ""),
                node.attrib.get("stop", ""),
                node.findtext("title") or "",
            ),
        ):
            programme = deepcopy(source_programme)
            programme.attrib["channel"] = amazon_id
            output_root.append(programme)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    tree = ET.ElementTree(output_root)
    ET.indent(tree, space="  ")
    tree.write(args.output, encoding="utf-8", xml_declaration=True)
    gzip_path = write_gzip(args.output)

    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "amazon_xmltv_id",
                "name",
                "status",
                "source_xmltv_id",
                "source_display_name",
                "programme_count",
                "candidate_count",
            ]
        )
        writer.writerows(report_rows)

    channel_count, active_count, programme_count = validate_roundtrip(
        args.output, gzip_path
    )
    min_channels = int(config.get("min_channels", 1))
    min_active = int(config.get("min_active_channels", min_channels))
    min_programmes = int(config.get("min_programmes", 1))

    if channel_count < min_channels:
        raise SystemExit(
            f"Amazon release gate failed: {channel_count} channels < {min_channels} required."
        )
    if active_count < min_active:
        raise SystemExit(
            f"Amazon release gate failed: {active_count} active channels < {min_active} required."
        )
    if programme_count < min_programmes:
        raise SystemExit(
            f"Amazon release gate failed: {programme_count} programmes < {min_programmes} required."
        )

    update_platform_report(
        args.platform_report, channel_count, active_count, programme_count
    )
    print(
        "Amazon validation OK: "
        f"{channel_count} channels, {active_count} active, {programme_count} programmes."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
