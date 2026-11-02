#!/usr/bin/env python3
"""Build an isolated logo-remediation candidate from pinned XMLTV snapshots.

Never changes epg/ or programme data. A failed invariant prevents the candidate
from being used by release tests.
"""
import argparse
import gzip
import hashlib
import json
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

from audit_all_epg_products import PRODUCTS, OPTIONAL
from enrich_epg_logos import enrich_file, load_channel_name_index, load_logos


def programme_digest(path):
    digest = hashlib.sha256()
    count = 0
    with (gzip.open(path, "rb") if path.suffix == ".gz" else path.open("rb")) as stream:
        for _, element in ET.iterparse(stream, events=("end",)):
            if element.tag != "programme":
                continue
            def canonical(node):
                return (node.tag, sorted(node.attrib.items()),
                        (node.text or "").strip(),
                        [canonical(child) for child in node])
            digest.update(json.dumps(canonical(element), ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            digest.update(b"\n")
            count += 1
            element.clear()
    return count, digest.hexdigest()


def channel_signature(path):
    with (gzip.open(path, "rb") if path.suffix == ".gz" else path.open("rb")) as stream:
        root = ET.parse(stream).getroot()
    result = []
    for node in root.findall("channel"):
        item = ET.fromstring(ET.tostring(node))
        for icon in list(item.findall("icon")):
            item.remove(icon)
        def canonical(n):
            return (n.tag, sorted(n.attrib.items()), (n.text or "").strip(),
                    [canonical(child) for child in n])
        result.append(canonical(item))
    return result


def stage(input_dir, output_dir, channels_csv, logos_csv, overrides):
    if output_dir.exists():
        raise ValueError("output directory must not exist")
    known_ids, name_index = load_channel_name_index(channels_csv)
    logos = load_logos(logos_csv)
    if not logos:
        raise ValueError("empty provider logo index")
    rules = json.loads(overrides.read_text(encoding="utf-8"))
    if not isinstance(rules, dict):
        raise ValueError("logo overrides must be a JSON object")
    report = {}
    output_dir.mkdir(parents=True)
    for stem, product in PRODUCTS.items():
        original = input_dir / (stem + ".xml.gz")
        if not original.exists():
            if stem not in OPTIONAL:
                raise FileNotFoundError(original)
            continue
        destination = output_dir / (stem + ".xml")
        with gzip.open(original, "rb") as src, destination.open("wb") as target:
            shutil.copyfileobj(src, target)
        before_programmes = programme_digest(original)
        before_channels = channel_signature(original)
        summary = enrich_file(destination, known_ids, name_index, logos, rules)
        candidate = destination.with_suffix(".xml.gz")
        after_programmes = programme_digest(candidate)
        if after_programmes != before_programmes:
            raise ValueError(f"{stem}: altered programme content/order")
        if channel_signature(candidate) != before_channels:
            raise ValueError(f"{stem}: altered channel IDs or non-icon metadata")
        destination.unlink()
        report[product] = {"input_programmes": before_programmes[0],
                           "input_digest": before_programmes[1],
                           "candidate_digest": after_programmes[1],
                           "logo_coverage_percent": summary["coverage_percent"],
                           "remaining_missing_logos": summary["missing_logo_after"],
                           "overrides_applied": summary["logo_overrides_applied"]}
    (output_dir / "logo-stage-evidence.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=Path("epg"))
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--channels-csv", required=True, type=Path)
    parser.add_argument("--logos-csv", required=True, type=Path)
    parser.add_argument("--overrides", type=Path, default=Path("config/logo-overrides.json"))
    args = parser.parse_args()
    evidence = stage(args.input, args.output, args.channels_csv, args.logos_csv, args.overrides)
    print(json.dumps(evidence, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
