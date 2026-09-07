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

TIME_RE = re.compile(r"(?P<stamp>\d{14}|\d{12})(?:\s*(?P<offset>[+-]\d{4}))?")
STRICT_TIME_RE = re.compile(r"\d{14} [+-]\d{4}")
CATEGORY_BY_STEM = {
    "usa": "main",
    "usa-sports": "sports",
    "usa-local": "local",
    "usa-fast": "fast",
}
TEXT_TAGS = {"title", "sub-title", "desc", "category", "keyword", "country", "date"}


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def text(value: str | None) -> str:
    return (value or "").strip()


def parse_time(value: str) -> datetime | None:
    match = TIME_RE.fullmatch(text(value))
    if not match:
        return None

    stamp = match.group("stamp")
    try:
        dt = datetime.strptime(
            stamp,
            "%Y%m%d%H%M%S" if len(stamp) == 14 else "%Y%m%d%H%M",
        )
        offset = match.group("offset")
        if offset:
            sign = 1 if offset[0] == "+" else -1
            delta = timedelta(hours=int(offset[1:3]), minutes=int(offset[3:5]))
            dt = dt.replace(tzinfo=timezone(sign * delta))
        else:
            dt = dt.replace(tzinfo=timezone.utc)
    except (ValueError, OverflowError):
        return None
    return dt.astimezone(timezone.utc)


def canonical_time(value: str) -> str | None:
    parsed = parse_time(value)
    return parsed.strftime("%Y%m%d%H%M%S +0000") if parsed else None


def clean_channel(source: ET.Element) -> ET.Element | None:
    channel_id = text(source.attrib.get("id"))
    if not channel_id:
        return None

    target = ET.Element("channel", {"id": channel_id})
    seen: set[tuple[str, str]] = set()

    for child in source:
        if local_name(child.tag) != "display-name":
            continue
        value = text(child.text)
        lang = text(child.attrib.get("lang"))
        key = (value.casefold(), lang.casefold())
        if not value or key in seen:
            continue
        seen.add(key)
        attrs = {"lang": lang} if lang else {}
        node = ET.SubElement(target, "display-name", attrs)
        node.text = value

    if not seen:
        ET.SubElement(target, "display-name").text = channel_id

    for child in source:
        if local_name(child.tag) == "icon":
            src = text(child.attrib.get("src"))
            if src:
                ET.SubElement(target, "icon", {"src": src})
                break

    return target


def copy_text_node(source: ET.Element, target: ET.Element, tag: str) -> bool:
    value = text(source.text)
    if not value:
        return False
    attrs: dict[str, str] = {}
    lang = text(source.attrib.get("lang"))
    if lang:
        attrs["lang"] = lang
    if tag == "episode-num":
        system = text(source.attrib.get("system"))
        if system:
            attrs["system"] = system
    node = ET.SubElement(target, tag, attrs)
    node.text = value
    return True


def copy_rating(source: ET.Element, target: ET.Element) -> None:
    value = ""
    icon = ""
    for child in source:
        child_tag = local_name(child.tag)
        if child_tag == "value" and not value:
            value = text(child.text)
        elif child_tag == "icon" and not icon:
            icon = text(child.attrib.get("src"))
    if not value and not icon:
        return
    attrs: dict[str, str] = {}
    system = text(source.attrib.get("system"))
    if system:
        attrs["system"] = system
    rating = ET.SubElement(target, "rating", attrs)
    if value:
        ET.SubElement(rating, "value").text = value
    if icon:
        ET.SubElement(rating, "icon", {"src": icon})


def clean_programme(source: ET.Element) -> ET.Element | None:
    channel_id = text(source.attrib.get("channel"))
    start = canonical_time(text(source.attrib.get("start")))
    if not channel_id or start is None:
        return None

    attrs = {"start": start, "channel": channel_id}
    stop_raw = text(source.attrib.get("stop"))
    if stop_raw:
        stop = canonical_time(stop_raw)
        if stop:
            attrs["stop"] = stop

    target = ET.Element("programme", attrs)
    has_title = False

    for child in source:
        tag = local_name(child.tag)
        if tag in TEXT_TAGS or tag == "episode-num":
            copied = copy_text_node(child, target, tag)
            if tag == "title" and copied:
                has_title = True
        elif tag == "icon":
            src = text(child.attrib.get("src"))
            if src:
                ET.SubElement(target, "icon", {"src": src})
        elif tag == "rating":
            copy_rating(child, target)

    return target if has_title else None


def xml(element: ET.Element) -> str:
    return ET.tostring(element, encoding="unicode", short_empty_elements=True)


def verify(path: Path, channel_ids: set[str], expected_programmes: int) -> None:
    parsed_channels = 0
    parsed_programmes = 0

    for _event, element in ET.iterparse(path, events=("end",)):
        tag = local_name(element.tag)
        if tag == "channel":
            if element.tag != "channel":
                raise RuntimeError(f"{path}: namespaced channel in normalized output")
            channel_id = text(element.attrib.get("id"))
            if not channel_id:
                raise RuntimeError(f"{path}: channel without id")
            if not any(
                text(child.text)
                for child in element
                if local_name(child.tag) == "display-name"
            ):
                raise RuntimeError(f"{path}: {channel_id} has no display-name")
            parsed_channels += 1
            element.clear()
        elif tag == "programme":
            if element.tag != "programme":
                raise RuntimeError(f"{path}: namespaced programme in normalized output")
            channel_id = text(element.attrib.get("channel"))
            start = text(element.attrib.get("start"))
            stop = text(element.attrib.get("stop"))
            if channel_id not in channel_ids:
                raise RuntimeError(f"{path}: programme references unknown channel {channel_id}")
            if not STRICT_TIME_RE.fullmatch(start):
                raise RuntimeError(f"{path}: non-canonical start {start!r}")
            if stop and not STRICT_TIME_RE.fullmatch(stop):
                raise RuntimeError(f"{path}: non-canonical stop {stop!r}")
            parsed_programmes += 1
            element.clear()

    if parsed_channels != len(channel_ids):
        raise RuntimeError(
            f"{path}: channel verification mismatch {parsed_channels}/{len(channel_ids)}"
        )
    if parsed_programmes != expected_programmes:
        raise RuntimeError(
            f"{path}: programme verification mismatch "
            f"{parsed_programmes}/{expected_programmes}"
        )


def normalize(path: Path) -> tuple[int, int, int]:
    if not path.is_file():
        raise RuntimeError(f"missing XMLTV file: {path}")

    channel_ids: set[str] = set()
    programmes = 0
    rejected = 0

    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        tmp = Path(handle.name)
        handle.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        handle.write('<tv generator-info-name="Kaimandura/epg-de">\n')

        try:
            for _event, element in ET.iterparse(path, events=("end",)):
                tag = local_name(element.tag)
                if tag == "channel":
                    channel = clean_channel(element)
                    if channel is not None:
                        channel_id = channel.attrib["id"]
                        if channel_id not in channel_ids:
                            channel_ids.add(channel_id)
                            handle.write(f"  {xml(channel)}\n")
                    element.clear()
                elif tag == "programme":
                    programme = clean_programme(element)
                    if programme is None:
                        rejected += 1
                    else:
                        channel_id = programme.attrib["channel"]
                        if channel_id not in channel_ids:
                            raise RuntimeError(
                                f"{path}: programme references undeclared channel {channel_id}"
                            )
                        handle.write(f"  {xml(programme)}\n")
                        programmes += 1
                    element.clear()
            handle.write("</tv>\n")
        except Exception:
            tmp.unlink(missing_ok=True)
            raise

    if not channel_ids or not programmes:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(
            f"{path}: invalid normalized output channels={len(channel_ids)} "
            f"programmes={programmes}"
        )

    ET.parse(tmp)
    verify(tmp, channel_ids, programmes)
    tmp.replace(path)

    gzip_path = path.with_suffix(path.suffix + ".gz")
    with path.open("rb") as source, gzip_path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as target:
            shutil.copyfileobj(source, target)

    with gzip.open(gzip_path, "rb") as check:
        prefix = check.read(160)
        if b'<?xml version="1.0" encoding="UTF-8"?>' not in prefix or b"<tv " not in prefix:
            raise RuntimeError(f"{gzip_path}: gzip/XMLTV header verification failed")

    return len(channel_ids), programmes, rejected


def update_coverage(
    coverage: Path,
    stats: dict[str, tuple[int, int, int]],
) -> None:
    if not coverage.is_file():
        return

    with coverage.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        fields = reader.fieldnames or []

    required = {"category", "channel_count", "programme_count", "gzip_bytes"}
    if not rows or not required.issubset(fields):
        raise RuntimeError(f"{coverage}: unsupported coverage report")

    for row in rows:
        category = text(row.get("category"))
        if category not in stats:
            continue
        channels, programmes, gzip_bytes = stats[category]
        row["channel_count"] = str(channels)
        row["programme_count"] = str(programmes)
        row["gzip_bytes"] = str(gzip_bytes)

    with coverage.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Normalize generated USA XMLTV files to a conservative "
            "TiviMate-compatible XMLTV subset."
        )
    )
    parser.add_argument("xml", nargs="+", type=Path)
    parser.add_argument("--coverage", type=Path)
    args = parser.parse_args()

    coverage_stats: dict[str, tuple[int, int, int]] = {}
    for path in args.xml:
        channels, programmes, rejected = normalize(path)
        gzip_path = path.with_suffix(path.suffix + ".gz")
        category = CATEGORY_BY_STEM.get(path.stem)
        if category:
            coverage_stats[category] = (
                channels,
                programmes,
                gzip_path.stat().st_size,
            )
        print(
            f"TiviMate-safe {path}: channels={channels} programmes={programmes} "
            f"rejected={rejected} gzip={gzip_path.stat().st_size}"
        )

    if args.coverage:
        update_coverage(args.coverage, coverage_stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
