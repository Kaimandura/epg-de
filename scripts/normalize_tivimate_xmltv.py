#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import gzip
import re
import shutil
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path


CANONICAL_XMLTV_TIME = re.compile(r"^\d{14} [+-]\d{4}$")
CATEGORY_BY_STEM = {
    "usa": "main",
    "usa-sports": "sports",
    "usa-local": "local",
    "usa-fast": "fast",
}


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


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
            dt = dt.replace(
                tzinfo=timezone(sign * timedelta(hours=hours, minutes=minutes))
            )
        else:
            dt = dt.replace(tzinfo=timezone.utc)
    except (ValueError, OverflowError):
        return None
    return dt.astimezone(timezone.utc)


def canonical_time(value: str) -> str | None:
    dt = parse_xmltv_datetime(value)
    if dt is None:
        return None
    return dt.strftime("%Y%m%d%H%M%S +0000")


def clean_text(value: str | None) -> str:
    return (value or "").strip()


def copy_lang(source: ET.Element, target: ET.Element) -> None:
    lang = clean_text(source.attrib.get("lang"))
    if lang:
        target.attrib["lang"] = lang


def safe_channel(source: ET.Element) -> ET.Element | None:
    channel_id = clean_text(source.attrib.get("id"))
    if not channel_id:
        return None

    target = ET.Element("channel", {"id": channel_id})
    seen_names: set[tuple[str, str]] = set()

    for child in list(source):
        if local_name(child.tag) != "display-name":
            continue
        text = clean_text(child.text)
        if not text:
            continue
        lang = clean_text(child.attrib.get("lang"))
        key = (text.casefold(), lang.casefold())
        if key in seen_names:
            continue
        seen_names.add(key)
        node = ET.SubElement(target, "display-name")
        node.text = text
        if lang:
            node.attrib["lang"] = lang

    if not seen_names:
        node = ET.SubElement(target, "display-name")
        node.text = channel_id

    for child in list(source):
        if local_name(child.tag) != "icon":
            continue
        src = clean_text(child.attrib.get("src"))
        if src:
            ET.SubElement(target, "icon", {"src": src})
            break

    return target


def safe_rating(source: ET.Element) -> ET.Element | None:
    value = ""
    icon_src = ""
    for child in list(source):
        tag = local_name(child.tag)
        if tag == "value" and not value:
            value = clean_text(child.text)
        elif tag == "icon" and not icon_src:
            icon_src = clean_text(child.attrib.get("src"))
    if not value and not icon_src:
        return None

    attrs: dict[str, str] = {}
    system = clean_text(source.attrib.get("system"))
    if system:
        attrs["system"] = system
    target = ET.Element("rating", attrs)
    if value:
        node = ET.SubElement(target, "value")
        node.text = value
    if icon_src:
        ET.SubElement(target, "icon", {"src": icon_src})
    return target


def safe_programme(source: ET.Element) -> ET.Element | None:
    channel_id = clean_text(source.attrib.get("channel"))
    start = canonical_time(clean_text(source.attrib.get("start")))
    if not channel_id or start is None:
        return None

    attrs = {"start": start, "channel": channel_id}
    stop_raw = clean_text(source.attrib.get("stop"))
    if stop_raw:
        stop = canonical_time(stop_raw)
        if stop is not None:
            attrs["stop"] = stop

    target = ET.Element("programme", attrs)
    has_title = False

    for child in list(source):
        tag = local_name(child.tag)

        if tag in {"title", "sub-title", "desc", "category", "keyword", "country"}:
            text = clean_text(child.text)
            if not text:
                continue
            node = ET.SubElement(target, tag)
            node.text = text
            copy_lang(child, node)
            if tag == "title":
                has_title = True
            continue

        if tag == "date":
            text = clean_text(child.text)
            if text:
                node = ET.SubElement(target, "date")
                node.text = text
            continue

        if tag == "episode-num":
            text = clean_text(child.text)
            if not text:
                continue
            attrs_episode: dict[str, str] = {}
            system = clean_text(child.attrib.get("system"))
            if system:
                attrs_episode["system"] = system
            node = ET.SubElement(target, "episode-num", attrs_episode)
            node.text = text
            continue

        if tag == "icon":
            src = clean_text(child.attrib.get("src"))
            if src:
                ET.SubElement(target, "icon", {"src": src})
            continue

        if tag == "rating":
            rating = safe_rating(child)
            if rating is not None:
                target.append(rating)
            continue

    if not has_title:
        return None
    return target


def serialize(element: ET.Element) -> str:
    return ET.tostring(
        element,
        encoding="unicode",
        short_empty_elements=True,
    )


def normalize_one(path: Path) -> tuple[int, int, int]:
    if not path.exists():
        raise RuntimeError(f"missing XMLTV file: {path}")

    channel_ids: set[str] = set()
    programme_count = 0
    rejected_programmes = 0

    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as out:
        tmp_path = Path(out.name)
        out.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        out.write('<tv generator-info-name="Kaimandura/epg-de">\n')

        try:
            for _event, element in ET.iterparse(path, events=("end",)):
                tag = local_name(element.tag)

                if tag == "channel":
                    channel = safe_channel(element)
                    if channel is not None:
                        channel_id = channel.attrib["id"]
                        if channel_id not in channel_ids:
                            channel_ids.add(channel_id)
                            out.write("  ")
                            out.write(serialize(channel))
                            out.write("\n")
                    element.clear()
                    continue

                if tag == "programme":
                    programme = safe_programme(element)
                    if programme is None:
                        rejected_programmes += 1
                        element.clear()
                        continue
                    channel_id = programme.attrib["channel"]
                    if channel_id not in channel_ids:
                        raise RuntimeError(
                            f"{path}: programme references undeclared channel {channel_id!r}"
                        )
                    out.write("  ")
                    out.write(serialize(programme))
                    out.write("\n")
                    programme_count += 1
                    element.clear()

            out.write("</tv>\n")
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

    if not channel_ids:
        tmp_path.unlink(missing_ok=True)
        raise RuntimeError(f"{path}: no channels after TiviMate normalization")
    if programme_count == 0:
        tmp_path.unlink(missing_ok=True)
        raise RuntimeError(f"{path}: no programmes after TiviMate normalization")

    ET.parse(tmp_path)

    strict_channels = 0
    strict_programmes = 0
    for _event, element in ET.iterparse(tmp_path, events=("end",)):
        tag = local_name(element.tag)
        if tag == "channel":
            if element.tag != "channel":
                raise RuntimeError(f"{path}: namespaced channel survived normalization")
            if not clean_text(element.attrib.get("id")):
                raise RuntimeError(f"{path}: channel without id")
            names = [
                clean_text(child.text)
                for child in list(element)
                if local_name(child.tag) == "display-name"
            ]
            if not any(names):
                raise RuntimeError(f"{path}: channel without display-name")
            strict_channels += 1
            element.clear()
        elif tag == "programme":
            if element.tag != "programme":
                raise RuntimeError(f"{path}: namespaced programme survived normalization")
            start = clean_text(element.attrib.get("start"))
            stop = clean_text(element.attrib.get("stop"))
            if not CANONICAL_XMLTV_TIME.fullmatch(start):
                raise RuntimeError(f"{path}: non-canonical start timestamp {start!r}")
            if stop and not CANONICAL_XMLTV_TIME.fullmatch(stop):
                raise RuntimeError(f"{path}: non-canonical stop timestamp {stop!r}")
            if clean_text(element.attrib.get("channel")) not in channel_ids:
                raise RuntimeError(f"{path}: programme channel not declared")
            strict_programmes += 1
            element.clear()

    if strict_channels != len(channel_ids) or strict_programmes != programme_count:
        raise RuntimeError(
            f"{path}: strict verification mismatch "
            f"channels={strict_channels}/{len(channel_ids)} "
            f"programmes={strict_programmes}/{programme_count}"
        )

    tmp_path.replace(path)

    gzip_path = path.with_suffix(path.suffix + ".gz")
    with path.open("rb") as source, gzip_path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as target:
            shutil.copyfileobj(source, target)

    with gzip.open(gzip_path, "rb") as check:
        prefix = check.read(128)
        if b"<tv " not in prefix and b"<tv>" not in prefix:
            raise RuntimeError(f"{gzip_path}: invalid XMLTV prefix after gzip")

    return len(channel_ids), programme_count, rejected_programmes


def update_coverage(path: Path, stats: dict[str, tuple[int, int, int]]) -> None:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
        fields = list(rows[0].keys()) if rows else []

    required = {"category", "channel_count", "programme_count", "gzip_bytes"}
    if not rows or not required.issubset(fields):
        raise RuntimeError(f"{path}: unsupported USA coverage report")

    for row in rows:
        category = clean_text(row.get("category"))
        if category not in stats:
            continue
        channels, programmes, gzip_bytes = stats[category]
        row["channel_count"] = str(channels)
        row["programme_count"] = str(programmes)
        row["gzip_bytes"] = str(gzip_bytes)

    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Rewrite generated USA XMLTV files to a conservative TiviMate-safe "
            "subset with canonical timestamps and no XML namespaces."
        )
    )
    parser.add_argument("xml", nargs="+", type=Path)
    parser.add_argument("--coverage", type=Path)
    args = parser.parse_args()

    coverage_stats: dict[str, tuple[int, int, int]] = {}
    for path in args.xml:
        channels, programmes, rejected = normalize_one(path)
        gzip_path = path.with_suffix(path.suffix + ".gz")
        category = CATEGORY_BY_STEM.get(path.stem)
        if category:
            coverage_stats[category] = (
                channels,
                programmes,
                gzip_path.stat().st_size,
            )
        print(
            f"TiviMate normalized {path}: channels={channels} "
            f"programmes={programmes} rejected={rejected} "
            f"gzip={gzip_path.stat().st_size}"
        )

    if args.coverage:
        update_coverage(args.coverage, coverage_stats)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
