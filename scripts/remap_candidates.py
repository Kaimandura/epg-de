#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


def load_remaps(path: Path) -> list[dict[str, str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("remaps", [])
    if not isinstance(rows, list):
        raise ValueError("remap file: 'remaps' must be a list")

    result: list[dict[str, str]] = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"remap #{index} must be an object")
        normalized = {
            str(key): str(value).strip()
            for key, value in row.items()
            if value is not None
        }
        missing = [
            key
            for key in ("site", "site_id", "from_xmltv_id", "to_xmltv_id")
            if not normalized.get(key)
        ]
        if missing:
            raise ValueError(
                f"remap #{index} missing required fields: {', '.join(missing)}"
            )
        result.append(normalized)
    return result


def candidate_signature(candidate: dict[str, Any]) -> tuple[str, str, str]:
    attrs = candidate.get("attrs", {})
    return (
        str(attrs.get("site", "")),
        str(attrs.get("site_id", "")),
        str(attrs.get("provider", "")),
    )


def write_channels_xml(
    path: Path,
    channels: dict[str, list[dict[str, Any]]],
) -> None:
    root = ET.Element("channels")
    for xmltv_id in sorted(channels):
        candidates = channels[xmltv_id]
        if not candidates:
            continue
        preferred = candidates[0]
        attrs = dict(preferred.get("attrs", {}))
        attrs["xmltv_id"] = xmltv_id
        node = ET.SubElement(root, "channel", attrs)
        node.text = str(preferred.get("name", "") or xmltv_id)

    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    path.parent.mkdir(parents=True, exist_ok=True)
    tree.write(path, encoding="utf-8", xml_declaration=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Apply authoritative source-specific XMLTV ID remaps to collected candidates."
    )
    parser.add_argument("--candidates", required=True, type=Path)
    parser.add_argument("--remap-file", required=True, type=Path)
    parser.add_argument("--channels", required=True, type=Path)
    args = parser.parse_args()

    payload = json.loads(args.candidates.read_text(encoding="utf-8"))
    channels: dict[str, list[dict[str, Any]]] = payload.get("channels", {})
    if not isinstance(channels, dict):
        raise ValueError("candidates file: 'channels' must be an object")

    remaps = load_remaps(args.remap_file)
    applied = 0

    for remap in remaps:
        source_id = remap["from_xmltv_id"]
        target_id = remap["to_xmltv_id"]
        source_candidates = channels.get(source_id, [])
        if not isinstance(source_candidates, list):
            raise ValueError(f"candidate group {source_id!r} must be a list")

        matched: list[dict[str, Any]] = []
        remaining: list[dict[str, Any]] = []
        for candidate in source_candidates:
            attrs = candidate.get("attrs", {})
            if (
                str(attrs.get("site", "")) == remap["site"]
                and str(attrs.get("site_id", "")) == remap["site_id"]
            ):
                moved = dict(candidate)
                moved_attrs = dict(attrs)
                moved_attrs["xmltv_id"] = target_id
                if remap.get("lang"):
                    moved_attrs["lang"] = remap["lang"]
                moved["attrs"] = moved_attrs
                moved["xmltv_id"] = target_id
                if remap.get("name"):
                    moved["name"] = remap["name"]
                reasons = set(moved.get("reasons", []))
                reasons.add("manual_id_remap")
                moved["reasons"] = sorted(reasons)
                matched.append(moved)
            else:
                remaining.append(candidate)

        if not matched:
            raise SystemExit(
                "Required remap source candidate not found: "
                f"{source_id} <- {remap['site']}:{remap['site_id']}"
            )

        if remaining:
            channels[source_id] = remaining
        else:
            channels.pop(source_id, None)

        target_candidates = channels.setdefault(target_id, [])
        existing = {candidate_signature(candidate) for candidate in target_candidates}
        for candidate in matched:
            signature = candidate_signature(candidate)
            if signature not in existing:
                target_candidates.append(candidate)
                existing.add(signature)
                applied += 1

    meta = payload.setdefault("meta", {})
    if isinstance(meta, dict):
        meta["id_remaps_applied"] = applied
        meta["selected_xmltv_ids"] = len(channels)

    payload["channels"] = channels
    args.candidates.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_channels_xml(args.channels, channels)

    print(f"Applied {applied} authoritative XMLTV ID remap(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
