#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import re
import shutil
import time
import unicodedata
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


@dataclass
class GuideChoice:
    channel: ET.Element
    programmes: list[ET.Element]
    site: str
    group: str
    url: str


def split_values(value: str) -> list[str]:
    return [part.strip() for part in re.split(r"[;,]", value or "") if part.strip()]


def as_bool(value: Any) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "y"}


def load_channel_metadata(path: Path) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            channel_id = (row.get("id") or "").strip()
            if channel_id:
                result[channel_id] = {str(k): str(v or "").strip() for k, v in row.items()}
    return result


def load_logos(path: Path) -> dict[tuple[str, str], list[dict[str, str]]]:
    result: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            channel = (row.get("channel") or "").strip()
            url = (row.get("url") or "").strip()
            if not channel or not url:
                continue
            if str(row.get("in_use") or "TRUE").strip().casefold() in {"false", "0", "no"}:
                continue
            feed = (row.get("feed") or "").strip()
            result[(channel, feed)].append({str(k): str(v or "").strip() for k, v in row.items()})

    def score(item: dict[str, str]) -> tuple[int, int, str]:
        try:
            width = int(item.get("width", "0") or 0)
            height = int(item.get("height", "0") or 0)
        except ValueError:
            width = height = 0
        return width * height, 1 if item.get("format") == "SVG" else 0, item.get("url", "")

    for key in result:
        result[key].sort(key=score, reverse=True)
    return result


def split_feed_id(xmltv_id: str) -> tuple[str, str]:
    if "@" not in xmltv_id:
        return xmltv_id, ""
    base, feed = xmltv_id.split("@", 1)
    return base, feed


def metadata_for(xmltv_id: str, metadata: dict[str, dict[str, str]]) -> tuple[str | None, dict[str, str] | None]:
    if xmltv_id in metadata:
        return xmltv_id, metadata[xmltv_id]
    base, _ = split_feed_id(xmltv_id)
    if base in metadata:
        return base, metadata[base]
    return None, None


def parse_xmltv_datetime(value: str) -> datetime | None:
    value = (value or "").strip()
    match = re.match(r"^(\d{12}|\d{14})(?:\s*([+-]\d{4}))?", value)
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
        dt = dt.replace(tzinfo=timezone(sign * timedelta(hours=hours, minutes=minutes)))
    else:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def filter_programmes(
    programmes: list[ET.Element],
    minimum_time: datetime,
    maximum_time: datetime,
) -> list[ET.Element]:
    result: list[ET.Element] = []
    seen: set[tuple[str, str, str]] = set()
    for programme in programmes:
        start_text = programme.attrib.get("start", "")
        stop_text = programme.attrib.get("stop", "")
        start = parse_xmltv_datetime(start_text)
        stop = parse_xmltv_datetime(stop_text)
        if stop is not None and stop < minimum_time:
            continue
        if start is not None and start > maximum_time:
            continue
        signature = (start_text, stop_text, (programme.findtext("title") or "").strip())
        if signature in seen:
            continue
        seen.add(signature)
        result.append(deepcopy(programme))
    result.sort(
        key=lambda node: (
            node.attrib.get("start", ""),
            node.attrib.get("stop", ""),
            node.findtext("title") or "",
        )
    )
    return result


def guide_group(url: str) -> str | None:
    path = urlparse(url).path
    match = re.search(r"/epg/guides/([^/]+)/", path)
    return match.group(1) if match else None


def is_selected_group(group: str | None, prefix: str) -> bool:
    return bool(group and (group == prefix or group.startswith(prefix + "-")))


def fetch_url(url: str, timeout: int, retries: int, max_bytes: int) -> bytes:
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            request = urllib.request.Request(
                url,
                headers={"User-Agent": "Kaimandura-epg-de/USA-builder"},
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                length = response.headers.get("Content-Length")
                if length and int(length) > max_bytes:
                    raise RuntimeError(f"guide exceeds compressed limit: {length} > {max_bytes}")
                data = response.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise RuntimeError(f"guide exceeds compressed limit: > {max_bytes}")
            return data
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, RuntimeError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(min(8, attempt * 2))
    raise RuntimeError(f"download failed after {retries} attempts: {last_error}")


def decode_xml(data: bytes, max_uncompressed_bytes: int) -> bytes:
    if data[:2] != b"\x1f\x8b":
        if len(data) > max_uncompressed_bytes:
            raise RuntimeError("uncompressed guide exceeds size limit")
        return data
    with gzip.GzipFile(fileobj=io.BytesIO(data), mode="rb") as handle:
        payload = handle.read(max_uncompressed_bytes + 1)
    if len(payload) > max_uncompressed_bytes:
        raise RuntimeError("expanded guide exceeds size limit")
    return payload


def parse_guide(
    xml_bytes: bytes,
    minimum_time: datetime,
    maximum_time: datetime,
    min_programmes: int,
) -> tuple[dict[str, ET.Element], dict[str, list[ET.Element]]]:
    root = ET.fromstring(xml_bytes)
    if root.tag != "tv":
        raise RuntimeError("guide root is not <tv>")

    channels: dict[str, ET.Element] = {}
    raw_programmes: dict[str, list[ET.Element]] = defaultdict(list)
    for channel in root.findall("channel"):
        channel_id = (channel.attrib.get("id") or "").strip()
        if channel_id and channel_id not in channels:
            channels[channel_id] = deepcopy(channel)
    for programme in root.findall("programme"):
        channel_id = (programme.attrib.get("channel") or "").strip()
        if channel_id:
            raw_programmes[channel_id].append(programme)

    programmes: dict[str, list[ET.Element]] = {}
    for channel_id, items in raw_programmes.items():
        filtered = filter_programmes(items, minimum_time, maximum_time)
        if len(filtered) >= min_programmes:
            programmes[channel_id] = filtered
    return channels, programmes


def source_priority(site: str, ordered: list[str]) -> int:
    try:
        return ordered.index(site)
    except ValueError:
        return len(ordered) + 100


def better_choice(new: GuideChoice, old: GuideChoice | None, priorities: list[str]) -> bool:
    if old is None:
        return True
    if len(new.programmes) != len(old.programmes):
        return len(new.programmes) > len(old.programmes)
    new_priority = source_priority(new.site, priorities)
    old_priority = source_priority(old.site, priorities)
    if new_priority != old_priority:
        return new_priority < old_priority
    return new.url < old.url


def display_names(channel: ET.Element) -> list[str]:
    return [
        (node.text or "").strip()
        for node in channel.findall("display-name")
        if (node.text or "").strip()
    ]


def primary_name(channel: ET.Element, fallback: str) -> str:
    names = display_names(channel)
    return names[0] if names else fallback


def ensure_display_name(channel: ET.Element, fallback: str) -> None:
    if display_names(channel):
        return
    node = ET.SubElement(channel, "display-name")
    node.text = fallback


def enrich_logo(
    xmltv_id: str,
    channel: ET.Element,
    metadata: dict[str, dict[str, str]],
    logos: dict[tuple[str, str], list[dict[str, str]]],
) -> None:
    if channel.find("icon") is not None:
        return
    metadata_id, _ = metadata_for(xmltv_id, metadata)
    if not metadata_id:
        return
    _, feed = split_feed_id(xmltv_id)
    candidates = logos.get((metadata_id, feed), []) or logos.get((metadata_id, ""), [])
    if not candidates:
        return
    url = candidates[0].get("url", "").strip()
    if url:
        channel.append(ET.Element("icon", {"src": url}))


def normalized_name(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.casefold()
    text = re.sub(r"\[[^\]]+\]", " ", text)
    text = re.sub(r"\b(?:uhd|fhd|full\s*hd|hd|sd|4k)\b", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def schedule_signature(programmes: list[ET.Element]) -> str:
    digest = hashlib.sha256()
    for programme in programmes:
        digest.update(programme.attrib.get("start", "").encode("utf-8"))
        digest.update(b"|")
        digest.update(programme.attrib.get("stop", "").encode("utf-8"))
        digest.update(b"|")
        digest.update((programme.findtext("title") or "").strip().encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def merge_aliases(target: ET.Element, source: ET.Element) -> int:
    existing = {value.casefold() for value in display_names(target)}
    added = 0
    for node in source.findall("display-name"):
        value = (node.text or "").strip()
        if not value or value.casefold() in existing:
            continue
        target.append(deepcopy(node))
        existing.add(value.casefold())
        added += 1
    if target.find("icon") is None and source.find("icon") is not None:
        target.append(deepcopy(source.find("icon")))
    return added


def dedupe_choices(
    choices: dict[str, GuideChoice],
    enabled: bool,
) -> tuple[dict[str, GuideChoice], int]:
    if not enabled:
        return choices, 0

    groups: dict[str, list[str]] = defaultdict(list)
    for channel_id, choice in choices.items():
        key = normalized_name(primary_name(choice.channel, channel_id))
        if key:
            groups[key].append(channel_id)

    removed = 0
    for ids in groups.values():
        if len(ids) < 2:
            continue
        by_schedule: dict[str, list[str]] = defaultdict(list)
        for channel_id in ids:
            if channel_id in choices:
                by_schedule[schedule_signature(choices[channel_id].programmes)].append(channel_id)
        for identical in by_schedule.values():
            if len(identical) < 2:
                continue

            def winner_score(channel_id: str) -> tuple[int, int, int, str]:
                choice = choices[channel_id]
                has_us = 1 if ".us" in channel_id.casefold() else 0
                has_icon = 1 if choice.channel.find("icon") is not None else 0
                return has_us, has_icon, -len(channel_id), channel_id

            winner_id = max(identical, key=winner_score)
            winner = choices[winner_id]
            for channel_id in identical:
                if channel_id == winner_id or channel_id not in choices:
                    continue
                merge_aliases(winner.channel, choices[channel_id].channel)
                del choices[channel_id]
                removed += 1
    return choices, removed


def is_closed_or_nsfw(xmltv_id: str, metadata: dict[str, dict[str, str]]) -> bool:
    _, row = metadata_for(xmltv_id, metadata)
    if not row:
        return False
    if as_bool(row.get("is_nsfw")):
        return True
    return bool((row.get("closed") or "").strip())


def classify(
    channel_id: str,
    choice: GuideChoice,
    metadata: dict[str, dict[str, str]],
    sports_patterns: list[re.Pattern[str]],
    fast_markers: list[str],
) -> str:
    metadata_id, row = metadata_for(channel_id, metadata)
    categories = {value.casefold() for value in split_values((row or {}).get("categories", ""))}
    names = display_names(choice.channel)
    name_blob = " ".join(names + [channel_id])

    if "sports" in categories or any(pattern.search(name_blob) for pattern in sports_patterns):
        return "sports"

    lower_group = choice.group.casefold()
    lower_site = choice.site.casefold()
    if any(marker.casefold() in lower_group or marker.casefold() in lower_site for marker in fast_markers):
        return "fast"

    areas = split_values((row or {}).get("broadcast_area", ""))
    if choice.group.casefold().startswith("us-local"):
        return "local"
    if any(area.startswith("s/US-") or area.startswith("ct/US-") for area in areas):
        return "local"
    if row and (row.get("country") or "").upper() == "US" and (
        (row.get("city") or "").strip() or (row.get("subdivision") or "").strip()
    ):
        return "local"

    return "main"


def validate_output(root: ET.Element, label: str) -> tuple[int, int]:
    channels = root.findall("channel")
    ids = [(node.attrib.get("id") or "").strip() for node in channels]
    if not all(ids) or len(ids) != len(set(ids)):
        raise RuntimeError(f"{label}: empty or duplicate channel IDs")
    known = set(ids)
    programmes = root.findall("programme")
    active = {(node.attrib.get("channel") or "").strip() for node in programmes}
    unknown = active - known
    if unknown:
        raise RuntimeError(f"{label}: programme references unknown channels: {sorted(unknown)[:10]}")
    empty = known - active
    if empty:
        raise RuntimeError(f"{label}: contains channels without EPG: {sorted(empty)[:10]}")
    return len(known), len(programmes)


def write_output(
    path: Path,
    choices: dict[str, GuideChoice],
    max_gzip_bytes: int,
) -> tuple[int, int, int]:
    root = ET.Element("tv", {"generator-info-name": "Kaimandura/epg-de USA"})
    for channel_id in sorted(choices):
        channel = deepcopy(choices[channel_id].channel)
        channel.attrib["id"] = channel_id
        ensure_display_name(channel, channel_id)
        root.append(channel)

    for channel_id in sorted(choices):
        for source in choices[channel_id].programmes:
            programme = deepcopy(source)
            programme.attrib["channel"] = channel_id
            root.append(programme)

    channel_count, programme_count = validate_output(root, path.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(path, encoding="utf-8", xml_declaration=True)

    gzip_path = path.with_suffix(path.suffix + ".gz")
    with path.open("rb") as source, gzip_path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as target:
            shutil.copyfileobj(source, target)

    gzip_size = gzip_path.stat().st_size
    if gzip_size > max_gzip_bytes:
        raise RuntimeError(
            f"{gzip_path}: {gzip_size} bytes exceeds configured limit {max_gzip_bytes}"
        )
    with gzip.open(gzip_path, "rb") as handle:
        roundtrip = ET.fromstring(handle.read())
    validate_output(roundtrip, gzip_path.name)
    return channel_count, programme_count, gzip_size


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build compact active-only USA XMLTV guides from current iptv-org US guide feeds."
    )
    parser.add_argument("--guides-index", required=True, type=Path)
    parser.add_argument("--channels-csv", required=True, type=Path)
    parser.add_argument("--logos-csv", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()

    config = json.loads(args.config.read_text(encoding="utf-8"))
    prefix = str(config.get("guide_group_prefix", "us"))
    allowed_host = str(config.get("allowed_host", "iptv-org.github.io"))
    timeout = int(config.get("request_timeout_seconds", 90))
    retries = int(config.get("download_retries", 3))
    max_guide_bytes = int(config.get("max_guide_bytes", 150 * 1024 * 1024))
    max_uncompressed = int(config.get("max_uncompressed_guide_bytes", 512 * 1024 * 1024))
    max_gzip_bytes = int(config.get("max_output_gzip_bytes", 90 * 1024 * 1024))
    min_programmes = int(config.get("min_programmes_per_channel", 1))
    past_hours = int(config.get("programme_past_hours", 12))
    future_days = int(config.get("programme_future_days", 7))
    priorities = [str(value) for value in config.get("source_priority", [])]
    fast_markers = [str(value) for value in config.get("fast_group_markers", [])]
    sports_patterns = [
        re.compile(str(value), flags=re.IGNORECASE)
        for value in config.get("sports_name_patterns", [])
    ]
    outputs = {str(k): str(v) for k, v in config.get("outputs", {}).items()}
    required_categories = {"main", "sports", "local", "fast"}
    if set(outputs) != required_categories:
        raise SystemExit(f"USA config outputs must be exactly {sorted(required_categories)}")

    metadata = load_channel_metadata(args.channels_csv)
    logos = load_logos(args.logos_csv)
    index = json.loads(args.guides_index.read_text(encoding="utf-8"))
    if not isinstance(index, list):
        raise SystemExit("guides index must be a JSON array")

    url_info: dict[str, tuple[str, str]] = {}
    for item in index:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url", "")).strip()
        site = str(item.get("site", "")).strip()
        if not url:
            continue
        parsed = urlparse(url)
        group = guide_group(url)
        if parsed.scheme != "https" or parsed.hostname != allowed_host:
            continue
        if not is_selected_group(group, prefix):
            continue
        if url not in url_info:
            url_info[url] = (site, group or prefix)

    if not url_info:
        raise SystemExit("No current iptv-org USA guide URLs were found in guides.json")

    now = datetime.now(timezone.utc)
    minimum_time = now - timedelta(hours=past_hours)
    maximum_time = now + timedelta(days=future_days)
    best: dict[str, GuideChoice] = {}
    source_rows: list[list[Any]] = []
    downloaded = failed = 0

    sorted_urls = sorted(
        url_info,
        key=lambda url: (source_priority(url_info[url][0], priorities), url),
    )
    print(f"USA guide sources selected: {len(sorted_urls)}")
    for position, url in enumerate(sorted_urls, start=1):
        site, group = url_info[url]
        try:
            compressed = fetch_url(url, timeout, retries, max_guide_bytes)
            xml_bytes = decode_xml(compressed, max_uncompressed)
            channels, programmes = parse_guide(
                xml_bytes, minimum_time, maximum_time, min_programmes
            )
            active = 0
            programme_total = 0
            for channel_id, items in programmes.items():
                channel = channels.get(channel_id)
                if channel is None:
                    channel = ET.Element("channel", {"id": channel_id})
                    ensure_display_name(channel, channel_id)
                if is_closed_or_nsfw(channel_id, metadata):
                    continue
                choice = GuideChoice(
                    channel=deepcopy(channel),
                    programmes=items,
                    site=site,
                    group=group,
                    url=url,
                )
                if better_choice(choice, best.get(channel_id), priorities):
                    best[channel_id] = choice
                active += 1
                programme_total += len(items)
            downloaded += 1
            source_rows.append(
                ["SOURCE", f"{group}/{site}", "OK", active, programme_total, len(compressed), url]
            )
            print(
                f"[{position}/{len(sorted_urls)}] OK {group}/{site}: "
                f"active={active} programmes={programme_total} compressed={len(compressed)}"
            )
        except Exception as exc:
            failed += 1
            source_rows.append(
                ["SOURCE", f"{group}/{site}", "FAILED", 0, 0, 0, f"{url} | {exc}"]
            )
            print(f"[{position}/{len(sorted_urls)}] FAILED {group}/{site}: {exc}")

    if downloaded == 0 or not best:
        raise SystemExit("No USA guide source produced usable EPG data")

    for channel_id, choice in best.items():
        enrich_logo(channel_id, choice.channel, metadata, logos)
        ensure_display_name(choice.channel, channel_id)

    best, deduped = dedupe_choices(
        best, bool(config.get("deduplicate_exact_schedules", True))
    )

    categories: dict[str, dict[str, GuideChoice]] = {
        "main": {},
        "sports": {},
        "local": {},
        "fast": {},
    }
    for channel_id, choice in best.items():
        category = classify(
            channel_id,
            choice,
            metadata,
            sports_patterns,
            fast_markers,
        )
        categories[category][channel_id] = choice

    args.output_dir.mkdir(parents=True, exist_ok=True)
    report_rows = list(source_rows)
    for category in ("main", "sports", "local", "fast"):
        filename = outputs[category]
        output_path = args.output_dir / filename
        channel_count, programme_count, gzip_size = write_output(
            output_path, categories[category], max_gzip_bytes
        )
        report_rows.append(
            [
                "OUTPUT",
                category,
                "OK",
                channel_count,
                programme_count,
                gzip_size,
                f"{filename}.gz; exact_schedule_deduped_total={deduped}",
            ]
        )
        print(
            f"USA {category}: channels={channel_count} programmes={programme_count} "
            f"gzip={gzip_size} bytes"
        )

    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "kind",
                "name",
                "status",
                "channel_count",
                "programme_count",
                "bytes",
                "details",
            ]
        )
        writer.writerows(report_rows)

    print(
        f"USA build complete: sources_ok={downloaded} sources_failed={failed} "
        f"active_unique={len(best)} exact_schedule_deduped={deduped}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
