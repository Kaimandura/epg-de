#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


def as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def matches(candidate: dict[str, Any], rule: dict[str, Any]) -> bool:
    attrs = candidate.get("attrs", {})
    site = str(attrs.get("site", ""))
    site_id = str(attrs.get("site_id", ""))
    source_file = str(candidate.get("source_file", ""))

    if site != str(rule.get("site", "")):
        return False

    prefixes = as_list(rule.get("site_id_prefix"))
    if prefixes and not any(site_id.startswith(prefix) for prefix in prefixes):
        return False

    contains = str(rule.get("source_file_contains", "")).strip()
    if contains and contains not in source_file:
        return False

    return True


def safe_token(value: str) -> str:
    token = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip("-.")
    return token or "unknown"


def isolated_id(candidate: dict[str, Any], rule: dict[str, Any]) -> str:
    attrs = candidate.get("attrs", {})
    site_id = str(attrs.get("site_id", ""))
    prefix = safe_token(str(rule.get("id_prefix", rule.get("name", "platform"))))

    region = str(rule.get("region", "")).strip()
    payload = site_id
    if "#" in site_id:
        path, payload = site_id.split("#", 1)
        if not region and "/" in path:
            region = path.rsplit("/", 1)[-1]

    if not region:
        region = "de"

    return f"{prefix}.{safe_token(region)}.{safe_token(payload)}"


def candidate_signature(candidate: dict[str, Any]) -> tuple[str, str, str]:
    attrs = candidate.get("attrs", {})
    return (
        str(attrs.get("site", "")),
        str(attrs.get("site_id", "")),
        str(attrs.get("provider", "")),
    )


def make_isolated_candidate(
    candidate: dict[str, Any],
    rule: dict[str, Any],
    original_name: str,
) -> tuple[str, dict[str, Any]]:
    new_id = isolated_id(candidate, rule)
    moved = dict(candidate)
    moved_attrs = dict(candidate.get("attrs", {}))
    moved_attrs["xmltv_id"] = new_id
    moved["attrs"] = moved_attrs
    moved["xmltv_id"] = new_id

    label = str(rule.get("label", rule.get("name", "Platform"))).strip()
    moved["name"] = f"{original_name} [{label}]"
    moved["display_aliases"] = [original_name]
    reasons = set(moved.get("reasons", []))
    reasons.add(f"platform_isolated={rule.get('name', 'platform')}")
    moved["reasons"] = sorted(reasons)
    return new_id, moved


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
        description="Inject and isolate FAST platform channels so their schedules cannot overwrite linear channels."
    )
    parser.add_argument("--epg-root", type=Path, default=Path("upstream/epg"))
    parser.add_argument("--candidates", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--channels", required=True, type=Path)
    args = parser.parse_args()

    payload = json.loads(args.candidates.read_text(encoding="utf-8"))
    channels: dict[str, list[dict[str, Any]]] = payload.get("channels", {})
    if not isinstance(channels, dict):
        raise ValueError("candidates file: 'channels' must be an object")

    config = json.loads(args.config.read_text(encoding="utf-8"))
    platform_rules = config.get("platforms", [])
    if not isinstance(platform_rules, list) or not platform_rules:
        raise ValueError("platform isolation config must contain a non-empty 'platforms' list")

    removed = 0
    for original_id in list(channels):
        candidates = channels.get(original_id, [])
        keep: list[dict[str, Any]] = []
        for candidate in candidates:
            if any(matches(candidate, rule) for rule in platform_rules):
                removed += 1
            else:
                keep.append(candidate)
        if keep:
            channels[original_id] = keep
        else:
            channels.pop(original_id, None)

    isolated: dict[str, list[dict[str, Any]]] = {}
    collisions: dict[str, tuple[str, str]] = {}
    scanned_entries = matched_entries = 0

    for path in sorted(args.epg_root.glob("sites/**/*.channels.xml")):
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError:
            continue
        source_file = str(path.relative_to(args.epg_root))

        for element in root.findall("channel"):
            scanned_entries += 1
            attrs = dict(element.attrib)
            original_name = (element.text or "").strip()
            if not original_name:
                continue
            candidate = {
                "xmltv_id": str(attrs.get("xmltv_id", "")),
                "name": original_name,
                "attrs": attrs,
                "source_file": source_file,
                "reasons": [],
            }

            matched_rule = next(
                (rule for rule in platform_rules if matches(candidate, rule)),
                None,
            )
            if matched_rule is None:
                continue

            matched_entries += 1
            new_id, moved = make_isolated_candidate(candidate, matched_rule, original_name)
            signature = (
                str(moved["attrs"].get("site", "")),
                str(moved["attrs"].get("site_id", "")),
            )
            previous = collisions.get(new_id)
            if previous is not None and previous != signature:
                raise SystemExit(
                    f"Platform isolation ID collision for {new_id}: {previous} vs {signature}"
                )
            collisions[new_id] = signature

            bucket = isolated.setdefault(new_id, [])
            existing = {candidate_signature(item) for item in bucket}
            if candidate_signature(moved) not in existing:
                bucket.append(moved)

    if matched_entries == 0:
        raise SystemExit("Platform isolation matched no upstream channels.")

    for new_id, candidates in isolated.items():
        if new_id in channels:
            raise SystemExit(f"Platform isolation target already exists: {new_id}")
        channels[new_id] = candidates

    meta = payload.setdefault("meta", {})
    if isinstance(meta, dict):
        meta["platform_candidates_removed_from_shared_ids"] = removed
        meta["platform_entries_scanned"] = scanned_entries
        meta["platform_entries_matched"] = matched_entries
        meta["platform_xmltv_ids_injected"] = len(isolated)
        meta["selected_xmltv_ids"] = len(channels)

    payload["channels"] = channels
    args.candidates.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_channels_xml(args.channels, channels)

    print(
        f"FAST isolation: removed={removed} matched_upstream={matched_entries} "
        f"injected_ids={len(isolated)} total_ids={len(channels)}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
