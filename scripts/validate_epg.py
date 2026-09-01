#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import xml.etree.ElementTree as ET
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate generated XMLTV output.")
    parser.add_argument("xml", type=Path)
    parser.add_argument("--min-channels", type=int, default=25)
    parser.add_argument("--min-programmes", type=int, default=100)
    args = parser.parse_args()
    root = ET.parse(args.xml).getroot()
    if root.tag != "tv":
        raise SystemExit("Invalid XMLTV: root element is not <tv>.")
    channels = [node.attrib.get("id", "") for node in root.findall("channel")]
    channel_ids = {item for item in channels if item}
    programmes = root.findall("programme")
    if len(channel_ids) < args.min_channels:
        raise SystemExit(f"Too few channels: {len(channel_ids)} < {args.min_channels}")
    if len(programmes) < args.min_programmes:
        raise SystemExit(f"Too few programmes: {len(programmes)} < {args.min_programmes}")
    if len(channels) != len(channel_ids):
        raise SystemExit("Duplicate or empty channel IDs found.")
    seen = set()
    for programme in programmes:
        channel_id = programme.attrib.get("channel", "")
        if channel_id not in channel_ids:
            raise SystemExit(f"Programme references unknown channel: {channel_id}")
        signature = (channel_id, programme.attrib.get("start", ""), programme.attrib.get("stop", ""), programme.findtext("title") or "")
        if signature in seen:
            raise SystemExit(f"Duplicate programme found: {signature}")
        seen.add(signature)
    gzip_path = args.xml.with_suffix(args.xml.suffix + ".gz")
    if not gzip_path.exists():
        raise SystemExit(f"Missing gzip file: {gzip_path}")
    with gzip.open(gzip_path, "rb") as handle:
        ET.fromstring(handle.read())
    print(f"Validation OK: {len(channel_ids)} channels, {len(programmes)} programmes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
