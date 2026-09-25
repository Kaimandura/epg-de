#!/usr/bin/env python3
import argparse
import gzip
from pathlib import Path
import xml.etree.ElementTree as ET

MAGENTA_REQUIRED = [
    "MagentaSport.de@SD",
    "MagentaTV.de@MSSport",
    *[f"MagentaTV.de@MyTeamTVSport{i:02d}" for i in range(1, 19)],
    "MagentaTV.de@SkySportKompakt1",
]

parser = argparse.ArgumentParser()
parser.add_argument("files", nargs="+")
parser.add_argument("--require-magenta-sports", action="store_true")
parser.add_argument("--require-all-channels-active", action="store_true")
args = parser.parse_args()

failed = False
for path_arg in args.files:
    path = Path(path_arg)
    try:
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError("missing or empty file")
        with gzip.open(path, "rb") as fh:
            root = ET.parse(fh).getroot()
        if root.tag != "tv":
            raise ValueError(f"root element is {root.tag!r}, expected 'tv'")
        channels = root.findall("channel")
        programmes = root.findall("programme")
        if not channels:
            raise ValueError("no channels")
        if not programmes:
            raise ValueError("no programmes")
        channel_ids = [c.get("id") for c in channels]
        if any(not x for x in channel_ids):
            raise ValueError("channel without id")
        if len(channel_ids) != len(set(channel_ids)):
            raise ValueError("duplicate channel ids")
        known = set(channel_ids)
        programme_counts = {}
        bad_refs = 0
        missing_titles = 0
        for p in programmes:
            channel_id = p.get("channel")
            if not channel_id or channel_id not in known:
                bad_refs += 1
            else:
                programme_counts[channel_id] = programme_counts.get(channel_id, 0) + 1
            title = p.find("title")
            if title is None or not (title.text or "").strip():
                missing_titles += 1
        if bad_refs:
            raise ValueError(f"{bad_refs} programmes reference unknown/missing channels")
        if missing_titles:
            raise ValueError(f"{missing_titles} programmes have no title")

        if args.require_magenta_sports and path.name == "DE-MAGENTA.xml.gz":
            missing = [x for x in MAGENTA_REQUIRED if x not in known]
            inactive = [x for x in MAGENTA_REQUIRED if programme_counts.get(x, 0) == 0]
            if missing:
                raise ValueError("missing required Magenta sport channels: " + ", ".join(missing))
            if inactive:
                raise ValueError("required Magenta sport channels without programmes: " + ", ".join(inactive))
            print("OK Magenta sports: " + ", ".join(
                f"{x}={programme_counts[x]}" for x in MAGENTA_REQUIRED
            ))

        print(f"OK {path.name}: channels={len(channels)} programmes={len(programmes)}")
    except Exception as exc:
        failed = True
        print(f"FAIL {path}: {exc}", file=__import__("sys").stderr)

if failed:
    raise SystemExit(1)
