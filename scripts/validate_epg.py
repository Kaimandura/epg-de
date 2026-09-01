#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import math
import xml.etree.ElementTree as ET
from pathlib import Path


def load_xml(path: Path) -> ET.Element:
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError) as exc:
        raise SystemExit(f"Invalid XMLTV file {path}: {exc}") from exc
    if root.tag != "tv":
        raise SystemExit(f"Invalid XMLTV {path}: root element is not <tv>.")
    return root


def counts(root: ET.Element) -> tuple[set[str], list[ET.Element], set[str]]:
    channels = [node.attrib.get("id", "") for node in root.findall("channel")]
    channel_ids = {item for item in channels if item}
    programmes = root.findall("programme")
    active_channels = {
        programme.attrib.get("channel", "")
        for programme in programmes
        if programme.attrib.get("channel", "")
    }
    return channel_ids, programmes, active_channels


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate generated XMLTV output and Last-Known-Good regression gates."
    )
    parser.add_argument("xml", type=Path)
    parser.add_argument("--gzip", dest="gzip_path", type=Path)
    parser.add_argument("--min-channels", type=int, default=25)
    parser.add_argument("--min-programmes", type=int, default=100)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--min-baseline-programme-ratio", type=float, default=0.0)
    parser.add_argument("--min-baseline-active-channel-ratio", type=float, default=0.0)
    args = parser.parse_args()

    for name, value in (
        ("--min-baseline-programme-ratio", args.min_baseline_programme_ratio),
        ("--min-baseline-active-channel-ratio", args.min_baseline_active_channel_ratio),
    ):
        if not 0.0 <= value <= 1.0:
            parser.error(f"{name} must be between 0.0 and 1.0")

    root = load_xml(args.xml)
    channel_nodes = root.findall("channel")
    channel_ids, programmes, active_channels = counts(root)

    if len(channel_ids) < args.min_channels:
        raise SystemExit(f"Too few channels: {len(channel_ids)} < {args.min_channels}")
    if len(programmes) < args.min_programmes:
        raise SystemExit(f"Too few programmes: {len(programmes)} < {args.min_programmes}")
    if len(channel_nodes) != len(channel_ids):
        raise SystemExit("Duplicate or empty channel IDs found.")

    seen: set[tuple[str, str, str, str]] = set()
    for programme in programmes:
        channel_id = programme.attrib.get("channel", "")
        if channel_id not in channel_ids:
            raise SystemExit(f"Programme references unknown channel: {channel_id}")
        signature = (
            channel_id,
            programme.attrib.get("start", ""),
            programme.attrib.get("stop", ""),
            programme.findtext("title") or "",
        )
        if signature in seen:
            raise SystemExit(f"Duplicate programme found: {signature}")
        seen.add(signature)

    gzip_path = args.gzip_path or args.xml.with_suffix(args.xml.suffix + ".gz")
    if not gzip_path.exists():
        raise SystemExit(f"Missing gzip file: {gzip_path}")

    xml_bytes = args.xml.read_bytes()
    try:
        with gzip.open(gzip_path, "rb") as handle:
            gzip_bytes = handle.read()
        gzip_root = ET.fromstring(gzip_bytes)
    except (OSError, ET.ParseError) as exc:
        raise SystemExit(f"Invalid gzip XMLTV {gzip_path}: {exc}") from exc

    if gzip_root.tag != "tv":
        raise SystemExit(f"Invalid gzip XMLTV {gzip_path}: root element is not <tv>.")
    if gzip_bytes != xml_bytes:
        raise SystemExit("Gzip payload does not exactly match the staged XML file.")

    if args.baseline and args.baseline.exists():
        baseline_root = load_xml(args.baseline)
        _, baseline_programmes, baseline_active_channels = counts(baseline_root)

        if baseline_programmes and args.min_baseline_programme_ratio > 0:
            required_programmes = math.ceil(
                len(baseline_programmes) * args.min_baseline_programme_ratio
            )
            if len(programmes) < required_programmes:
                raise SystemExit(
                    "Last-Known-Good gate failed: "
                    f"{len(programmes)} programmes < {required_programmes} required "
                    f"({args.min_baseline_programme_ratio:.0%} of baseline "
                    f"{len(baseline_programmes)})."
                )

        if baseline_active_channels and args.min_baseline_active_channel_ratio > 0:
            required_active = math.ceil(
                len(baseline_active_channels) * args.min_baseline_active_channel_ratio
            )
            if len(active_channels) < required_active:
                raise SystemExit(
                    "Last-Known-Good gate failed: "
                    f"{len(active_channels)} active channels < {required_active} required "
                    f"({args.min_baseline_active_channel_ratio:.0%} of baseline "
                    f"{len(baseline_active_channels)})."
                )

        print(
            "Baseline gate OK: "
            f"{len(active_channels)} active channels / {len(programmes)} programmes "
            f"vs {len(baseline_active_channels)} / {len(baseline_programmes)}."
        )

    print(
        f"Validation OK: {len(channel_ids)} channels, "
        f"{len(active_channels)} active channels, {len(programmes)} programmes."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
