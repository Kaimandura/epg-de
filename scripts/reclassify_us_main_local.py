#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import gzip
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from copy import deepcopy
from pathlib import Path


def load_patterns(path: Path) -> list[re.Pattern[str]]:
    patterns: list[re.Pattern[str]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        patterns.append(re.compile(line, re.IGNORECASE))
    if not patterns:
        raise RuntimeError("No USA national channel patterns configured")
    return patterns


def load_xml(path: Path) -> ET.Element:
    root = ET.parse(path).getroot()
    if root.tag != "tv":
        raise RuntimeError(f"{path}: root is not <tv>")
    return root


def display_names(channel: ET.Element) -> list[str]:
    return [
        (node.text or "").strip()
        for node in channel.findall("display-name")
        if (node.text or "").strip()
    ]


def is_national(channel: ET.Element, patterns: list[re.Pattern[str]]) -> bool:
    names = display_names(channel)
    if not names:
        names = [(channel.attrib.get("id") or "").strip()]
    return any(pattern.search(name) for name in names for pattern in patterns)


def collect(root: ET.Element) -> tuple[dict[str, ET.Element], dict[str, list[ET.Element]]]:
    channels: dict[str, ET.Element] = {}
    programmes: dict[str, list[ET.Element]] = defaultdict(list)
    for channel in root.findall("channel"):
        channel_id = (channel.attrib.get("id") or "").strip()
        if channel_id and channel_id not in channels:
            channels[channel_id] = deepcopy(channel)
    for programme in root.findall("programme"):
        channel_id = (programme.attrib.get("channel") or "").strip()
        if channel_id:
            programmes[channel_id].append(deepcopy(programme))
    return channels, programmes


def merge_aliases(target: ET.Element, source: ET.Element) -> None:
    existing = {
        (node.text or "").strip().casefold()
        for node in target.findall("display-name")
        if (node.text or "").strip()
    }
    for node in source.findall("display-name"):
        value = (node.text or "").strip()
        if value and value.casefold() not in existing:
            target.append(deepcopy(node))
            existing.add(value.casefold())
    if target.find("icon") is None and source.find("icon") is not None:
        target.append(deepcopy(source.find("icon")))


def schedule_signature(programmes: list[ET.Element]) -> tuple[tuple[str, str, str], ...]:
    rows = []
    for programme in programmes:
        rows.append(
            (
                programme.attrib.get("start", ""),
                programme.attrib.get("stop", ""),
                (programme.findtext("title") or "").strip(),
            )
        )
    rows.sort()
    return tuple(rows)


def normalized_name(channel: ET.Element, fallback: str) -> str:
    names = display_names(channel)
    value = names[0] if names else fallback
    value = value.casefold()
    value = re.sub(r"\[[^\]]+\]", " ", value)
    value = re.sub(r"\b(?:uhd|fhd|full\s*hd|hd|sd|4k|1080p|720p|576p|480p)\b", " ", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def dedupe_exact(
    channels: dict[str, ET.Element],
    programmes: dict[str, list[ET.Element]],
) -> int:
    groups: dict[tuple[str, tuple[tuple[str, str, str], ...]], list[str]] = defaultdict(list)
    for channel_id, channel in channels.items():
        items = programmes.get(channel_id, [])
        if not items:
            continue
        groups[(normalized_name(channel, channel_id), schedule_signature(items))].append(channel_id)

    removed = 0
    for ids in groups.values():
        if len(ids) < 2:
            continue
        ids.sort(key=lambda value: (len(value), value.casefold()))
        winner_id = ids[0]
        winner = channels[winner_id]
        for channel_id in ids[1:]:
            merge_aliases(winner, channels[channel_id])
            channels.pop(channel_id, None)
            programmes.pop(channel_id, None)
            removed += 1
    return removed


def build_root(
    channels: dict[str, ET.Element],
    programmes: dict[str, list[ET.Element]],
) -> ET.Element:
    root = ET.Element("tv")
    active_ids = sorted(
        channel_id
        for channel_id in channels
        if programmes.get(channel_id)
    )
    for channel_id in active_ids:
        root.append(deepcopy(channels[channel_id]))
    for channel_id in active_ids:
        items = programmes[channel_id]
        items.sort(
            key=lambda node: (
                node.attrib.get("start", ""),
                node.attrib.get("stop", ""),
                node.findtext("title") or "",
            )
        )
        for programme in items:
            root.append(deepcopy(programme))
    return root


def write_xml_and_gzip(root: ET.Element, xml_path: Path) -> tuple[int, int]:
    ET.indent(root, space="  ")
    payload = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    xml_path.write_bytes(payload)
    gz_path = Path(str(xml_path) + ".gz")
    with gz_path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as handle:
            handle.write(payload)
    channels = len(root.findall("channel"))
    programmes = len(root.findall("programme"))
    return channels, programmes


def refresh_report(
    report_path: Path,
    main_xml: Path,
    local_xml: Path,
    main_channels: int,
    main_programmes: int,
    local_channels: int,
    local_programmes: int,
) -> None:
    if not report_path.exists():
        raise RuntimeError(f"USA coverage report missing: {report_path}")

    with report_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        rows = [dict(row) for row in reader]

    required = {"category", "channel_count", "programme_count", "gzip_bytes"}
    if not required.issubset(fieldnames):
        raise RuntimeError(f"USA coverage report has unexpected columns: {fieldnames}")

    values = {
        "main": (
            main_channels,
            main_programmes,
            Path(str(main_xml) + ".gz").stat().st_size,
        ),
        "local": (
            local_channels,
            local_programmes,
            Path(str(local_xml) + ".gz").stat().st_size,
        ),
    }

    seen: set[str] = set()
    for row in rows:
        category = (row.get("category") or "").strip()
        if category not in values:
            continue
        channels, programmes, gzip_bytes = values[category]
        row["channel_count"] = str(channels)
        row["programme_count"] = str(programmes)
        row["gzip_bytes"] = str(gzip_bytes)
        seen.add(category)

    missing = set(values) - seen
    if missing:
        raise RuntimeError(f"USA coverage report missing categories after reclassification: {sorted(missing)}")

    with report_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    print(
        "USA coverage report refreshed after reclassification: "
        f"main={main_channels}/{main_programmes} "
        f"local={local_channels}/{local_programmes}"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--main", required=True, type=Path)
    parser.add_argument("--local", required=True, type=Path)
    parser.add_argument("--patterns", required=True, type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    patterns = load_patterns(args.patterns)
    main_root = load_xml(args.main)
    local_root = load_xml(args.local)

    main_channels, main_programmes = collect(main_root)
    local_channels, local_programmes = collect(local_root)

    national_channels: dict[str, ET.Element] = {}
    national_programmes: dict[str, list[ET.Element]] = {}
    moved = 0

    for channel_id, channel in main_channels.items():
        items = main_programmes.get(channel_id, [])
        if not items:
            continue
        if is_national(channel, patterns):
            national_channels[channel_id] = channel
            national_programmes[channel_id] = items
        else:
            if channel_id in local_channels:
                merge_aliases(local_channels[channel_id], channel)
                local_programmes[channel_id].extend(items)
            else:
                local_channels[channel_id] = channel
                local_programmes[channel_id] = items
            moved += 1

    national_deduped = dedupe_exact(national_channels, national_programmes)
    local_deduped = dedupe_exact(local_channels, local_programmes)

    national_root = build_root(national_channels, national_programmes)
    local_out_root = build_root(local_channels, local_programmes)

    main_channels_count, main_programmes_count = write_xml_and_gzip(national_root, args.main)
    local_channels_count, local_programmes_count = write_xml_and_gzip(local_out_root, args.local)

    if main_channels_count == 0 or main_programmes_count == 0:
        raise RuntimeError("USA main reclassification produced an empty guide")
    if local_channels_count == 0 or local_programmes_count == 0:
        raise RuntimeError("USA local reclassification produced an empty guide")

    if args.report is not None:
        refresh_report(
            args.report,
            args.main,
            args.local,
            main_channels_count,
            main_programmes_count,
            local_channels_count,
            local_programmes_count,
        )

    print(
        "USA reclassification complete: "
        f"moved_to_local={moved} "
        f"main_channels={main_channels_count} main_programmes={main_programmes_count} "
        f"local_channels={local_channels_count} local_programmes={local_programmes_count} "
        f"main_exact_deduped={national_deduped} local_exact_deduped={local_deduped}"
    )


if __name__ == "__main__":
    main()
