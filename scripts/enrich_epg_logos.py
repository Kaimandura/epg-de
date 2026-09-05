#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import gzip
import re
import shutil
import unicodedata
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LogoCandidate:
    channel_id: str
    feed: str
    url: str
    width: int
    height: int
    fmt: str


def split_values(value: str) -> list[str]:
    return [part.strip() for part in re.split(r"[;,]", value or "") if part.strip()]


def as_bool(value: str) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "y"}


def split_feed_id(xmltv_id: str) -> tuple[str, str]:
    if "@" not in xmltv_id:
        return xmltv_id, ""
    return tuple(xmltv_id.split("@", 1))  # type: ignore[return-value]


def normalized_name(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.casefold()
    text = re.sub(r"^de\s*(?:-|:|\|)\s*", "", text)
    text = re.sub(r"\[[^\]]+\]", " ", text)
    text = re.sub(r"\b(?:uhd|fhd|full\s*hd|hd|sd|4k|1080p|720p|576p|480p)\b", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def display_names(channel: ET.Element) -> list[str]:
    return [
        (node.text or "").strip()
        for node in channel.findall("display-name")
        if (node.text or "").strip()
    ]


def load_channel_name_index(path: Path) -> tuple[set[str], dict[str, set[str]]]:
    known_ids: set[str] = set()
    names: dict[str, set[str]] = defaultdict(set)

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            channel_id = (row.get("id") or "").strip()
            if not channel_id:
                continue
            if as_bool(row.get("is_nsfw") or ""):
                continue
            if (row.get("closed") or "").strip():
                continue

            known_ids.add(channel_id)
            values = [(row.get("name") or "").strip()] + split_values(row.get("alt_names") or "")
            for value in values:
                key = normalized_name(value)
                if key:
                    names[key].add(channel_id)

    return known_ids, names


def safe_int(value: str) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def load_logos(path: Path) -> dict[tuple[str, str], list[LogoCandidate]]:
    index: dict[tuple[str, str], list[LogoCandidate]] = defaultdict(list)

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            channel_id = (row.get("channel") or "").strip()
            feed = (row.get("feed") or "").strip()
            url = (row.get("url") or "").strip()
            if not channel_id or not url or not url.lower().startswith("https://"):
                continue
            if str(row.get("in_use") or "TRUE").strip().casefold() in {"false", "0", "no"}:
                continue
            candidate = LogoCandidate(
                channel_id=channel_id,
                feed=feed,
                url=url,
                width=safe_int(row.get("width") or "0"),
                height=safe_int(row.get("height") or "0"),
                fmt=(row.get("format") or "").strip().upper(),
            )
            index[(channel_id, feed)].append(candidate)

    format_rank = {
        "PNG": 5,
        "WEBP": 4,
        "JPEG": 3,
        "JPG": 3,
        "SVG": 2,
        "": 1,
    }

    def score(candidate: LogoCandidate) -> tuple[int, int, int, str]:
        area = candidate.width * candidate.height
        nonzero = 1 if area > 0 else 0
        return (
            nonzero,
            area,
            format_rank.get(candidate.fmt, 0),
            candidate.url,
        )

    for key in index:
        index[key].sort(key=score, reverse=True)
    return index


def best_logo(
    channel_id: str,
    logos: dict[tuple[str, str], list[LogoCandidate]],
) -> str | None:
    base, feed = split_feed_id(channel_id)
    keys = []
    if feed:
        keys.append((base, feed))
    keys.extend([(channel_id, ""), (base, "")])
    seen: set[tuple[str, str]] = set()
    for key in keys:
        if key in seen:
            continue
        seen.add(key)
        candidates = logos.get(key, [])
        if candidates:
            return candidates[0].url
    return None


def unique_name_match(
    channel: ET.Element,
    name_index: dict[str, set[str]],
) -> str | None:
    matched: set[str] = set()
    for value in display_names(channel):
        key = normalized_name(value)
        if not key:
            continue
        ids = name_index.get(key, set())
        if len(ids) == 1:
            matched.update(ids)
    return next(iter(matched)) if len(matched) == 1 else None


def write_xml_and_gzip(root: ET.Element, xml_path: Path) -> None:
    ET.indent(root, space="  ")
    payload = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    xml_path.write_bytes(payload)
    gzip_path = xml_path.with_suffix(xml_path.suffix + ".gz")
    with gzip_path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as target:
            target.write(payload)
    with gzip.open(gzip_path, "rb") as handle:
        if handle.read() != payload:
            raise RuntimeError(f"gzip roundtrip mismatch: {gzip_path}")


def enrich_file(
    path: Path,
    known_ids: set[str],
    name_index: dict[str, set[str]],
    logos: dict[tuple[str, str], list[LogoCandidate]],
) -> dict[str, int | str]:
    root = ET.parse(path).getroot()
    if root.tag != "tv":
        raise RuntimeError(f"{path}: expected <tv> root")

    channels = root.findall("channel")
    original_ids = [node.attrib.get("id", "") for node in channels]
    if not all(original_ids) or len(original_ids) != len(set(original_ids)):
        raise RuntimeError(f"{path}: empty or duplicate channel IDs before logo enrichment")

    programme_refs_before = [node.attrib.get("channel", "") for node in root.findall("programme")]
    before = 0
    exact_added = 0
    name_added = 0

    for channel in channels:
        existing = [
            node.attrib.get("src", "").strip()
            for node in channel.findall("icon")
            if node.attrib.get("src", "").strip()
        ]
        if existing:
            before += 1
            continue

        xmltv_id = (channel.attrib.get("id") or "").strip()
        logo_url: str | None = None
        matched_id: str | None = None

        base, _feed = split_feed_id(xmltv_id)
        if xmltv_id in known_ids or base in known_ids:
            matched_id = xmltv_id if xmltv_id in known_ids else base
            logo_url = best_logo(xmltv_id, logos)
            if logo_url is None and matched_id != xmltv_id:
                logo_url = best_logo(matched_id, logos)
            if logo_url:
                exact_added += 1
        else:
            matched_id = unique_name_match(channel, name_index)
            if matched_id:
                logo_url = best_logo(matched_id, logos)
                if logo_url:
                    name_added += 1

        if logo_url:
            channel.append(ET.Element("icon", {"src": logo_url}))

    after = sum(
        1
        for channel in channels
        if any(
            node.attrib.get("src", "").strip()
            for node in channel.findall("icon")
        )
    )

    write_xml_and_gzip(root, path)

    roundtrip = ET.parse(path).getroot()
    ids_after = [node.attrib.get("id", "") for node in roundtrip.findall("channel")]
    refs_after = [node.attrib.get("channel", "") for node in roundtrip.findall("programme")]
    if ids_after != original_ids:
        raise RuntimeError(f"{path}: logo enrichment changed channel IDs/order")
    if refs_after != programme_refs_before:
        raise RuntimeError(f"{path}: logo enrichment changed programme references/order")

    return {
        "file": path.name,
        "channel_count": len(channels),
        "with_logo_before": before,
        "logos_added_exact": exact_added,
        "logos_added_unique_name": name_added,
        "with_logo_after": after,
        "missing_logo_after": len(channels) - after,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fill missing XMLTV channel logos from iptv-org/database without changing channel identity or schedules."
    )
    parser.add_argument("--channels-csv", required=True, type=Path)
    parser.add_argument("--logos-csv", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args()

    known_ids, name_index = load_channel_name_index(args.channels_csv)
    logos = load_logos(args.logos_csv)

    rows: list[dict[str, int | str]] = []
    for path in args.files:
        if not path.exists():
            raise RuntimeError(f"EPG file not found: {path}")
        row = enrich_file(path, known_ids, name_index, logos)
        rows.append(row)
        print(
            "Logo enrichment: "
            f"{row['file']} channels={row['channel_count']} "
            f"before={row['with_logo_before']} "
            f"added_exact={row['logos_added_exact']} "
            f"added_name={row['logos_added_unique_name']} "
            f"after={row['with_logo_after']} "
            f"missing={row['missing_logo_after']}"
        )

    args.report.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "file",
        "channel_count",
        "with_logo_before",
        "logos_added_exact",
        "logos_added_unique_name",
        "with_logo_after",
        "missing_logo_after",
    ]
    with args.report.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
