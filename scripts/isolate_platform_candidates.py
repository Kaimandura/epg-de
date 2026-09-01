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
        description="Isolate FAST platform candidates so platform schedules cannot overwrite linear channels."
    )
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

    isolated: dict[str, list[dict[str, Any]]] = {}
    moved = 0
    collisions: dict[str, tuple[str, str]] = {}

    for original_id in list(channels):
        candidates = channels.get(original_id, [])
        keep: list[dict[str, Any]] = []
        for candidate in candidates:
            matched_rule = next(
                (rule for rule in platform_rules if matches(candidate, rule)),
                None,
            )
            if matched_rule is None:
                keep.append(candidate)
                continue

            new_id = isolated_id(candidate, matched_rule)
            attrs = candidate.get("attrs", {})
            signature = (str(attrs.get("site", "")), str(attrs.get("site_id", "")))
            previous = collisions.get(new_id)
            if previous is not None and previous != signature:
                raise SystemExit(
                    f"Platform isolation ID collision for {new_id}: {previous} vs {signature}"
                )
            collisions[new_id] = signature

            moved_candidate = dict(candidate)
            moved_attrs = dict(attrs)
            moved_attrs["xmltv_id"] = new_id
            moved_candidate["attrs"] = moved_attrs
            moved_candidate["xmltv_id"] = new_id

            original_name = str(candidate.get("name", "") or original_id).strip()
            label = str(matched_rule.get("label", matched_rule.get("name", "Platform"))).strip()
            moved_candidate["name"] = f"{original_name} [{label}]"
            moved_candidate["display_aliases"] = [original_name]
            reasons = set(moved_candidate.get("reasons", []))
            reasons.add(f"platform_isolated={matched_rule.get('name', 'platform')}")
            moved_candidate["reasons"] = sorted(reasons)

            isolated.setdefault(new_id, []).append(moved_candidate)
            moved += 1

        if keep:
            channels[original_id] = keep
        else:
            channels.pop(original_id, None)

    for new_id, candidates in isolated.items():
        if new_id in channels:
            raise SystemExit(f"Platform isolation target already exists: {new_id}")
        channels[new_id] = candidates

    meta = payload.setdefault("meta", {})
    if isinstance(meta, dict):
        meta["platform_candidates_isolated"] = moved
        meta["selected_xmltv_ids"] = len(channels)

    payload["channels"] = channels
    args.candidates.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_channels_xml(args.channels, channels)

    print(f"Isolated {moved} FAST platform candidate(s) into {len(isolated)} XMLTV IDs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
