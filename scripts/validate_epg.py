#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import math
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

from cleanup_published_epgs import clean_file


MASTER_PRESERVE_IDS = {
    "Silverline.de",
    "SerienPlus.de@HD",
    "SerienPlus.de@SD",
    "OneTerra.de@SD",
    "CrimeTime.de@SD",
    "DFBTV.de@HD",
}


def cleanup_before_validation(path: Path) -> None:
    if not path.exists():
        return

    preserve = MASTER_PRESERVE_IDS if path.name == "de.xml" else set()
    rows, channels, active, programmes = clean_file(
        label=f"validate:{path.stem}",
        path=path,
        preserve=preserve,
        min_overlap=0.92,
        min_jaccard=0.80,
        min_shared=8,
    )
    print(
        f"Pre-validation cleanup: {path} channels={channels} active={active} "
        f"programmes={programmes} removed={len(rows)}"
    )


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


def channel_display_names(node: ET.Element) -> list[str]:
    return [
        (display.text or "").strip()
        for display in node.findall("display-name")
        if (display.text or "").strip()
    ]


def parse_required_display_name(spec: str) -> tuple[str, str]:
    xmltv_id, separator, expected_name = spec.partition("=")
    xmltv_id = xmltv_id.strip()
    expected_name = expected_name.strip()
    if not separator or not xmltv_id or not expected_name:
        raise SystemExit(
            "Invalid --required-display-name value. Expected XMLTV_ID=DISPLAY_NAME, "
            f"got: {spec!r}"
        )
    return xmltv_id, expected_name


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
    parser.add_argument(
        "--require-display-names",
        action="store_true",
        help="Require every channel to have at least one non-empty display-name.",
    )
    parser.add_argument(
        "--require-all-channels-active",
        action="store_true",
        help="Reject any channel that has no programme entries.",
    )
    parser.add_argument(
        "--required-active-channel",
        action="append",
        default=[],
        metavar="XMLTV_ID",
        help="Require this XMLTV channel ID to exist and have at least one programme.",
    )
    parser.add_argument(
        "--required-display-name",
        action="append",
        default=[],
        metavar="XMLTV_ID=DISPLAY_NAME",
        help="Require an exact case-insensitive display-name for the given XMLTV ID.",
    )
    args = parser.parse_args()

    for name, value in (
        ("--min-baseline-programme-ratio", args.min_baseline_programme_ratio),
        ("--min-baseline-active-channel-ratio", args.min_baseline_active_channel_ratio),
    ):
        if not 0.0 <= value <= 1.0:
            parser.error(f"{name} must be between 0.0 and 1.0")

    cleanup_before_validation(args.xml)
    root = load_xml(args.xml)
    channel_nodes = root.findall("channel")
    channel_ids, programmes, active_channels = counts(root)
    channel_by_id = {
        node.attrib.get("id", ""): node
        for node in channel_nodes
        if node.attrib.get("id", "")
    }

    if len(channel_ids) < args.min_channels:
        raise SystemExit(f"Too few channels: {len(channel_ids)} < {args.min_channels}")
    if len(programmes) < args.min_programmes:
        raise SystemExit(f"Too few programmes: {len(programmes)} < {args.min_programmes}")
    if len(channel_nodes) != len(channel_ids):
        raise SystemExit("Duplicate or empty channel IDs found.")

    inactive_channels = sorted(channel_ids - active_channels)
    if inactive_channels:
        preview = ", ".join(inactive_channels[:20])
        raise SystemExit(
            "Channels without programmes found after cleanup: "
            f"{preview}" + (" ..." if len(inactive_channels) > 20 else "")
        )

    if args.require_display_names:
        missing_display_names = [
            channel_id
            for channel_id, node in sorted(channel_by_id.items())
            if not channel_display_names(node)
        ]
        if missing_display_names:
            preview = ", ".join(missing_display_names[:20])
            raise SystemExit(
                "Channels without display-name found: "
                f"{preview}" + (" ..." if len(missing_display_names) > 20 else "")
            )

    seen: set[tuple[str, str, str, str]] = set()
    programme_counts: Counter[str] = Counter()
    for programme in programmes:
        channel_id = programme.attrib.get("channel", "")
        if channel_id not in channel_ids:
            raise SystemExit(f"Programme references unknown channel: {channel_id}")
        programme_counts[channel_id] += 1
        signature = (
            channel_id,
            programme.attrib.get("start", ""),
            programme.attrib.get("stop", ""),
            programme.findtext("title") or "",
        )
        if signature in seen:
            raise SystemExit(f"Duplicate programme found: {signature}")
        seen.add(signature)

    if args.xml.name == "de.xml" and "publish" in args.xml.parts:
        for required_id in sorted(MASTER_PRESERVE_IDS):
            if required_id not in channel_by_id:
                raise SystemExit(f"Required master channel missing: {required_id}")
            count = programme_counts.get(required_id, 0)
            if count < 1:
                raise SystemExit(f"Required master channel has no programmes: {required_id}")
            if not channel_display_names(channel_by_id[required_id]):
                raise SystemExit(f"Required master channel has no display-name: {required_id}")
            print(f"Required master channel OK: {required_id} ({count} programmes)")

    for required_id in args.required_active_channel:
        required_id = required_id.strip()
        if not required_id:
            continue
        if required_id not in channel_by_id:
            raise SystemExit(f"Required channel missing: {required_id}")
        count = programme_counts.get(required_id, 0)
        if count < 1:
            raise SystemExit(f"Required channel has no programmes: {required_id}")
        if not channel_display_names(channel_by_id[required_id]):
            raise SystemExit(f"Required channel has no display-name: {required_id}")
        print(f"Required channel OK: {required_id} ({count} programmes)")

    for spec in args.required_display_name:
        required_id, expected_name = parse_required_display_name(spec)
        node = channel_by_id.get(required_id)
        if node is None:
            raise SystemExit(f"Required display-name channel missing: {required_id}")
        names = channel_display_names(node)
        if expected_name.casefold() not in {name.casefold() for name in names}:
            raise SystemExit(
                f"Required display-name missing for {required_id}: {expected_name!r}; "
                f"available={names!r}"
            )
        print(f"Required display-name OK: {required_id} -> {expected_name}")

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
        cleanup_before_validation(args.baseline)
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
