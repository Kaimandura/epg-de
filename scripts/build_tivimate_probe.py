#!/usr/bin/env python3
from __future__ import annotations

import argparse
import xml.etree.ElementTree as ET
from copy import deepcopy
from pathlib import Path

TARGETS = {
    "revry": ("Revry", ("Revry.us",)),
    "revry-news": ("Revry News", ("RevryNews.us",)),
    "revolt": ("Revolt", ("Revolt.us",)),
    "rfd-tv": ("RFD-TV", ("RFDTV.us",)),
}


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def text(value: str | None) -> str:
    return (value or "").strip()


def matches(channel_id: str, prefixes: tuple[str, ...]) -> bool:
    return any(channel_id == prefix or channel_id.startswith(prefix + "@") for prefix in prefixes)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a tiny real-data XMLTV file for diagnosing TiviMate import issues."
    )
    parser.add_argument("--input", action="append", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    selected_source_ids: dict[str, str] = {}
    programmes: list[ET.Element] = []

    for path in args.input:
        if not path.is_file():
            raise RuntimeError(f"Missing input: {path}")

        for _event, element in ET.iterparse(path, events=("end",)):
            tag = local_name(element.tag)
            if tag == "channel":
                channel_id = text(element.attrib.get("id"))
                for safe_id, (_display_name, prefixes) in TARGETS.items():
                    if safe_id not in selected_source_ids and matches(channel_id, prefixes):
                        selected_source_ids[safe_id] = channel_id
                element.clear()
                continue

            if tag != "programme":
                element.clear()
                continue

            source_channel = text(element.attrib.get("channel"))
            safe_id = next(
                (key for key, value in selected_source_ids.items() if value == source_channel),
                None,
            )
            if safe_id is None:
                element.clear()
                continue

            start = text(element.attrib.get("start"))
            stop = text(element.attrib.get("stop"))
            title = next(
                (
                    text(child.text)
                    for child in element
                    if local_name(child.tag) == "title" and text(child.text)
                ),
                "",
            )
            if not start or not title:
                element.clear()
                continue

            attrs = {"start": start, "channel": safe_id}
            if stop:
                attrs["stop"] = stop
            programme = ET.Element("programme", attrs)
            ET.SubElement(programme, "title").text = title
            programmes.append(programme)
            element.clear()

    missing = [safe_id for safe_id in TARGETS if safe_id not in selected_source_ids]
    if missing:
        raise RuntimeError(f"Probe channels not found: {', '.join(missing)}")

    counts = {safe_id: 0 for safe_id in TARGETS}
    for programme in programmes:
        counts[programme.attrib["channel"]] += 1
    empty = [safe_id for safe_id, count in counts.items() if count == 0]
    if empty:
        raise RuntimeError(f"Probe channels without programmes: {', '.join(empty)}")

    root = ET.Element("tv")
    for safe_id, (display_name, _prefixes) in TARGETS.items():
        channel = ET.SubElement(root, "channel", {"id": safe_id})
        ET.SubElement(channel, "display-name").text = display_name
    for programme in programmes:
        root.append(deepcopy(programme))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(args.output, encoding="UTF-8", xml_declaration=True, short_empty_elements=True)

    ET.parse(args.output)
    print(
        "TiviMate probe built: "
        + ", ".join(f"{safe_id}={counts[safe_id]}" for safe_id in TARGETS)
        + f" output={args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
