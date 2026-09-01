#!/usr/bin/env python3
from __future__ import annotations

import argparse
import xml.etree.ElementTree as ET
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate XMLTV channel ID namespace constraints."
    )
    parser.add_argument("xml", type=Path)
    parser.add_argument("--require-prefix")
    parser.add_argument("--forbid-id", action="append", default=[])
    args = parser.parse_args()

    try:
        root = ET.parse(args.xml).getroot()
    except (ET.ParseError, OSError) as exc:
        raise SystemExit(f"Invalid XMLTV file {args.xml}: {exc}") from exc
    if root.tag != "tv":
        raise SystemExit(f"Invalid XMLTV {args.xml}: root element is not <tv>.")

    channel_ids = [
        node.attrib.get("id", "").strip()
        for node in root.findall("channel")
        if node.attrib.get("id", "").strip()
    ]
    channel_set = set(channel_ids)

    forbidden = sorted(set(args.forbid_id) & channel_set)
    if forbidden:
        raise SystemExit(f"Forbidden channel IDs found: {forbidden}")

    if args.require_prefix:
        invalid = sorted(
            channel_id
            for channel_id in channel_ids
            if not channel_id.startswith(args.require_prefix)
        )
        if invalid:
            raise SystemExit(
                f"Channel IDs outside required prefix {args.require_prefix!r}: {invalid[:20]}"
            )

    print(
        f"Channel namespace OK: {len(channel_ids)} channels"
        + (f", prefix={args.require_prefix}" if args.require_prefix else "")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
