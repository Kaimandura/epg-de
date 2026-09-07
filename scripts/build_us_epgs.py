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
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, BinaryIO


@dataclass(frozen=True)
class PlaylistChannel:
    xmltv_id: str
    name: str
    logo: str
    groups: str


def split_values(value: str) -> list[str]:
    return [part.strip() for part in re.split(r"[;,]", value or "") if part.strip()]


def as_bool(value: Any) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "y"}


def load_patterns(path: Path) -> list[re.Pattern[str]]:
    patterns: list[re.Pattern[str]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        patterns.append(re.compile(line, flags=re.IGNORECASE))
    if not patterns:
        raise RuntimeError(f"No patterns configured in {path}")
    return patterns


def clean_source_name(value: str) -> str:
    value = (value or "").strip()
    value = re.sub(r"^(?:US|USA)\s*[-:|]\s*", "", value, flags=re.IGNORECASE)
    while True:
        cleaned = re.sub(
            r"\s*(?:\[(?:geo[- ]?blocked|not\s+24/7|offline|duplicate)[^]]*\]"
            r"|\((?:\d{3,4}p|uhd|fhd|hd|sd|4k)\))\s*$",
            "",
            value,
            flags=re.IGNORECASE,
        ).strip()
        if cleaned == value:
            return cleaned
        value = cleaned


def compact_key(value: str) -> str:
    value = (value or "").replace("&", " and ").replace("+", " plus ")
    value = unicodedata.normalize("NFKD", value)
    value = "".join(char for char in value if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def id_stem(xmltv_id: str) -> str:
    base, _ = split_feed_id(xmltv_id)
    base = re.sub(
        r"\.(?:us|tv|com|net|org|plex|pluto|roku|samsung)$",
        "",
        base,
        flags=re.IGNORECASE,
    )
    return compact_key(base)


def is_generic_feed(feed: str) -> bool:
    value = compact_key(feed)
    return bool(
        re.fullmatch(
            r"(?:|sd|hd|fhd|uhd|4k|east|west|east(?:sd|hd|fhd|uhd|4k)"
            r"|west(?:sd|hd|fhd|uhd|4k)|us|usa|unitedstates|national|default"
            r"|english|en)",
            value,
        )
    )


def station_feed_key(feed: str) -> str:
    value = compact_key(feed)
    for suffix in ("television", "station", "channel", "tv", "dt", "ld", "cd", "lp"):
        if len(value) > len(suffix) + 2 and value.endswith(suffix):
            return value[: -len(suffix)]
    return value


def load_country_playlist(path: Path) -> list[PlaylistChannel]:
    entries: list[PlaylistChannel] = []
    seen: set[str] = set()
    for raw in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        if not raw.startswith("#EXTINF:"):
            continue
        attrs = dict(re.findall(r'([\w-]+)="([^"]*)"', raw))
        xmltv_id = (attrs.get("tvg-id") or "").strip()
        if not xmltv_id or xmltv_id in seen:
            continue
        seen.add(xmltv_id)
        label = raw.split(",", 1)[1] if "," in raw else xmltv_id
        entries.append(
            PlaylistChannel(
                xmltv_id=xmltv_id,
                name=clean_source_name(label) or xmltv_id,
                logo=(attrs.get("tvg-logo") or "").strip(),
                groups=(attrs.get("group-title") or "").strip(),
            )
        )
    if not entries:
        raise RuntimeError(f"No tvg-id entries found in {path}")
    return entries


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


def sanitize_channel(
    xmltv_id: str,
    channel: ET.Element,
    metadata: dict[str, dict[str, str]],
) -> None:
    names: list[str] = []
    seen: set[str] = set()

    metadata_id, row = metadata_for(xmltv_id, metadata)
    preferred = (row or {}).get("name", "") if metadata_id else ""
    for value in [preferred, *display_names(channel)]:
        cleaned = clean_source_name(value)
        key = cleaned.casefold()
        if cleaned and key not in seen:
            names.append(cleaned)
            seen.add(key)

    for node in list(channel.findall("display-name")):
        channel.remove(node)
    insert_at = 0
    for value in names or [xmltv_id]:
        node = ET.Element("display-name")
        node.text = value
        channel.insert(insert_at, node)
        insert_at += 1

    for icon in list(channel.findall("icon")):
        if not (icon.attrib.get("src") or "").strip():
            channel.remove(icon)


def sanitize_programme(programme: ET.Element) -> bool:
    channel_id = (programme.attrib.get("channel") or "").strip()
    start = (programme.attrib.get("start") or "").strip()
    if not channel_id or parse_xmltv_datetime(start) is None:
        return False

    titles = [
        node
        for node in programme.findall("title")
        if (node.text or "").strip()
    ]
    if not titles:
        return False

    programme.attrib["channel"] = channel_id
    programme.attrib["start"] = start
    stop = (programme.attrib.get("stop") or "").strip()
    if stop and parse_xmltv_datetime(stop) is None:
        programme.attrib.pop("stop", None)
    elif stop:
        programme.attrib["stop"] = stop

    for node in programme.iter():
        if node.text is not None:
            node.text = node.text.strip() or None
        if node.tail is not None:
            node.tail = node.tail.strip() or None

    for parent in programme.iter():
        for child in list(parent):
            tag = child.tag.rsplit("}", 1)[-1]
            if tag == "icon" and not (child.attrib.get("src") or "").strip():
                parent.remove(child)
                continue
            if not child.attrib and not list(child) and not (child.text or "").strip():
                parent.remove(child)
    return True


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


def is_national_channel(channel: ET.Element, patterns: list[re.Pattern[str]]) -> bool:
    names = display_names(channel)
    return any(pattern.search(clean_source_name(name)) for name in names for pattern in patterns)


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

    base, _ = split_feed_id(xmltv_id)
    if xmltv_id in fast_ids or base in fast_ids or any(pattern.search(blob) for pattern in fast_patterns):
        return "fast"

    if "sports" in categories or any(pattern.search(blob) for pattern in sports_patterns):
        return "sports"

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
    try:
        dt = datetime.strptime(raw, fmt)
        offset = match.group(2)
        if offset:
            sign = 1 if offset[0] == "+" else -1
            hours = int(offset[1:3])
            minutes = int(offset[3:5])
            dt = dt.replace(tzinfo=timezone(sign * timedelta(hours=hours, minutes=minutes)))
        else:
            dt = dt.replace(tzinfo=timezone.utc)
    except (ValueError, OverflowError):
        return None
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
    national_patterns: list[re.Pattern[str]],
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
                    sanitize_channel(channel_id, channel, metadata)
                    enrich_logo(channel_id, channel, metadata, logos)
                    channels[channel_id] = channel
                    category = classify_channel(
                        channel_id,
                        channel,
                        metadata,
                        fast_ids,
                        sports_patterns,
                        fast_patterns,
                    )
                    if category == "main" and not is_national_channel(channel, national_patterns):
                        category = "local"
                    categories[channel_id] = category
                element.clear()
            elif tag == "programme":
                # XMLTV channel declarations precede programme entries.  Stop here so
                # child nodes (display-name/icon) are not cleared before their parent
                # channel is copied and so the large programme section is not scanned
                # twice unnecessarily.
                break
    return channels, categories


def collect_schedule_stats(
    source: Path,
    channels: dict[str, ET.Element],
    categories: dict[str, str],
    now: datetime,
    past_hours: int,
    future_days: dict[str, int],
) -> tuple[dict[str, int], dict[str, str], int]:
    counts: dict[str, int] = defaultdict(int)
    digests: dict[str, hashlib._Hash] = {}  # type: ignore[attr-defined]
    seen: dict[str, set[bytes]] = defaultdict(set)
    rejected = 0
    with open_xml_stream(source) as handle:
        for _event, element in ET.iterparse(handle, events=("end",)):
            tag = element.tag.rsplit("}", 1)[-1]
            if tag != "programme":
                if tag == "channel":
                    element.clear()
                continue
            channel_id = (element.attrib.get("channel") or "").strip()
            category = categories.get(channel_id)
            if not category:
                element.clear()
                continue
            if not sanitize_programme(element):
                rejected += 1
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
    return (
        dict(counts),
        {channel_id: digest.hexdigest() for channel_id, digest in digests.items()},
        rejected,
    )


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


def alias_category(
    entry: PlaylistChannel,
    source_category: str,
    metadata: dict[str, dict[str, str]],
    fast_ids: set[str],
    sports_patterns: list[re.Pattern[str]],
    fast_patterns: list[re.Pattern[str]],
) -> str:
    base, feed = split_feed_id(entry.xmltv_id)
    _, row = metadata_for(entry.xmltv_id, metadata)
    blob = " ".join([entry.xmltv_id, entry.name, entry.groups])
    categories = {value.casefold() for value in split_values((row or {}).get("categories", ""))}
    identity_blob = " ".join(
        [
            (row or {}).get("network", ""),
            (row or {}).get("owners", ""),
            entry.name,
            entry.xmltv_id,
        ]
    )
    if (
        entry.xmltv_id in fast_ids
        or base in fast_ids
        or any(pattern.search(identity_blob) for pattern in fast_patterns)
    ):
        return "fast"

    if "sports" in categories or any(pattern.search(blob) for pattern in sports_patterns):
        return "sports"

    feed_key = compact_key(feed)
    if not is_generic_feed(feed) and re.match(r"^[kw][a-z0-9]{2,7}$", feed_key):
        return "local"
    return source_category


def build_alias_channel(
    entry: PlaylistChannel,
    source: ET.Element,
    metadata: dict[str, dict[str, str]],
    logos: dict[tuple[str, str], list[dict[str, str]]],
) -> ET.Element:
    channel = ET.Element("channel", {"id": entry.xmltv_id})
    base, _ = split_feed_id(entry.xmltv_id)
    _, row = metadata_for(entry.xmltv_id, metadata)
    values = [entry.name, (row or {}).get("name", ""), *display_names(source), base]
    seen: set[str] = set()
    for value in values:
        value = clean_source_name(value)
        key = value.casefold()
        if not value or key in seen:
            continue
        seen.add(key)
        node = ET.SubElement(channel, "display-name")
        node.text = value

    if entry.logo:
        channel.append(ET.Element("icon", {"src": entry.logo}))
    else:
        enrich_logo(entry.xmltv_id, channel, metadata, logos)
    ensure_display_name(channel, entry.xmltv_id)
    return channel


def map_playlist_channels(
    entries: list[PlaylistChannel],
    channels: dict[str, ET.Element],
    categories: dict[str, str],
    active_ids: set[str],
    metadata: dict[str, dict[str, str]],
    logos: dict[tuple[str, str], list[dict[str, str]]],
    fast_ids: set[str],
    sports_patterns: list[re.Pattern[str]],
    fast_patterns: list[re.Pattern[str]],
) -> tuple[
    dict[str, list[tuple[PlaylistChannel, str, str, ET.Element]]],
    list[dict[str, str]],
    list[dict[str, str]],
]:
    by_casefold = {channel_id.casefold(): channel_id for channel_id in active_ids}
    by_stem: dict[str, list[str]] = defaultdict(list)
    by_name: dict[str, list[str]] = defaultdict(list)
    for channel_id in sorted(active_ids):
        by_stem[id_stem(channel_id)].append(channel_id)
        for name in display_names(channels[channel_id]):
            key = compact_key(clean_source_name(name))
            if key:
                by_name[key].append(channel_id)

    aliases: dict[str, list[tuple[PlaylistChannel, str, str, ET.Element]]] = defaultdict(list)
    mapped_rows: list[dict[str, str]] = []
    unmapped_rows: list[dict[str, str]] = []

    for entry in entries:
        target_id = entry.xmltv_id
        base, feed = split_feed_id(target_id)
        source_id = ""
        method = ""

        exact = by_casefold.get(target_id.casefold())
        if exact:
            source_id = exact
            method = "exact-id"

        if not source_id and is_generic_feed(feed):
            exact_base = by_casefold.get(base.casefold())
            if exact_base:
                source_id = exact_base
                method = "base-id"

        if not source_id and is_generic_feed(feed):
            candidates = by_stem.get(id_stem(base), [])
            if len(candidates) == 1:
                source_id = candidates[0]
                method = "normalized-base-id"

        if not source_id and not is_generic_feed(feed):
            base_key = id_stem(base)
            feed_key = station_feed_key(feed)
            for key in (base_key + feed_key, feed_key + base_key):
                candidates = by_stem.get(key, [])
                if len(candidates) == 1:
                    source_id = candidates[0]
                    method = "base-feed-id"
                    break

        regional = compact_key(feed)
        if not source_id and regional in {
            "east",
            "west",
            "easthd",
            "westhd",
            "eastsd",
            "westsd",
        }:
            direction = "east" if regional.startswith("east") else "west"
            prefix = id_stem(base) + direction
            candidates = [
                channel_id
                for channel_id in active_ids
                if id_stem(channel_id).startswith(prefix)
            ]
            if len(candidates) == 1:
                source_id = candidates[0]
                method = "regional-feed-id"

        if not source_id and is_generic_feed(feed):
            name_key = compact_key(entry.name)
            candidates = by_name.get(name_key, []) if len(name_key) >= 4 else []
            if len(candidates) == 1:
                source_id = candidates[0]
                method = "playlist-name"

        if not source_id and is_generic_feed(feed):
            row = metadata.get(base, {})
            candidates: set[str] = set()
            values = [row.get("name", ""), *split_values(row.get("alt_names", ""))]
            for value in values:
                key = compact_key(value)
                matches = by_name.get(key, []) if len(key) >= 4 else []
                if len(matches) == 1:
                    candidates.update(matches)
            if len(candidates) == 1:
                source_id = next(iter(candidates))
                method = "metadata-name"

        if not source_id:
            unmapped_rows.append(
                {
                    "xmltv_id": target_id,
                    "display_name": entry.name,
                    "groups": entry.groups,
                }
            )
            continue

        category = alias_category(
            entry,
            categories[source_id],
            metadata,
            fast_ids,
            sports_patterns,
            fast_patterns,
        )
        alias = build_alias_channel(entry, channels[source_id], metadata, logos)
        aliases[source_id].append((entry, method, category, alias))
        mapped_rows.append(
            {
                "xmltv_id": target_id,
                "display_name": primary_name(alias, target_id),
                "category": category,
                "source_id": source_id,
                "source_display_name": primary_name(channels[source_id], source_id),
                "match_method": method,
            }
        )

    return dict(aliases), mapped_rows, unmapped_rows


def write_outputs(
    source: Path,
    output_dir: Path,
    outputs: dict[str, str],
    channels: dict[str, ET.Element],
    categories: dict[str, str],
    active_ids: set[str],
    aliases_by_source: dict[
        str,
        list[tuple[PlaylistChannel, str, str, ET.Element]],
    ],
    now: datetime,
    past_hours: int,
    future_days: dict[str, int],
    max_gzip_bytes: int,
) -> tuple[
    dict[str, tuple[int, int, int]],
    dict[str, tuple[ET.Element, str, str]],
    dict[str, int],
]:
    output_dir.mkdir(parents=True, exist_ok=True)
    handles: dict[str, Any] = {}
    counts: dict[str, int] = defaultdict(int)
    counts_by_output_id: dict[str, int] = defaultdict(int)
    published: dict[str, tuple[ET.Element, str, str]] = {}
    output_ids_by_source: dict[str, list[str]] = defaultdict(list)

    for source_id in sorted(active_ids):
        published[source_id] = (channels[source_id], categories[source_id], source_id)
        output_ids_by_source[source_id].append(source_id)
        for entry, _method, category, alias in aliases_by_source.get(source_id, []):
            target_id = entry.xmltv_id
            existing = published.get(target_id)
            if existing is not None and existing[2] != source_id:
                continue
            published[target_id] = (alias, category, source_id)
            if target_id not in output_ids_by_source[source_id]:
                output_ids_by_source[source_id].append(target_id)

    ids_by_category: dict[str, list[str]] = defaultdict(list)
    for output_id, (_channel, category, _source_id) in published.items():
        ids_by_category[category].append(output_id)

    for category, filename in outputs.items():
        path = output_dir / filename
        handle = path.open("w", encoding="utf-8", newline="\n")
        handles[category] = handle
        handle.write('<?xml version="1.0" encoding="utf-8"?>\n')
        handle.write('<tv generator-info-name="Kaimandura/epg-de USA">\n')
        for output_id in sorted(ids_by_category.get(category, [])):
            handle.write("  ")
            handle.write(
                ET.tostring(
                    published[output_id][0],
                    encoding="unicode",
                    short_empty_elements=True,
                )
            )
            handle.write("\n")

    seen: dict[str, set[bytes]] = defaultdict(set)
    try:
        with open_xml_stream(source) as source_handle:
            for _event, element in ET.iterparse(source_handle, events=("end",)):
                tag = element.tag.rsplit("}", 1)[-1]
                if tag != "programme":
                    if tag == "channel":
                        element.clear()
                    continue
                channel_id = (element.attrib.get("channel") or "").strip()
                if channel_id not in active_ids:
                    element.clear()
                    continue
                if not sanitize_programme(element):
                    element.clear()
                    continue
                signature = programme_signature(element)
                for output_id in output_ids_by_source.get(channel_id, []):
                    _channel, category, _source_id = published[output_id]
                    if not programme_in_window(
                        element,
                        now,
                        past_hours,
                        int(future_days.get(category, 7)),
                    ):
                        continue
                    if signature in seen[output_id]:
                        continue
                    seen[output_id].add(signature)
                    programme = deepcopy(element)
                    programme.attrib["channel"] = output_id
                    handles[category].write("  ")
                    handles[category].write(
                        ET.tostring(programme, encoding="unicode", short_empty_elements=True)
                    )
                    handles[category].write("\n")
                    counts[category] += 1
                    counts_by_output_id[output_id] += 1
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
    inactive_output_ids = sorted(set(published) - set(counts_by_output_id))
    if inactive_output_ids:
        preview = ", ".join(inactive_output_ids[:20])
        raise RuntimeError(
            "Published USA channel aliases without programmes: "
            f"{preview}" + (" ..." if len(inactive_output_ids) > 20 else "")
        )
    return results, published, dict(counts_by_output_id)


def write_mapping_reports(
    mapping_path: Path,
    unmapped_path: Path,
    mapped_rows: list[dict[str, str]],
    unmapped_rows: list[dict[str, str]],
    published: dict[str, tuple[ET.Element, str, str]],
    programme_counts: dict[str, int],
) -> None:
    mapping_path.parent.mkdir(parents=True, exist_ok=True)
    mapping_fields = [
        "xmltv_id",
        "display_name",
        "category",
        "source_id",
        "source_display_name",
        "match_method",
        "programme_count",
    ]
    with mapping_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=mapping_fields, lineterminator="\n")
        writer.writeheader()
        for row in sorted(mapped_rows, key=lambda value: value["xmltv_id"].casefold()):
            if row["xmltv_id"] not in published:
                continue
            output = dict(row)
            output["programme_count"] = str(programme_counts.get(row["xmltv_id"], 0))
            writer.writerow(output)

    unmapped_fields = ["xmltv_id", "display_name", "groups"]
    with unmapped_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=unmapped_fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(
            sorted(unmapped_rows, key=lambda value: value["xmltv_id"].casefold())
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build compact active-only USA XMLTV guides from a current USA aggregate source."
    )
    parser.add_argument("--channels-csv", required=True, type=Path)
    parser.add_argument("--logos-csv", required=True, type=Path)
    parser.add_argument("--country-playlist", required=True, type=Path)
    parser.add_argument("--national-patterns", required=True, type=Path)
    parser.add_argument("--fast-playlist-dir", type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--mapping-report", required=True, type=Path)
    parser.add_argument("--unmapped-report", required=True, type=Path)
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
    national_patterns = load_patterns(args.national_patterns)

    metadata = load_channel_metadata(args.channels_csv)
    logos = load_logos(args.logos_csv)
    fast_ids = load_fast_ids(args.fast_playlist_dir)
    playlist_entries = load_country_playlist(args.country_playlist)

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
        national_patterns,
    )
    if not channels:
        raise SystemExit("USA source contains no usable channel definitions")
    print(f"USA channel definitions accepted: {len(channels)}")

    now = datetime.now(timezone.utc)
    counts, schedule_hashes, rejected_programmes = collect_schedule_stats(
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

    aliases_by_source, mapped_rows, unmapped_rows = map_playlist_channels(
        playlist_entries,
        channels,
        categories,
        active_ids,
        metadata,
        logos,
        fast_ids,
        sports_patterns,
        fast_patterns,
    )

    results, published, programme_counts = write_outputs(
        source_path,
        args.output_dir,
        outputs,
        channels,
        categories,
        active_ids,
        aliases_by_source,
        now,
        past_hours,
        future_days,
        max_gzip_bytes,
    )

    write_mapping_reports(
        args.mapping_report,
        args.unmapped_report,
        mapped_rows,
        unmapped_rows,
        published,
        programme_counts,
    )

    mapped_active = sum(1 for row in mapped_rows if row["xmltv_id"] in published)

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
                "official_playlist_total",
                "official_playlist_mapped_active",
                "rejected_malformed_programmes",
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
                    len(playlist_entries),
                    mapped_active,
                    rejected_programmes,
                ]
            )
            print(
                f"USA {category}: channels={channel_count} programmes={programme_count} "
                f"gzip={gzip_size} bytes"
            )

    print(
        f"USA build complete: active={len(active_ids)} exact_schedule_deduped={deduped} "
        f"published_ids={len(published)} official_playlist_mapped={mapped_active}/"
        f"{len(playlist_entries)} rejected_programmes={rejected_programmes} "
        f"fast_ids={len(fast_ids)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
