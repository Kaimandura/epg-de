#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import re
import shutil
import xml.etree.ElementTree as ET
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def load_root(path: Path) -> ET.Element:
    if path.suffix == ".gz":
        with gzip.open(path, "rb") as handle:
            return ET.fromstring(handle.read())
    return ET.parse(path).getroot()


def extract(root: ET.Element) -> tuple[list[str], dict[str, ET.Element], dict[str, list[ET.Element]], list[ET.Element]]:
    order: list[str] = []
    channels: dict[str, ET.Element] = {}
    programmes: dict[str, list[ET.Element]] = defaultdict(list)
    extras: list[ET.Element] = []

    for child in root:
        if child.tag == "channel":
            channel_id = child.attrib.get("id", "").strip()
            if not channel_id:
                raise SystemExit("Master XMLTV contains a channel without id.")
            if channel_id in channels:
                raise SystemExit(f"Master XMLTV contains duplicate channel id: {channel_id}")
            channels[channel_id] = deepcopy(child)
            order.append(channel_id)
        elif child.tag == "programme":
            channel_id = child.attrib.get("channel", "").strip()
            if channel_id:
                programmes[channel_id].append(deepcopy(child))
        else:
            extras.append(deepcopy(child))

    return order, channels, programmes, extras


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
    text = re.sub(r"\[[^\]]+\]", " ", text)
    text = re.sub(r"\b(?:uhd|fhd|full\s*hd|hd|sd|4k)\b", " ", text)
    text = re.sub(r"[^0-9a-zäöüß+]+", " ", text)
    return " ".join(text.split())


def parse_xmltv_datetime(value: str) -> datetime | None:
    value = value.strip()
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
        tz = timezone(sign * timedelta(hours=hours, minutes=minutes))
        dt = dt.replace(tzinfo=tz)
    else:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def latest_programme_time(programmes: list[ET.Element]) -> datetime | None:
    latest: datetime | None = None
    for programme in programmes:
        value = programme.attrib.get("stop") or programme.attrib.get("start") or ""
        parsed = parse_xmltv_datetime(value)
        if parsed is not None and (latest is None or parsed > latest):
            latest = parsed
    return latest


def element_signature(node: ET.Element, ignore_channel: bool = False) -> tuple[Any, ...]:
    attrs = tuple(
        sorted(
            (key, value)
            for key, value in node.attrib.items()
            if not (ignore_channel and key == "channel")
        )
    )
    children = tuple(element_signature(child) for child in node)
    return (
        node.tag,
        attrs,
        (node.text or "").strip(),
        children,
    )


def schedule_signature(programmes: list[ET.Element]) -> str:
    digest = hashlib.sha256()
    ordered = sorted(
        programmes,
        key=lambda node: (
            node.attrib.get("start", ""),
            node.attrib.get("stop", ""),
            node.findtext("title") or "",
        ),
    )
    for programme in ordered:
        payload = repr(element_signature(programme, ignore_channel=True)).encode("utf-8")
        digest.update(payload)
        digest.update(b"\n")
    return digest.hexdigest()


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
        source_icon = source.find("icon")
        if source_icon is not None:
            target.append(deepcopy(source_icon))
    return added


def winner_score(channel_id: str, channel: ET.Element) -> tuple[int, int, str]:
    score = 0
    folded = channel_id.casefold()
    if ".de" in folded:
        score += 50
    if "@hd" in folded:
        score += 20
    elif "@sd" in folded:
        score += 10
    if channel.find("icon") is not None:
        score += 5
    return score, -len(channel_id), channel_id


def write_output(
    source_root: ET.Element,
    output: Path,
    order: list[str],
    channels: dict[str, ET.Element],
    programmes: dict[str, list[ET.Element]],
    extras: list[ET.Element],
) -> None:
    root = ET.Element(source_root.tag, dict(source_root.attrib))
    root.text = source_root.text

    for extra in extras:
        root.append(deepcopy(extra))

    for channel_id in order:
        channel = channels.get(channel_id)
        if channel is not None:
            root.append(channel)

    for channel_id in order:
        if channel_id not in channels:
            continue
        for programme in sorted(
            programmes.get(channel_id, []),
            key=lambda node: (
                node.attrib.get("start", ""),
                node.attrib.get("stop", ""),
                node.findtext("title") or "",
            ),
        ):
            programme.attrib["channel"] = channel_id
            root.append(programme)

    output.parent.mkdir(parents=True, exist_ok=True)
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(output, encoding="utf-8", xml_declaration=True)

    gzip_path = output.with_suffix(output.suffix + ".gz")
    with output.open("rb") as source, gzip_path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as target:
            shutil.copyfileobj(source, target)

    with gzip.open(gzip_path, "rb") as handle:
        roundtrip = ET.fromstring(handle.read())
    known = {
        node.attrib.get("id", "")
        for node in roundtrip.findall("channel")
        if node.attrib.get("id", "")
    }
    if len(known) != len(roundtrip.findall("channel")):
        raise SystemExit("Final master contains empty or duplicate channel IDs.")
    unknown = {
        node.attrib.get("channel", "")
        for node in roundtrip.findall("programme")
        if node.attrib.get("channel", "") not in known
    }
    if unknown:
        raise SystemExit(
            f"Final master contains programme references to unknown IDs: {sorted(unknown)[:10]}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Finalize the Germany master XMLTV: critical fallback, logos and conservative deduplication."
    )
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--skip-fallback", action="store_true")
    args = parser.parse_args()

    root = load_root(args.input)
    if root.tag != "tv":
        raise SystemExit(f"{args.input}: expected <tv> root")

    config = json.loads(args.config.read_text(encoding="utf-8"))
    order, channels, programmes, extras = extract(root)
    original_channels = len(channels)
    report_rows: list[list[Any]] = []

    baseline_channels: dict[str, ET.Element] = {}
    baseline_programmes: dict[str, list[ET.Element]] = defaultdict(list)
    if args.baseline and not args.skip_fallback:
        baseline_root = load_root(args.baseline)
        _, baseline_channels, baseline_programmes, _ = extract(baseline_root)

    restored = 0
    if not args.skip_fallback and baseline_channels:
        fallback_cfg = config.get("fallback", {})
        min_programmes = int(fallback_cfg.get("min_programmes", 1))
        min_future_hours = int(fallback_cfg.get("min_future_hours", 24))
        cutoff = datetime.now(timezone.utc) + timedelta(hours=min_future_hours)

        for channel_id in config.get("critical_fallback_ids", []):
            current_count = len(programmes.get(channel_id, []))
            if current_count >= min_programmes:
                continue

            baseline_list = baseline_programmes.get(channel_id, [])
            latest = latest_programme_time(baseline_list)
            if len(baseline_list) < min_programmes or latest is None or latest < cutoff:
                print(
                    f"Critical fallback unavailable/stale: {channel_id} "
                    f"current={current_count} baseline={len(baseline_list)} latest={latest}"
                )
                continue

            if channel_id not in channels:
                baseline_channel = baseline_channels.get(channel_id)
                if baseline_channel is None:
                    continue
                channels[channel_id] = deepcopy(baseline_channel)
                order.append(channel_id)

            programmes[channel_id] = [deepcopy(node) for node in baseline_list]
            restored += 1
            report_rows.append(
                [
                    "RESTORE_LAST_KNOWN_GOOD",
                    channel_id,
                    channel_id,
                    primary_name(channels[channel_id]),
                    len(baseline_list),
                    f"latest_stop={latest.isoformat()}",
                ]
            )
            print(
                f"Critical fallback restored: {channel_id} "
                f"({len(baseline_list)} programmes, latest={latest.isoformat()})"
            )

    icon_count = 0
    for channel_id, icon_url in config.get("icon_overrides", {}).items():
        channel = channels.get(channel_id)
        if channel is None:
            raise SystemExit(f"Required icon target is missing: {channel_id}")
        for icon in list(channel.findall("icon")):
            channel.remove(icon)
        channel.append(ET.Element("icon", {"src": str(icon_url)}))
        icon_count += 1
        report_rows.append(
            [
                "ICON_OVERRIDE",
                channel_id,
                channel_id,
                primary_name(channel),
                len(programmes.get(channel_id, [])),
                str(icon_url),
            ]
        )

    drop_prefixes = [str(value) for value in config.get("drop_prefixes_from_master", [])]
    dropped_platform = 0
    if drop_prefixes:
        for channel_id in list(order):
            if not any(channel_id.startswith(prefix) for prefix in drop_prefixes):
                continue
            channel = channels.pop(channel_id, None)
            count = len(programmes.pop(channel_id, []))
            if channel is not None:
                report_rows.append(
                    [
                        "DROP_PLATFORM_DUPLICATE_FROM_MASTER",
                        channel_id,
                        "",
                        primary_name(channel),
                        count,
                        "dedicated platform EPG retained",
                    ]
                )
                dropped_platform += 1
        order = [channel_id for channel_id in order if channel_id in channels]

    deduped = 0
    if bool(config.get("deduplicate_exact_schedules", True)):
        preserve = {str(value) for value in config.get("dedupe_preserve_ids", [])}
        groups: dict[str, list[str]] = defaultdict(list)
        for channel_id in order:
            name_key = normalized_name(primary_name(channels[channel_id]))
            if name_key:
                groups[name_key].append(channel_id)

        for name_key, ids in groups.items():
            if len(ids) < 2:
                continue
            if any(channel_id in preserve for channel_id in ids):
                continue

            by_signature: dict[str, list[str]] = defaultdict(list)
            for channel_id in ids:
                channel_programmes = programmes.get(channel_id, [])
                if not channel_programmes:
                    continue
                by_signature[schedule_signature(channel_programmes)].append(channel_id)

            for same_schedule in by_signature.values():
                if len(same_schedule) < 2:
                    continue
                winner = max(
                    same_schedule,
                    key=lambda channel_id: winner_score(channel_id, channels[channel_id]),
                )
                for channel_id in same_schedule:
                    if channel_id == winner or channel_id not in channels:
                        continue
                    removed = channels[channel_id]
                    alias_count = add_aliases(channels[winner], removed)
                    count = len(programmes.get(channel_id, []))
                    report_rows.append(
                        [
                            "DEDUPE_IDENTICAL_SCHEDULE",
                            channel_id,
                            winner,
                            primary_name(removed),
                            count,
                            f"normalized_name={name_key};aliases_added={alias_count}",
                        ]
                    )
                    channels.pop(channel_id, None)
                    programmes.pop(channel_id, None)
                    deduped += 1

        order = [channel_id for channel_id in order if channel_id in channels]

    for channel_id, icon_url in config.get("icon_overrides", {}).items():
        channel = channels.get(channel_id)
        if channel is None:
            raise SystemExit(f"Icon validation failed; channel was removed: {channel_id}")
        urls = {
            node.attrib.get("src", "")
            for node in channel.findall("icon")
            if node.attrib.get("src", "")
        }
        if str(icon_url) not in urls:
            raise SystemExit(f"Icon validation failed for {channel_id}")

    known = set(channels)
    unknown_refs = sorted(channel_id for channel_id in programmes if channel_id not in known)
    if unknown_refs:
        raise SystemExit(f"Programmes remain for removed/unknown channels: {unknown_refs[:10]}")

    write_output(root, args.output, order, channels, programmes, extras)

    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "action",
                "xmltv_id",
                "kept_xmltv_id",
                "name",
                "programme_count",
                "details",
            ]
        )
        writer.writerows(report_rows)

    active = sum(1 for channel_id in channels if programmes.get(channel_id))
    total_programmes = sum(len(programmes.get(channel_id, [])) for channel_id in channels)
    print(
        "Master finalization OK: "
        f"channels={original_channels}->{len(channels)} active={active} programmes={total_programmes} "
        f"platform_removed={dropped_platform} exact_deduped={deduped} "
        f"fallback_restored={restored} icons={icon_count}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
