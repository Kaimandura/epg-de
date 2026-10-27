"""Read-only inventory of prohibited pseudo channel identities in committed XMLTV.

This inventory is evidence, not a sanitizer. A nonzero exit blocks release.
"""
from __future__ import annotations

import argparse
import gzip
import json
import xml.etree.ElementTree as ET
from pathlib import Path

from build_us_epgs import is_pseudo_channel_id


def inventory(path: Path) -> dict:
    channels: set[str] = set()
    programme_counts: dict[str, int] = {}
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rb") as stream:
        for _event, node in ET.iterparse(stream, events=("end",)):
            if node.tag == "channel":
                channel_id = (node.get("id") or "").strip()
                if is_pseudo_channel_id(channel_id):
                    channels.add(channel_id)
            elif node.tag == "programme":
                channel_id = (node.get("channel") or "").strip()
                if is_pseudo_channel_id(channel_id):
                    programme_counts[channel_id] = programme_counts.get(channel_id, 0) + 1
            node.clear()
    identities = sorted(channels | programme_counts.keys())
    return {
        "file": str(path),
        "pseudo_ids": identities,
        "pseudo_channel_count": len(channels),
        "pseudo_programme_count": sum(programme_counts.values()),
        "programme_counts_by_id": dict(sorted(programme_counts.items())),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--epg-dir", type=Path, default=Path("epg"))
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    files = sorted(args.epg_dir.glob("*.xml.gz"))
    if not files:
        raise SystemExit("No committed EPG files found; cannot validate")
    rows = [inventory(path) for path in files]
    report = {
        "status": "failed" if any(row["pseudo_ids"] for row in rows) else "passed",
        "files_checked": len(rows),
        "files": rows,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Pseudo identity inventory: {report['status']} ({len(files)} files)")
    for row in rows:
        if row["pseudo_ids"]:
            print(f"{row['file']}: {len(row['pseudo_ids'])} pseudo IDs, "
                  f"{row['pseudo_programme_count']} programmes: {', '.join(row['pseudo_ids'])}")
    return 1 if report["status"] != "passed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
