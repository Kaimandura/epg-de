#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, BinaryIO


def split_values(value: str) -> list[str]:
    return [part.strip() for part in re.split(r"[;,]", value or "") if part.strip()]


def as_bool(value: Any) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "y"}


def load_channel_metadata(path: Path) -> dict[str, dict[str, str]]:
    rows: dict[str, dict[str, str]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            channel_id = (row.get("id") or "").strip()
            if channel_id:
                rows[channel_id] = {str(k): str(v or "").strip() for k, v in row.items()}
    return rows


def load_logos(path: Path) -> dict[tuple[str, str], list[dict[str, str]]]:
    rows: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            channel = (row.get("channel") or "").strip()
            url = (row.get("url") or "").strip()
            if not channel or not url:
                continue
            if str(row.get("in_use") or "TRUE").strip().casefold() in {"false", "0", "no"}:
                continue
            feed = (row.get("feed") or "").strip()
            rows[(channel, feed)].append({str(k): str(v or "").strip() for k, v in row.items()})

    def score(item: dict[str, str]) -> tuple[int, int, str]:
        try:
            width = int(item.get("width", "0") or 0)
            height = int(item.get("height", "0") or 0)
        except ValueError:
            width = height = 0
        return width * height, 1 if item.get("format") == "SVG" else 0, item.get("url", "")

    for key in rows:
        rows[key].sort(key=score, reverse=True)
    return rows


def load_fast_ids(directory: Path | None) -> set[str]:
    if directory is None or not directory.exists():
        return set()
    ids: set[str] = set()
    for path in sorted(directory.glob("*.m3u")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        ids.update(value.strip() for value in re.findall(r'tvg-id="([^"]+)"', text) if value.strip())
    return ids


def split_feed_id(xmltv_id: str) -> tuple[str, str]:
    if "@" not in xmltv_id:
        return xmltv_id, ""
    return tuple(xmltv_id.split("@", 1))  # type: ignore[return-value]


def metadata_for(
    xmltv_id: str,
    metadata: dict[str, dict[str, str]],
) -> tuple[str | None, dict[str, str] | None]:
    if xmltv_id in metadata:
        return xmltv_id, metadata[xmltv_id]
    base, _ = split_feed_id(xmltv_id)
    if base in metadata:
        return base, metadata[base]
    return None, None


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
    text = re.sub(r"\b(?:uhd|fhd|full\s*hd|hd|sd|4k|1080p|720p|576p|480p)\b", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def is_closed_or_nsfw(xmltv_id: str, metadata: dict[str, dict[str, str]]) -> bool:
    _, row = metadata_for(xmltv_id, metadata)
    if not row:
        return False
    if as_bool(row.get("is_nsfw")):
        return True
    return bool((row.get("closed") or "").strip())


def looks_like_local_station(xmltv_id: str, name: str) -> bool:
    base, _ = split_feed_id(xmltv_id)
    token = base.split(".", 1)[0]
    if re.fullmatch(r"[KW][A-Z]{2,5}(?:DT\d*)?", token, flags=re.IGNORECASE):
        return True
    return bool(re.search(r"\b(?:ABC|CBS|NBC|FOX|PBS|CW)\s+\d{1,2}\b", name, flags=re.IGNORECASE))


def classify_channel(
    xmltv_id: str,
    channel: ET.Element,
    metadata: dict[str, dict[str, str]],
    fast_ids: set[str],
    sports_patterns: list[re.Pattern[str]],
    fast_patterns: list[re.Pattern[str]],
) -> str:
    metadata_id, row = metadata_for(xmltv_id, metadata)
    names = display_names(channel)
    name = names[0] if names else xmltv_id
    blob = " ".join(names + [xmltv_id])
    categories = {value.casefold() for value in split_values((row or {}).get("categories", ""))}

    if "sports" in categories or any(pattern.search(blob) for pattern in sports_patterns):
        return "sports"

    base, _ = split_feed_id(xmltv_id)
    if xmltv_id in fast_ids or base in fast_ids or any(pattern.search(blob) for pattern in fast_patterns):
        return "fast"

    areas = split_values((row or {}).get("broadcast_area", ""))
    if any(area.startswith("s/US-") or area.startswith("ct/US-") for area in areas):
        return "local"
    if row and (row.get("country") or "").upper() == "US" and (
        (row.get("city") or "").strip() or (row.get("subdivision") or "").strip()
    ):
        return "local"
    if looks_like_local_station(xmltv_id, name):
        return "local"

    return "main"


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


def programme_in_window(
    programme: ET.Element,
    now: datetime,
    past_hours: int,
    future_days: int,
) -> bool:
    start = parse_xmltv_datetime(programme.attrib.get("start", ""))
    stop = parse_xmltv_datetime(programme.attrib.get("stop", ""))
    minimum = now - timedelta(hours=past_hours)
    maximum = now + timedelta(days=future_days)
    if stop is not None and stop < minimum:
        return False
    if start is not None and start > maximum:
        return False
    return True


def programme_signature(programme: ET.Element) -> bytes:
    payload = "\x1f".join(
        [
            programme.attrib.get("start", ""),
            programme.attrib.get("stop", ""),
            (programme.findtext("title") or "").strip(),
        ]
    ).encode("utf-8", errors="replace")
    return hashlib.blake2b(payload, digest_size=12).digest()


def open_xml_stream(path: Path) -> BinaryIO:
    with path.open("rb") as probe:
        magic = probe.read(2)
    if magic == b"\x1f\x8b":
        return gzip.open(path, "rb")
    return path.open("rb")


def download_source(
    urls: list[str],
    destination: Path,
    timeout: int,
    retries: int,
    max_bytes: int,
) -> tuple[str, int]:
    last_error: Exception | None = None
    destination.parent.mkdir(parents=True, exist_ok=True)
    for url in urls:
        if not url.lower().startswith("https://"):
            continue
        for attempt in range(1, retries + 1):
            try:
                request = urllib.request.Request(
                    url,
                    headers={"User-Agent": "Kaimandura-epg-de/USA-builder"},
                )
                total = 0
                with urllib.request.urlopen(request, timeout=timeout) as response, destination.open("wb") as out:
                    length = response.headers.get("Content-Length")
                    if length and int(length) > max_bytes:
                        raise RuntimeError(f"source too large: {length} > {max_bytes}")
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > max_bytes:
                            raise RuntimeError(f"source exceeds {max_bytes} bytes")
                        out.write(chunk)
                if total == 0:
                    raise RuntimeError("source downloaded zero bytes")
                return url, total
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, RuntimeError) as exc:
                last_error = exc
                destination.unlink(missing_ok=True)
                if attempt < retries:
                    time.sleep(min(8, attempt * 2))
    raise RuntimeError(f"all USA EPG sources failed: {last_error}")


def collect_channels(
    source: Path,
    metadata: dict[str, dict[str, str]],
    logos: dict[tuple[str, str], list[dict[str, str]]],
    fast_ids: set[str],
    sports_patterns: list[re.Pattern[str]],
    fast_patterns: list[re.Pattern[str]],
) -> tuple[dict[str, ET.Element], dict[str, str]]:
    channels: dict[str, ET.Element] = {}
    categories: dict[str, str] = {}
    with open_xml_stream(source) as handle:
        for _event, element in ET.iterparse(handle, events=("end",)):
            tag = element.tag.rsplit("}", 1)[-1]
            if tag == "channel":
                channel_id = (element.attrib.get("id") or "").strip()
                if channel_id and channel_id not in channels and not is_closed_or_nsfw(channel_id, metadata):
                    channel = deepcopy(element)
                    channel.attrib["id"] = channel_id
                    ensure_display_name(channel, channel_id)
                    enrich_logo(channel_id, channel, metadata, logos)
                    channels[channel_id] = channel
                    categories[channel_id] = classify_channel(
                        channel_id,
                        channel,
                        metadata,
                        fast_ids,
                        sports_patterns,
                        fast_patterns,
                    )
            element.clear()
    return channels, categories


def collect_schedule_stats(
    source: Path,
    channels: dict[str, ET.Element],
    categories: dict[str, str],
    now: datetime,
    past_hours: int,
    future_days: dict[str, int],
) -> tuple[dict[str, int], dict[str, str]]:
    counts: dict[str, int] = defaultdict(int)
    digests: dict[str, hashlib._Hash] = {}  # type: ignore[attr-defined]
    seen: dict[str, set[bytes]] = defaultdict(set)
    with open_xml_stream(source) as handle:
        for _event, element in ET.iterparse(handle, events=("end",)):
            tag = element.tag.rsplit("}", 1)[-1]
            if tag != "programme":
                element.clear()
                continue
            channel_id = (element.attrib.get("channel") or "").strip()
            category = categories.get(channel_id)
            if not category:
                element.clear()
                continue
            if not programme_in_window(
                element,
                now,
                past_hours,
                int(future_days.get(category, 7)),
            ):
                element.clear()
                continue
            signature = programme_signature(element)
            if signature in seen[channel_id]:
                element.clear()
                continue
            seen[channel_id].add(signature)
            digest = digests.setdefault(channel_id, hashlib.sha256())
            digest.update(signature)
            counts[channel_id] += 1
            element.clear()
    return dict(counts), {channel_id: digest.hexdigest() for channel_id, digest in digests.items()}


def merge_aliases(target: ET.Element, source: ET.Element) -> None:
    existing = {value.casefold() for value in display_names(target)}
    for node in source.findall("display-name"):
        value = (node.text or "").strip()
        if not value or value.casefold() in existing:
            continue
        target.append(deepcopy(node))
        existing.add(value.casefold())
    if target.find("icon") is None and source.find("icon") is not None:
        target.append(deepcopy(source.find("icon")))


def dedupe_active_channels(
    channels: dict[str, ET.Element],
    categories: dict[str, str],
    counts: dict[str, int],
    schedule_hashes: dict[str, str],
    metadata: dict[str, dict[str, str]],
    enabled: bool,
) -> tuple[set[str], int]:
    active = {channel_id for channel_id in channels if counts.get(channel_id, 0) > 0}
    if not enabled:
        return active, 0

    groups: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    for channel_id in active:
        name_key = normalized_name(primary_name(channels[channel_id], channel_id))
        schedule = schedule_hashes.get(channel_id, "")
        if name_key and schedule:
            groups[(categories[channel_id], name_key, schedule)].append(channel_id)

    removed = 0
    for ids in groups.values():
        if len(ids) < 2:
            continue

        def winner_score(channel_id: str) -> tuple[int, int, int, int, str]:
            metadata_id, _ = metadata_for(channel_id, metadata)
            return (
                1 if metadata_id else 0,
                1 if ".us" in channel_id.casefold() else 0,
                1 if channels[channel_id].find("icon") is not None else 0,
                -len(channel_id),
                channel_id,
            )

        winner = max(ids, key=winner_score)
        for channel_id in ids:
            if channel_id == winner or channel_id not in active:
                continue
            merge_aliases(channels[winner], channels[channel_id])
            active.remove(channel_id)
            removed += 1
    return active, removed


def write_outputs(
    source: Path,
    output_dir: Path,
    outputs: dict[str, str],
    channels: dict[str, ET.Element],
    categories: dict[str, str],
    active_ids: set[str],
    now: datetime,
    past_hours: int,
    future_days: dict[str, int],
    max_gzip_bytes: int,
) -> dict[str, tuple[int, int, int]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    handles: dict[str, Any] = {}
    counts: dict[str, int] = defaultdict(int)
    ids_by_category: dict[str, list[str]] = defaultdict(list)
    for channel_id in active_ids:
        ids_by_category[categories[channel_id]].append(channel_id)

    for category, filename in outputs.items():
        path = output_dir / filename
        handle = path.open("w", encoding="utf-8", newline="\n")
        handles[category] = handle
        handle.write('<?xml version="1.0" encoding="utf-8"?>\n')
        handle.write('<tv generator-info-name="Kaimandura/epg-de USA">\n')
        for channel_id in sorted(ids_by_category.get(category, [])):
            handle.write("  ")
            handle.write(ET.tostring(channels[channel_id], encoding="unicode", short_empty_elements=True))
            handle.write("\n")

    seen: dict[str, set[bytes]] = defaultdict(set)
    try:
        with open_xml_stream(source) as source_handle:
            for _event, element in ET.iterparse(source_handle, events=("end",)):
                tag = element.tag.rsplit("}", 1)[-1]
                if tag != "programme":
                    element.clear()
                    continue
                channel_id = (element.attrib.get("channel") or "").strip()
                if channel_id not in active_ids:
                    element.clear()
                    continue
                category = categories[channel_id]
                if not programme_in_window(
                    element,
                    now,
                    past_hours,
                    int(future_days.get(category, 7)),
                ):
                    element.clear()
                    continue
                signature = programme_signature(element)
                if signature in seen[channel_id]:
                    element.clear()
                    continue
                seen[channel_id].add(signature)
                handles[category].write("  ")
                handles[category].write(ET.tostring(element, encoding="unicode", short_empty_elements=True))
                handles[category].write("\n")
                counts[category] += 1
                element.clear()
    finally:
        for handle in handles.values():
            handle.write("</tv>\n")
            handle.close()

    results: dict[str, tuple[int, int, int]] = {}
    for category, filename in outputs.items():
        path = output_dir / filename
        gzip_path = path.with_suffix(path.suffix + ".gz")
        with path.open("rb") as source_handle, gzip_path.open("wb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as target:
                shutil.copyfileobj(source_handle, target)
        gzip_size = gzip_path.stat().st_size
        if gzip_size > max_gzip_bytes:
            raise RuntimeError(
                f"{gzip_path}: {gzip_size} bytes exceeds configured limit {max_gzip_bytes}"
            )
        results[category] = (
            len(ids_by_category.get(category, [])),
            counts.get(category, 0),
            gzip_size,
        )
    return results


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build compact active-only USA XMLTV guides from a current USA aggregate source."
    )
    parser.add_argument("--channels-csv", required=True, type=Path)
    parser.add_argument("--logos-csv", required=True, type=Path)
    parser.add_argument("--fast-playlist-dir", type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--work-dir", required=True, type=Path)
    args = parser.parse_args()

    config = json.loads(args.config.read_text(encoding="utf-8"))
    source_urls = [str(value) for value in config.get("source_urls", []) if str(value).strip()]
    if not source_urls:
        raise SystemExit("USA config must contain at least one source_urls entry")
    outputs = {str(k): str(v) for k, v in config.get("outputs", {}).items()}
    required = {"main", "sports", "local", "fast"}
    if set(outputs) != required:
        raise SystemExit(f"USA config outputs must be exactly {sorted(required)}")

    timeout = int(config.get("request_timeout_seconds", 120))
    retries = int(config.get("download_retries", 3))
    max_source_bytes = int(config.get("max_source_bytes", 512 * 1024 * 1024))
    max_gzip_bytes = int(config.get("max_output_gzip_bytes", 90 * 1024 * 1024))
    past_hours = int(config.get("programme_past_hours", 12))
    future_days = {
        "main": 7,
        "sports": 7,
        "local": 3,
        "fast": 3,
        **{str(k): int(v) for k, v in config.get("programme_future_days", {}).items()},
    }
    sports_patterns = [
        re.compile(str(value), flags=re.IGNORECASE)
        for value in config.get("sports_name_patterns", [])
    ]
    fast_patterns = [
        re.compile(str(value), flags=re.IGNORECASE)
        for value in config.get("fast_name_patterns", [])
    ]

    metadata = load_channel_metadata(args.channels_csv)
    logos = load_logos(args.logos_csv)
    fast_ids = load_fast_ids(args.fast_playlist_dir)

    args.work_dir.mkdir(parents=True, exist_ok=True)
    source_path = args.work_dir / "usa-source.xml.gz"
    source_url, source_bytes = download_source(
        source_urls,
        source_path,
        timeout,
        retries,
        max_source_bytes,
    )
    print(f"USA source downloaded: {source_url} ({source_bytes} bytes)")

    channels, categories = collect_channels(
        source_path,
        metadata,
        logos,
        fast_ids,
        sports_patterns,
        fast_patterns,
    )
    if not channels:
        raise SystemExit("USA source contains no usable channel definitions")
    print(f"USA channel definitions accepted: {len(channels)}")

    now = datetime.now(timezone.utc)
    counts, schedule_hashes = collect_schedule_stats(
        source_path,
        channels,
        categories,
        now,
        past_hours,
        future_days,
    )
    active_ids, deduped = dedupe_active_channels(
        channels,
        categories,
        counts,
        schedule_hashes,
        metadata,
        bool(config.get("deduplicate_exact_schedules", True)),
    )
    if not active_ids:
        raise SystemExit("USA source contains no current EPG programmes after filtering")

    results = write_outputs(
        source_path,
        args.output_dir,
        outputs,
        channels,
        categories,
        active_ids,
        now,
        past_hours,
        future_days,
        max_gzip_bytes,
    )

    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "category",
                "channel_count",
                "programme_count",
                "gzip_bytes",
                "source",
                "exact_schedule_deduped_total",
            ]
        )
        for category in ("main", "sports", "local", "fast"):
            channel_count, programme_count, gzip_size = results[category]
            writer.writerow(
                [
                    category,
                    channel_count,
                    programme_count,
                    gzip_size,
                    source_url,
                    deduped,
                ]
            )
            print(
                f"USA {category}: channels={channel_count} programmes={programme_count} "
                f"gzip={gzip_size} bytes"
            )

    print(
        f"USA build complete: active={len(active_ids)} exact_schedule_deduped={deduped} "
        f"fast_ids={len(fast_ids)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
