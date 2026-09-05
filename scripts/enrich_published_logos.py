#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import gzip
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path


def split_values(value: str) -> list[str]:
    return [part.strip() for part in re.split(r"[;,]", value or "") if part.strip()]


def split_feed_id(xmltv_id: str) -> tuple[str, str]:
    if "@" not in xmltv_id:
        return xmltv_id, ""
    base, feed = xmltv_id.split("@", 1)
    return base, feed


def normalized_name(value: str) -> str:
    value = (value or "").strip()
    value = re.sub(r"^DE\s*(?:-|:|\|)\s*", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\[[^\]]+\]", " ", value)
    value = re.sub(r"\b(?:UHD|FHD|FULL\s*HD|HD|SD|4K|1080P|720P|576P|480P)\b", " ", value, flags=re.IGNORECASE)
    value = value.casefold()
    value = re.sub(r"[^0-9a-zäöüß+]+", " ", value)
    return " ".join(value.split())


def display_names(channel: ET.Element) -> list[str]:
    return [
        (node.text or "").strip()
        for node in channel.findall("display-name")
        if (node.text or "").strip()
    ]


def has_valid_icon(channel: ET.Element) -> bool:
    return any(
        (node.attrib.get("src") or "").strip().startswith(("http://", "https://"))
        for node in channel.findall("icon")
    )


def clean_empty_icons(channel: ET.Element) -> None:
    for node in list(channel.findall("icon")):
        src = (node.attrib.get("src") or "").strip()
        if not src:
            channel.remove(node)


def logo_score(row: dict[str, str]) -> tuple[int, int, int, str]:
    fmt = (row.get("format") or "").strip().upper()
    url = (row.get("url") or "").strip()
    raster = 1 if fmt in {"PNG", "JPG", "JPEG", "WEBP"} or re.search(
        r"\.(?:png|jpe?g|webp)(?:\?|$)", url, re.IGNORECASE
    ) else 0
    try:
        width = int(row.get("width", "0") or 0)
        height = int(row.get("height", "0") or 0)
    except ValueError:
        width = height = 0
    area = width * height
    square = -abs(width - height) if width and height else 0
    return raster, area, square, url


def load_logos(path: Path) -> dict[tuple[str, str], list[dict[str, str]]]:
    result: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            channel = (row.get("channel") or "").strip()
            url = (row.get("url") or "").strip()
            if not channel or not url or not url.startswith(("http://", "https://")):
                continue
            if (row.get("in_use") or "TRUE").strip().casefold() in {"false", "0", "no"}:
                continue
            feed = (row.get("feed") or "").strip()
            result[(channel, feed)].append(
                {str(k): str(v or "").strip() for k, v in row.items()}
            )

    for key in result:
        result[key].sort(key=logo_score, reverse=True)
    return result


def choose_logo(
    xmltv_id: str,
    logos: dict[tuple[str, str], list[dict[str, str]]],
) -> str | None:
    base, feed = split_feed_id(xmltv_id)
    keys = [(base, feed)]
    if feed:
        keys.append((base, ""))
    if xmltv_id != base:
        keys.append((xmltv_id, ""))
    for key in keys:
        rows = logos.get(key, [])
        if rows:
            url = (rows[0].get("url") or "").strip()
            if url:
                return url
    return None


def load_name_index(
    channels_csv: Path,
    logos: dict[tuple[str, str], list[dict[str, str]]],
) -> dict[str, set[str]]:
    logo_ids = {channel for channel, _feed in logos}
    index: dict[str, set[str]] = defaultdict(set)
    with channels_csv.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            channel_id = (row.get("id") or "").strip()
            if not channel_id or channel_id not in logo_ids:
                continue
            if (row.get("is_nsfw") or "").strip().casefold() in {"true", "1", "yes"}:
                continue
            if (row.get("closed") or "").strip():
                continue
            names = [(row.get("name") or "").strip()]
            names.extend(split_values(row.get("alt_names") or ""))
            for name in names:
                key = normalized_name(name)
                if key:
                    index[key].add(channel_id)
    return index


def choose_unique_name_logo(
    channel: ET.Element,
    name_index: dict[str, set[str]],
    logos: dict[tuple[str, str], list[dict[str, str]]],
) -> str | None:
    candidates: set[str] = set()
    for name in display_names(channel):
        key = normalized_name(name)
        if key:
            candidates.update(name_index.get(key, set()))
    if len(candidates) != 1:
        return None
    candidate_id = next(iter(candidates))
    return choose_logo(candidate_id, logos)


def load_root(path: Path) -> ET.Element:
    with gzip.open(path, "rb") as handle:
        root = ET.fromstring(handle.read())
    if root.tag != "tv":
        raise RuntimeError(f"{path}: XMLTV root is not <tv>")
    return root


def validate_active_only(root: ET.Element, label: str) -> None:
    channels = root.findall("channel")
    ids = [(node.attrib.get("id") or "").strip() for node in channels]
    if not all(ids) or len(ids) != len(set(ids)):
        raise RuntimeError(f"{label}: empty or duplicate channel IDs")
    known = set(ids)
    active: set[str] = set()
    for programme in root.findall("programme"):
        channel_id = (programme.attrib.get("channel") or "").strip()
        if channel_id not in known:
            raise RuntimeError(f"{label}: programme references unknown channel {channel_id}")
        active.add(channel_id)
    empty = known - active
    if empty:
        raise RuntimeError(
            f"{label}: empty channels must not be published: {sorted(empty)[:10]}"
        )


def write_gzip(root: ET.Element, path: Path) -> None:
    ET.indent(root, space="  ")
    payload = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as handle:
            handle.write(payload)
    tmp.replace(path)


def add_icon(channel: ET.Element, url: str) -> None:
    clean_empty_icons(channel)
    channel.append(ET.Element("icon", {"src": url}))


def load_amazon_mapping(path: Path | None) -> dict[str, str]:
    if path is None or not path.exists():
        return {}
    result: dict[str, str] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            target = (row.get("amazon_xmltv_id") or "").strip()
            source = (row.get("source_xmltv_id") or "").strip()
            status = (row.get("status") or "").strip()
            if target and source and status == "OK":
                result[target] = source
    return result


def enrich_file(
    path: Path,
    logos: dict[tuple[str, str], list[dict[str, str]]],
    name_index: dict[str, set[str]],
    source_icons: dict[str, str] | None = None,
    mapped_sources: dict[str, str] | None = None,
) -> tuple[ET.Element, dict[str, int]]:
    root = load_root(path)
    validate_active_only(root, path.name)
    stats = {
        "channels": 0,
        "existing": 0,
        "added_id": 0,
        "added_mapping": 0,
        "added_name": 0,
        "missing": 0,
    }

    for channel in root.findall("channel"):
        channel_id = (channel.attrib.get("id") or "").strip()
        stats["channels"] += 1
        clean_empty_icons(channel)
        if has_valid_icon(channel):
            stats["existing"] += 1
            continue

        url = choose_logo(channel_id, logos)
        if url:
            add_icon(channel, url)
            stats["added_id"] += 1
            continue

        source_id = (mapped_sources or {}).get(channel_id, "")
        if source_id:
            url = (source_icons or {}).get(source_id) or choose_logo(source_id, logos)
            if url:
                add_icon(channel, url)
                stats["added_mapping"] += 1
                continue

        url = choose_unique_name_logo(channel, name_index, logos)
        if url:
            add_icon(channel, url)
            stats["added_name"] += 1
            continue

        stats["missing"] += 1

    validate_active_only(root, path.name)
    write_gzip(root, path)
    return root, stats


def icon_map(root: ET.Element) -> dict[str, str]:
    result: dict[str, str] = {}
    for channel in root.findall("channel"):
        channel_id = (channel.attrib.get("id") or "").strip()
        for icon in channel.findall("icon"):
            src = (icon.attrib.get("src") or "").strip()
            if channel_id and src.startswith(("http://", "https://")):
                result[channel_id] = src
                break
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Enrich missing logos in published German XMLTV gzip files without changing channel/programme identity."
    )
    parser.add_argument("--logos-csv", required=True, type=Path)
    parser.add_argument("--channels-csv", required=True, type=Path)
    parser.add_argument("--master", required=True, type=Path)
    parser.add_argument("--samsung", type=Path)
    parser.add_argument("--pluto", type=Path)
    parser.add_argument("--amazon", type=Path)
    parser.add_argument("--amazon-report", type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()

    logos = load_logos(args.logos_csv)
    if not logos:
        raise RuntimeError("Logo database is empty")
    name_index = load_name_index(args.channels_csv, logos)

    rows: list[list[str | int | float]] = []

    master_root, master_stats = enrich_file(args.master, logos, name_index)
    master_icons = icon_map(master_root)

    def record(label: str, path: Path, stats: dict[str, int]) -> None:
        channels = stats["channels"]
        with_logo = channels - stats["missing"]
        coverage = round((with_logo / channels * 100.0), 2) if channels else 0.0
        rows.append([
            label,
            path.name,
            channels,
            with_logo,
            stats["existing"],
            stats["added_id"],
            stats["added_mapping"],
            stats["added_name"],
            stats["missing"],
            coverage,
        ])
        print(
            f"Logo enrichment {label}: channels={channels} with_logo={with_logo} "
            f"existing={stats['existing']} added_id={stats['added_id']} "
            f"added_mapping={stats['added_mapping']} added_name={stats['added_name']} "
            f"missing={stats['missing']} coverage={coverage:.2f}%"
        )

    record("master", args.master, master_stats)

    for label, path in (("samsung", args.samsung), ("pluto", args.pluto)):
        if path is None or not path.exists():
            continue
        _root, stats = enrich_file(path, logos, name_index)
        record(label, path, stats)

    if args.amazon is not None and args.amazon.exists():
        mapping = load_amazon_mapping(args.amazon_report)
        _root, stats = enrich_file(
            args.amazon,
            logos,
            name_index,
            source_icons=master_icons,
            mapped_sources=mapping,
        )
        record("amazon", args.amazon, stats)

    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "epg",
            "file",
            "channel_count",
            "with_logo",
            "existing_logo",
            "added_by_id",
            "added_by_mapping",
            "added_by_unique_name",
            "missing_logo",
            "coverage_percent",
        ])
        writer.writerows(rows)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
