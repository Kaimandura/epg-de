#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import gzip
import json
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from collections import defaultdict
from copy import deepcopy
from pathlib import Path
from typing import Any


def write_channels_file(path: Path, selections: dict[str, dict[str, Any]]) -> None:
    root = ET.Element("channels")
    for xmltv_id in sorted(selections):
        candidate = selections[xmltv_id]
        node = ET.SubElement(root, "channel", candidate["attrs"])
        node.text = candidate["name"]
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(path, encoding="utf-8", xml_declaration=True)


def run_grab(epg_root: Path, channels: Path, output: Path, days: int) -> int:
    cmd = ["npm", "run", "grab", "---", f"--channels={channels.resolve()}", f"--output={output.resolve()}", f"--days={days}", "--timeout=30000"]
    return subprocess.run(cmd, cwd=epg_root, check=False).returncode


def parse_guide(path: Path) -> tuple[dict[str, ET.Element], dict[str, list[ET.Element]]]:
    channels: dict[str, ET.Element] = {}
    programmes: dict[str, list[ET.Element]] = defaultdict(list)
    if not path.exists() or path.stat().st_size == 0:
        return channels, programmes
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return channels, programmes
    for channel in root.findall("channel"):
        channel_id = channel.attrib.get("id", "")
        if channel_id:
            channels[channel_id] = deepcopy(channel)
    for programme in root.findall("programme"):
        channel_id = programme.attrib.get("channel", "")
        if channel_id:
            programmes[channel_id].append(deepcopy(programme))
    return channels, programmes


def programme_signature(programme: ET.Element) -> tuple[str, str, str, str]:
    return (programme.attrib.get("channel", ""), programme.attrib.get("start", ""), programme.attrib.get("stop", ""), programme.findtext("title") or "")


def write_output(output: Path, candidate_data: dict[str, list[dict[str, Any]]], best_channels: dict[str, ET.Element], best_programmes: dict[str, list[ET.Element]]) -> None:
    root = ET.Element("tv", {"generator-info-name": "Kaimandura/epg-de"})
    for xmltv_id in sorted(candidate_data):
        channel = best_channels.get(xmltv_id)
        if channel is None:
            candidate = candidate_data[xmltv_id][0]
            channel = ET.Element("channel", {"id": xmltv_id})
            display = ET.SubElement(channel, "display-name")
            display.text = candidate["name"]
        root.append(deepcopy(channel))
    seen: set[tuple[str, str, str, str]] = set()
    all_programmes: list[ET.Element] = []
    for xmltv_id in sorted(best_programmes):
        for programme in best_programmes[xmltv_id]:
            signature = programme_signature(programme)
            if signature in seen:
                continue
            seen.add(signature)
            all_programmes.append(deepcopy(programme))
    all_programmes.sort(key=lambda item: (item.attrib.get("channel", ""), item.attrib.get("start", ""), item.attrib.get("stop", "")))
    for programme in all_programmes:
        root.append(programme)
    output.parent.mkdir(parents=True, exist_ok=True)
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(output, encoding="utf-8", xml_declaration=True)
    gzip_path = output.with_suffix(output.suffix + ".gz")
    with output.open("rb") as source, gzip_path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as target:
            shutil.copyfileobj(source, target)


def write_coverage(path: Path, candidate_data: dict[str, list[dict[str, Any]]], best_programmes: dict[str, list[ET.Element]], best_source: dict[str, dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["xmltv_id", "name", "status", "programme_count", "selected_site", "candidate_count", "reasons"])
        for xmltv_id in sorted(candidate_data):
            candidates = candidate_data[xmltv_id]
            programmes = best_programmes.get(xmltv_id, [])
            selected = best_source.get(xmltv_id)
            reasons = sorted({reason for item in candidates for reason in item.get("reasons", [])})
            writer.writerow([xmltv_id, candidates[0]["name"], "OK" if programmes else "NO_EPG", len(programmes), selected["attrs"].get("site", "") if selected else "", len(candidates), ";".join(reasons)])


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a deduplicated Germany XMLTV guide with source fallback.")
    parser.add_argument("--epg-root", required=True, type=Path)
    parser.add_argument("--candidates", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--coverage", required=True, type=Path)
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--rounds", type=int, default=4)
    parser.add_argument("--min-programmes", type=int, default=4)
    args = parser.parse_args()
    payload = json.loads(args.candidates.read_text(encoding="utf-8"))
    candidate_data: dict[str, list[dict[str, Any]]] = payload["channels"]
    best_channels: dict[str, ET.Element] = {}
    best_programmes: dict[str, list[ET.Element]] = {}
    best_source: dict[str, dict[str, Any]] = {}
    next_index = {xmltv_id: 0 for xmltv_id in candidate_data}
    pending = set(candidate_data)

    with tempfile.TemporaryDirectory(prefix="epg-de-") as tmp:
        tmp_path = Path(tmp)
        for round_number in range(args.rounds):
            selections: dict[str, dict[str, Any]] = {}
            for xmltv_id in sorted(pending):
                index = next_index[xmltv_id]
                candidates = candidate_data[xmltv_id]
                if index < len(candidates):
                    selections[xmltv_id] = candidates[index]
            if not selections:
                break
            channels_path = tmp_path / f"round-{round_number + 1}.channels.xml"
            guide_path = tmp_path / f"round-{round_number + 1}.guide.xml"
            write_channels_file(channels_path, selections)
            code = run_grab(args.epg_root, channels_path, guide_path, args.days)
            if code != 0:
                print(f"Warning: grab round {round_number + 1} exited with code {code}")
            round_channels, round_programmes = parse_guide(guide_path)
            resolved_this_round: set[str] = set()
            for xmltv_id, candidate in selections.items():
                programmes = round_programmes.get(xmltv_id, [])
                previous = best_programmes.get(xmltv_id, [])
                if len(programmes) > len(previous):
                    best_programmes[xmltv_id] = programmes
                    if xmltv_id in round_channels:
                        best_channels[xmltv_id] = round_channels[xmltv_id]
                    best_source[xmltv_id] = candidate
                if len(best_programmes.get(xmltv_id, [])) >= args.min_programmes:
                    resolved_this_round.add(xmltv_id)
                else:
                    next_index[xmltv_id] += 1
                    if next_index[xmltv_id] >= len(candidate_data[xmltv_id]):
                        resolved_this_round.add(xmltv_id)
            pending -= resolved_this_round
            print(f"Round {round_number + 1}: attempted {len(selections)}, resolved {len(resolved_this_round)}, pending {len(pending)}")

    write_output(args.output, candidate_data, best_channels, best_programmes)
    write_coverage(args.coverage, candidate_data, best_programmes, best_source)
    with_programmes = sum(1 for key in candidate_data if best_programmes.get(key))
    total_programmes = sum(len(items) for items in best_programmes.values())
    print(f"Built {args.output}: {with_programmes}/{len(candidate_data)} channels with EPG, {total_programmes} programmes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
