#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path


TIMESTAMP_RE = re.compile(r"^(?:\d{12}|\d{14})(?:\s+[+-]\d{4})?$")


def playlist_ids(path: Path) -> set[str]:
    ids: set[str] = set()
    for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        if not line.startswith("#EXTINF:"):
            continue
        match = re.search(r'tvg-id="([^"]+)"', line)
        if match and match.group(1).strip():
            ids.add(match.group(1).strip())
    return ids


def audit_xml(path: Path) -> tuple[dict[str, int], set[str], list[str]]:
    channels: set[str] = set()
    active: set[str] = set()
    signatures: set[bytes] = set()
    errors: list[str] = []
    programmes = 0
    logo_channels = 0

    try:
        iterator = ET.iterparse(path, events=("end",))
        for _event, element in iterator:
            tag = element.tag.rsplit("}", 1)[-1]
            if tag == "channel":
                channel_id = (element.attrib.get("id") or "").strip()
                if not channel_id:
                    errors.append("empty channel id")
                elif channel_id in channels:
                    errors.append(f"duplicate channel id: {channel_id}")
                else:
                    channels.add(channel_id)
                if not any((node.text or "").strip() for node in element.findall("display-name")):
                    errors.append(f"channel without display-name: {channel_id}")
                icons = element.findall("icon")
                if any((node.attrib.get("src") or "").strip() for node in icons):
                    logo_channels += 1
                if any(not (node.attrib.get("src") or "").strip() for node in icons):
                    errors.append(f"channel with empty icon src: {channel_id}")
                element.clear()
                continue

            if tag != "programme":
                continue

            programmes += 1
            channel_id = (element.attrib.get("channel") or "").strip()
            start = (element.attrib.get("start") or "").strip()
            stop = (element.attrib.get("stop") or "").strip()
            title = next(
                (
                    (node.text or "").strip()
                    for node in element.findall("title")
                    if (node.text or "").strip()
                ),
                "",
            )
            if channel_id not in channels:
                errors.append(f"programme references unknown channel: {channel_id}")
            else:
                active.add(channel_id)
            if not title:
                errors.append(f"programme without title: {channel_id} {start}")
            if not TIMESTAMP_RE.fullmatch(start):
                errors.append(f"invalid programme start: {channel_id} {start!r}")
            if stop and not TIMESTAMP_RE.fullmatch(stop):
                errors.append(f"invalid programme stop: {channel_id} {stop!r}")
            if any(
                not (node.attrib.get("src") or "").strip()
                for node in element.findall("icon")
            ):
                errors.append(f"programme with empty icon src: {channel_id} {start}")

            payload = "\x1f".join([channel_id, start, stop, title]).encode(
                "utf-8", errors="replace"
            )
            signature = hashlib.blake2b(payload, digest_size=16).digest()
            if signature in signatures:
                errors.append(f"duplicate programme: {channel_id} {start} {title}")
            signatures.add(signature)
            element.clear()
    except (ET.ParseError, OSError) as exc:
        errors.append(f"invalid XML: {exc}")

    inactive = sorted(channels - active)
    if inactive:
        errors.append(
            "channels without programmes: "
            + ", ".join(inactive[:20])
            + (" ..." if len(inactive) > 20 else "")
        )

    return (
        {
            "channels": len(channels),
            "active_channels": len(active),
            "programmes": programmes,
            "channels_with_logo": logo_channels,
        },
        channels,
        errors,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Hard quality gate for the four USA XMLTV outputs.")
    parser.add_argument("--main", required=True, type=Path)
    parser.add_argument("--sports", required=True, type=Path)
    parser.add_argument("--local", required=True, type=Path)
    parser.add_argument("--fast", required=True, type=Path)
    parser.add_argument("--coverage", required=True, type=Path)
    parser.add_argument("--mapping", required=True, type=Path)
    parser.add_argument("--playlist", required=True, type=Path)
    parser.add_argument("--summary", required=True, type=Path)
    parser.add_argument("--min-playlist-mapped", type=int, default=400)
    args = parser.parse_args()

    paths = {
        "main": args.main,
        "sports": args.sports,
        "local": args.local,
        "fast": args.fast,
    }
    summaries: dict[str, dict[str, int]] = {}
    ids_by_category: dict[str, set[str]] = {}
    errors: list[str] = []
    for category, path in paths.items():
        summary, channel_ids, file_errors = audit_xml(path)
        summaries[category] = summary
        ids_by_category[category] = channel_ids
        errors.extend(f"{category}: {message}" for message in file_errors[:100])

    categories = list(paths)
    for index, left in enumerate(categories):
        for right in categories[index + 1 :]:
            overlap = sorted(ids_by_category[left] & ids_by_category[right])
            if overlap:
                errors.append(
                    f"channel IDs overlap between {left} and {right}: "
                    + ", ".join(overlap[:20])
                )

    with args.coverage.open("r", encoding="utf-8-sig", newline="") as handle:
        coverage_rows = list(csv.DictReader(handle))
    coverage = {(row.get("category") or "").strip(): row for row in coverage_rows}
    for category, summary in summaries.items():
        row = coverage.get(category)
        if row is None:
            errors.append(f"coverage row missing: {category}")
            continue
        for field, key in (("channel_count", "channels"), ("programme_count", "programmes")):
            try:
                reported = int(row.get(field) or -1)
            except ValueError:
                reported = -1
            if reported != summary[key]:
                errors.append(
                    f"coverage mismatch for {category}.{field}: "
                    f"{reported} != {summary[key]}"
                )

    official_ids = playlist_ids(args.playlist)
    all_output_ids = set().union(*ids_by_category.values())
    with args.mapping.open("r", encoding="utf-8-sig", newline="") as handle:
        mapping_rows = list(csv.DictReader(handle))
    mapped_ids = [(row.get("xmltv_id") or "").strip() for row in mapping_rows]
    if len(mapped_ids) != len(set(mapped_ids)):
        errors.append("mapping report contains duplicate xmltv_id rows")
    unexpected = sorted(set(mapped_ids) - official_ids)
    if unexpected:
        errors.append("mapping report contains non-playlist IDs: " + ", ".join(unexpected[:20]))
    missing_outputs = sorted(set(mapped_ids) - all_output_ids)
    if missing_outputs:
        errors.append("mapped IDs missing from outputs: " + ", ".join(missing_outputs[:20]))
    if len(mapped_ids) < args.min_playlist_mapped:
        errors.append(
            f"official playlist mapping regression: {len(mapped_ids)} < "
            f"{args.min_playlist_mapped}"
        )
    for row in mapping_rows:
        try:
            programme_count = int(row.get("programme_count") or 0)
        except ValueError:
            programme_count = 0
        if programme_count < 1:
            errors.append(f"mapped ID without programmes: {row.get('xmltv_id', '')}")

    output = {
        "status": "failed" if errors else "passed",
        "hard_error_count": len(errors),
        "outputs": summaries,
        "official_playlist": {
            "total_ids": len(official_ids),
            "mapped_active_ids": len(mapped_ids),
            "coverage_percent": round(100 * len(mapped_ids) / max(1, len(official_ids)), 2),
        },
        "errors": errors[:200],
    }
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    if errors:
        for message in errors[:40]:
            print(f"ERROR: {message}")
        raise SystemExit(f"USA quality gate failed with {len(errors)} hard error(s)")

    print(
        "USA quality gate passed: "
        f"{sum(value['channels'] for value in summaries.values())} channels, "
        f"{sum(value['programmes'] for value in summaries.values())} programmes, "
        f"{len(mapped_ids)}/{len(official_ids)} official playlist IDs mapped"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
