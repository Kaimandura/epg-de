#!/usr/bin/env python3
import gzip, sys
from pathlib import Path
import xml.etree.ElementTree as ET

files = [Path(x) for x in sys.argv[1:]]
if not files:
    raise SystemExit("no EPG files supplied")

failed = False
for path in files:
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
        bad_refs = sum(1 for p in programmes if not p.get("channel") or p.get("channel") not in known)
        missing_titles = sum(1 for p in programmes if p.find("title") is None or not (p.find("title").text or "").strip())
        if bad_refs:
            raise ValueError(f"{bad_refs} programmes reference unknown/missing channels")
        if missing_titles:
            raise ValueError(f"{missing_titles} programmes have no title")
        print(f"OK {path.name}: channels={len(channels)} programmes={len(programmes)}")
    except Exception as exc:
        failed = True
        print(f"FAIL {path}: {exc}", file=sys.stderr)

if failed:
    raise SystemExit(1)
