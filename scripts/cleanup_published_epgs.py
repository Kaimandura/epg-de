#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import gzip
import re
import shutil
import xml.etree.ElementTree as ET
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path


def parse_named_file(value: str) -> tuple[str, Path]:
    name, separator, raw_path = value.partition("=")
    name = name.strip()
    raw_path = raw_path.strip()
    if not separator or not name or not raw_path:
        raise argparse.ArgumentTypeError("Expected NAME=PATH")
    return name, Path(raw_path)


def load_root(path: Path) -> ET.Element:
    if path.suffix == ".gz":
        with gzip.open(path, "rb") as handle:
            root = ET.fromstring(handle.read())
    else:
        root = ET.parse(path).getroot()
    if root.tag != "tv":
        raise SystemExit(f"{path}: expected <tv> root")
    return root


def display_names(channel: ET.Element) -> list[str]:
    return [
        (node.text or "").strip()
        for node in channel.findall("display-name")
        if (node.text or "").strip()
    ]


def primary_name(channel: ET.Element) -> str:
    names = display_names(channel)
    return names[0] if names else ""


def normalized_name(value: str) -> str:
    text = value.casefold().strip()
    text = re.sub(r"^de\s*(?:-|:|\|)\s*", "", text)
    text = re.sub(r"\[[^\]]+\]", " ", text)
    text = re.sub(r"\b(?:uhd|fhd|full\s*hd|hd|sd|4k)\b", " ", text)
    text = re.sub(r"[^0-9a-zäöüß+]+", " ", text)
    return " ".join(text.split())


def parse_xmltv_datetime(value: str) -> datetime | None:
    match = re.match(r"^(\d{12}|\d{14})(?:\s*([+-]\d{4}))?", value.strip())
    if not match:
        return None
    raw = match.group(1)
    fmt = "%Y%m%d%H%M%S" if len(raw) == 14 else "%Y%m%d%H%M"
    dt = datetime.strptime(raw, fmt)
    offset = match.group(2)
    if offset:
        sign = 1 if offset[0] == "+" else -1
        hours = int(offset[1:3])
        minutes = int(offset[3:5])
        dt = dt.replace(
            tzinfo=timezone(sign * timedelta(hours=hours, minutes=minutes))
        )
    else:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def canonical_time(value: str) -> str:
    parsed = parse_xmltv_datetime(value)
    return parsed.strftime("%Y%m%d%H%M%S") if parsed else value.strip()


def schedule_slots(programmes: list[ET.Element]) -> set[tuple[str, str]]:
    return {
        (
            canonical_time(node.attrib.get("start", "")),
            canonical_time(node.attrib.get("stop", "")),
        )
        for node in programmes
        if node.attrib.get("start", "")
    }


def similarity(
    left: list[ET.Element],
    right: list[ET.Element],
) -> tuple[float, float, int]:
    left_slots = schedule_slots(left)
    right_slots = schedule_slots(right)
    if not left_slots or not right_slots:
        return 0.0, 0.0, 0
    shared = len(left_slots & right_slots)
    coverage = shared / min(len(left_slots), len(right_slots))
    union = len(left_slots | right_slots)
    jaccard = shared / union if union else 0.0
    return coverage, jaccard, shared


def add_aliases(target: ET.Element, source: ET.Element) -> int:
    existing = {
        (node.text or "").strip().casefold()
        for node in target.findall("display-name")
        if (node.text or "").strip()
    }
    added = 0
    for node in source.findall("display-name"):
        value = (node.text or "").strip()
        if not value or value.casefold() in existing:
            continue
        target.append(deepcopy(node))
        existing.add(value.casefold())
        added += 1

    if target.find("icon") is None:
        icon = source.find("icon")
        if icon is not None:
            target.append(deepcopy(icon))
    return added


def winner_score(
    channel_id: str,
    channel: ET.Element,
    programme_count: int,
    preserve: set[str],
) -> tuple[int, int, int, str]:
    score = 0
    folded = channel_id.casefold()
    if channel_id in preserve:
        score += 1000
    if ".de" in folded:
        score += 100
    if "@hd" in folded:
        score += 40
    elif "@sd" in folded:
        score += 20
    if channel.find("icon") is not None:
        score += 10
    return score, programme_count, -len(channel_id), channel_id


def write_xml_and_gzip(root: ET.Element, xml_path: Path) -> None:
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(xml_path, encoding="utf-8", xml_declaration=True)

    gzip_path = xml_path.with_suffix(xml_path.suffix + ".gz")
    with xml_path.open("rb") as source, gzip_path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as target:
            shutil.copyfileobj(source, target)

    with gzip.open(gzip_path, "rb") as handle:
        roundtrip = ET.fromstring(handle.read())
    if roundtrip.tag != "tv":
        raise SystemExit(f"{gzip_path}: gzip roundtrip failed")


def clean_file(
    label: str,
    path: Path,
    preserve: set[str],
    min_overlap: float,
    min_jaccard: float,
    min_shared: int,
) -> tuple[list[list[str | int | float]], int, int, int]:
    root = load_root(path)

    channels = {
        node.attrib["id"]: node
        for node in root.findall("channel")
        if node.attrib.get("id")
    }
    if len(channels) != len(root.findall("channel")):
        raise SystemExit(f"{path}: duplicate or empty channel IDs before cleanup")

    programmes_by_channel: dict[str, list[ET.Element]] = defaultdict(list)
    for programme in root.findall("programme"):
        channel_id = programme.attrib.get("channel", "")
        if channel_id:
            programmes_by_channel[channel_id].append(programme)

    rows: list[list[str | int | float]] = []
    removed_ids: set[str] = set()

    for channel_id, channel in list(channels.items()):
        if programmes_by_channel.get(channel_id):
            continue
        removed_ids.add(channel_id)
        rows.append(
            [
                label,
                "DROP_NO_EPG",
                channel_id,
                "",
                primary_name(channel),
                0,
                "no programme entries",
            ]
        )

    groups: dict[str, list[str]] = defaultdict(list)
    for channel_id, channel in channels.items():
        if channel_id in removed_ids:
            continue
        key = normalized_name(primary_name(channel))
        if key:
            groups[key].append(channel_id)

    for name_key, ids in groups.items():
        candidates = [channel_id for channel_id in ids if channel_id not in removed_ids]
        if len(candidates) < 2:
            continue

        ranked = sorted(
            candidates,
            key=lambda channel_id: winner_score(
                channel_id,
                channels[channel_id],
                len(programmes_by_channel.get(channel_id, [])),
                preserve,
            ),
            reverse=True,
        )

        for winner in ranked:
            if winner in removed_ids:
                continue
            for channel_id in ranked:
                if channel_id == winner or channel_id in removed_ids:
                    continue
                if channel_id in preserve:
                    continue

                coverage, jaccard, shared = similarity(
                    programmes_by_channel.get(winner, []),
                    programmes_by_channel.get(channel_id, []),
                )
                if (
                    shared < min_shared
                    or coverage < min_overlap
                    or jaccard < min_jaccard
                ):
                    continue

                aliases_added = add_aliases(channels[winner], channels[channel_id])
                rows.append(
                    [
                        label,
                        "DEDUPE_SAME_CHANNEL",
                        channel_id,
                        winner,
                        primary_name(channels[channel_id]),
                        len(programmes_by_channel.get(channel_id, [])),
                        (
                            f"name={name_key};coverage={coverage:.3f};"
                            f"jaccard={jaccard:.3f};shared={shared};"
                            f"aliases_added={aliases_added}"
                        ),
                    ]
                )
                removed_ids.add(channel_id)

    output_root = ET.Element(root.tag, dict(root.attrib))
    output_root.text = root.text

    for child in root:
        if child.tag not in {"channel", "programme"}:
            output_root.append(deepcopy(child))

    kept_ids = set(channels) - removed_ids
    for child in root.findall("channel"):
        channel_id = child.attrib.get("id", "")
        if channel_id in kept_ids:
            output_root.append(deepcopy(channels[channel_id]))

    for child in root.findall("programme"):
        if child.attrib.get("channel", "") in kept_ids:
            output_root.append(deepcopy(child))

    active_ids = {
        node.attrib.get("channel", "")
        for node in output_root.findall("programme")
        if node.attrib.get("channel", "")
    }
    inactive = kept_ids - active_ids
    if inactive:
        raise SystemExit(
            f"{path}: inactive channels remain after cleanup: {sorted(inactive)[:20]}"
        )
    unknown = active_ids - kept_ids
    if unknown:
        raise SystemExit(
            f"{path}: programme references unknown channels: {sorted(unknown)[:20]}"
        )

    write_xml_and_gzip(output_root, path)

    channel_count = len(kept_ids)
    programme_count = len(output_root.findall("programme"))
    return rows, channel_count, channel_count, programme_count


def update_platform_report(
    path: Path,
    counts: dict[str, tuple[int, int, int]],
) -> None:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames or []
        rows = list(reader)

    for row in rows:
        platform = row.get("platform", "")
        if platform not in counts:
            continue
        channel_count, active_count, programme_count = counts[platform]
        row["channel_count"] = str(channel_count)
        row["active_channel_count"] = str(active_count)
        row["programme_count"] = str(programme_count)

    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Remove channels without programmes and merge same-name channels "
            "with effectively identical schedules."
        )
    )
    parser.add_argument(
        "--file",
        action="append",
        type=parse_named_file,
        required=True,
        metavar="NAME=PATH",
    )
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--platform-report", type=Path)
    parser.add_argument("--preserve-id", action="append", default=[])
    parser.add_argument("--min-overlap-ratio", type=float, default=0.92)
    parser.add_argument("--min-jaccard-ratio", type=float, default=0.80)
    parser.add_argument("--min-shared-programmes", type=int, default=8)
    args = parser.parse_args()

    preserve = {value.strip() for value in args.preserve_id if value.strip()}
    all_rows: list[list[str | int | float]] = []
    platform_counts: dict[str, tuple[int, int, int]] = {}

    seen_labels: set[str] = set()
    for label, path in args.file:
        if label in seen_labels:
            raise SystemExit(f"Duplicate --file label: {label}")
        seen_labels.add(label)
        if not path.exists():
            raise SystemExit(f"Missing EPG file: {path}")

        rows, channels, active, programmes = clean_file(
            label=label,
            path=path,
            preserve=preserve,
            min_overlap=args.min_overlap_ratio,
            min_jaccard=args.min_jaccard_ratio,
            min_shared=args.min_shared_programmes,
        )
        all_rows.extend(rows)
        platform_counts[label] = (channels, active, programmes)
        print(
            f"Cleanup {label}: channels={channels} active={active} "
            f"programmes={programmes} removed={len(rows)}"
        )

    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "epg",
                "action",
                "xmltv_id",
                "kept_xmltv_id",
                "name",
                "programme_count",
                "details",
            ]
        )
        writer.writerows(all_rows)

    if args.platform_report:
        update_platform_report(args.platform_report, platform_counts)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
