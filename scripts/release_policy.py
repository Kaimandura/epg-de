"""Read-only XMLTV release gate shared by producers and Pages.

Identity evidence is explicit aliases, identical IDs, or matching names AND
matching timed titles. A name alone is never permission to discard a schedule.
"""
from __future__ import annotations

import gzip
import csv
import hashlib
import html
import json
import re
import unicodedata
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

FILES = {
    "DE": {"DE-MASTER.xml.gz": "de.xml.gz", "DE-MAGENTA.xml.gz": "magenta.xml.gz",
           "DE-SAMSUNG.xml.gz": "samsung.xml.gz", "DE-PLUTO.xml.gz": "pluto.xml.gz",
           "DE-AMAZON.xml.gz": "amazon.xml.gz"},
    "USA": {"USA-MASTER.xml.gz": "usa.xml.gz", "USA-LOCAL.xml.gz": "usa-local.xml.gz",
            "USA-FAST.xml.gz": "usa-fast.xml.gz", "USA-SPORTS.xml.gz": "usa-sports.xml.gz"},
}
OPTIONAL_FILES = {'DE-AMAZON.xml.gz'}


def validate_file_set(names, region, extra=()):
    allowed = set(FILES[region]) | set(extra)
    required = allowed - OPTIONAL_FILES
    if not required <= set(names) <= allowed:
        raise ValueError(f'{region}: missing or unexpected release files: {sorted(set(names) ^ required)}')
MAGENTA_REQUIRED = ["MagentaSport.de@SD", "MagentaTV.de@MSSport",
                    *[f"MagentaTV.de@MyTeamTVSport{i:02d}" for i in range(1, 19)],
                    "MagentaTV.de@SkySportKompakt1"]


def name_key(value):
    value = unicodedata.normalize("NFKC", html.unescape(value)).casefold()
    value = re.sub(r"^(?:de|us|usa)\s*[-:|]\s*", "", value)
    value = re.sub(r"\[(?:samsung tv plus|pluto tv|prime video)\]", "", value)
    value = re.sub(r"\b(?:uhd|fhd|hd|sd|4k)\b", "", value)
    return " ".join(re.findall(r"\w+|\+", value))


@lru_cache(maxsize=100000)
def timestamp(value):
    if not re.fullmatch(r"\d{14} [+-]\d{4}", value):
        raise ValueError(f"invalid XMLTV timestamp: {value!r}")
    return datetime.strptime(value, "%Y%m%d%H%M%S %z").astimezone(timezone.utc)


def configured_aliases(root=None, mapping=None):
    root = root or Path(__file__).resolve().parents[1]
    definitions = json.loads((root / "config/amazon-prime-de.json").read_text(encoding="utf-8"))
    aliases = [{"region": "DE", "ids": ["AmazonPrime.de." + c["key"], *c.get("source_ids", [])]}
               for c in definitions["channels"] if c.get("source_ids")]
    if mapping:
        with Path(mapping).open(encoding="utf-8-sig", newline="") as stream:
            aliases.extend({"region": "USA", "ids": list(dict.fromkeys([r["xmltv_id"], r["source_id"], r.get('canonical_xmltv_id') or r['xmltv_id']]))} for r in csv.DictReader(stream))
    return aliases


def variant(cid):
    """Explicit regional/language feeds are not inferred equivalent by names.

Quality suffixes don't create a separate schedule identity. Regional station
IDs (including US callsigns) are otherwise left intact.
"""
    suffix = cid.partition("@")[2].casefold()
    suffix = re.sub(r"(?:uhd|fhd|hd|sd|4k)$", "", suffix)
    suffix = {'dach':'de','germany':'de','german':'de','deutsch':'de'}.get(suffix,suffix)
    if cid.startswith(("SamsungTVPlus.", "PlutoTV.", "AmazonPrime.")):
        return cid.split(".")[1].casefold()
    # Affiliate callsigns and subchannels are distinct even while simulcasting
    # the same network programme. Name/schedule similarity cannot erase them.
    station = re.match(r'^([KW][A-Z]{2,4}(?:[.-]?(?:DT|TV))?(?:[.-]?\d+)?)\.(?:us|ca)(?:@|$)', cid)
    if station:
        return 'station:' + station.group(1).casefold() + (':' + suffix if suffix else '')
    return suffix


@dataclass
class Channel:
    file: str
    id: str
    names: set = field(default_factory=set)
    slots: set = field(default_factory=set)
    records: set = field(default_factory=set)
    count: int = 0
    icons: tuple = ()


def inspect(path, label=None, require_active=True):
    label = label or path.name
    channels = {}
    errors = []
    count = 0
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rb") as stream:
        iterator = ET.iterparse(stream, events=("start", "end"))
        _, root = next(iterator)
        if root.tag != "tv":
            raise ValueError(f"{label}: expected tv root")
        for event, node in iterator:
            if event != "end":
                continue
            if node.tag == "channel":
                cid = node.get("id", "").strip()
                if not cid or cid in channels:
                    errors.append(f"{label}: empty/duplicate channel ID {cid}")
                names = {name_key(n.text or "") for n in node.findall("display-name")}
                names.discard("")
                if not names:
                    errors.append(f"{label}: {cid}: missing display name")
                channels[cid] = Channel(label, cid, names,
                    icons=tuple(n.get("src", "").strip() for n in node.findall("icon") if n.get("src", "").strip()))
            elif node.tag == "programme":
                count += 1
                cid = node.get("channel")
                title = (node.findtext("title") or "").strip()
                if cid not in channels:
                    errors.append(f"{label}: unknown programme channel {cid}")
                elif not title:
                    errors.append(f"{label}: {cid}: missing title")
                else:
                    try:
                        start = timestamp(node.get("start", ""))
                        stop = timestamp(node.get("stop")) if node.get("stop") else None
                        if stop is not None and stop <= start:
                            raise ValueError("non-positive programme duration")
                        signature = (start.isoformat(), stop.isoformat() if stop else "", title.casefold())
                        channel = channels[cid]
                        if signature in channel.slots:
                            errors.append(f"{label}: {cid}: duplicate programme {signature}")
                        channel.slots.add(signature)
                        channel.count += 1
                        # Full content hash excludes only the alias ID and formatting.
                        attrs = dict(node.attrib)
                        attrs.pop("channel", None)
                        def content(element):
                            return [element.tag, sorted(element.attrib.items()),
                                    (element.text or "").strip(), [content(c) for c in element]]
                        payload = [sorted(attrs.items()), [content(c) for c in node]]
                        channel.records.add(hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode()).hexdigest())
                    except ValueError as exc:
                        errors.append(f"{label}: {cid}: {exc}")
            else:
                continue
            root.remove(node)
        if not channels or not count:
            errors.append(f"{label}: empty guide")
    if require_active:
        errors.extend(f"{label}: inactive channel {c.id}" for c in channels.values() if not c.count)
    if label == "DE-MAGENTA.xml.gz":
        errors.extend(f"{label}: missing/inactive required sport {cid}" for cid in MAGENTA_REQUIRED
                      if cid not in channels or not channels[cid].count)
    with_logos = sum(bool(c.icons) for c in channels.values())
    return list(channels.values()), errors, {"channels": len(channels), "programmes": count,
        "with_logos": with_logos, "missing_logos": len(channels)-with_logos,
        "logo_presence_percent": round(with_logos / len(channels)*100, 2) if channels else 0.0}


def identity_groups(channels, aliases=()):
    parent = list(range(len(channels)))
    variants = [{variant(c.id)} - {""} for c in channels]
    evidence = []
    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    def join(a, b, reason):
        aroot, broot = find(a), find(b)
        if aroot != broot:
            if reason == "name-and-schedule" and len(variants[aroot] | variants[broot]) > 1:
                return
            parent[broot] = aroot
            variants[aroot].update(variants[broot])
            evidence.append({"left": [channels[a].file, channels[a].id],
                             "right": [channels[b].file, channels[b].id], "reason": reason})
    ids = defaultdict(list)
    names = defaultdict(list)
    for i, c in enumerate(channels):
        # DE and USA contain different regional products, never conflate by name.
        region = c.file.split("-", 1)[0]
        ids[(region, c.id.casefold())].append(i)
        for name in sorted(c.names):
            names[(region, name)].append(i)
    for bucket in ids.values():
        for i in bucket[1:]:
            join(bucket[0], i, "same-id")
    for alias in aliases:
        bucket = [i for cid in alias["ids"] for i in ids.get((alias["region"], cid.casefold()), [])]
        for i in bucket[1:]:
            join(bucket[0], i, "explicit-alias")
    compared = set()
    for bucket in names.values():
        for pos, a in enumerate(bucket):
            for b in bucket[pos + 1:]:
                key = (min(a,b), max(a,b))
                if key in compared or find(a) == find(b):
                    continue
                compared.add(key)
                left, right = channels[a], channels[b]
                lv, rv = variant(left.id), variant(right.id)
                if lv and rv and lv != rv:
                    continue
                shared = len(left.slots & right.slots)
                minimum = min(len(left.slots), len(right.slots))
                # Titles and UTC instants both have to agree. Timing alone is unsafe.
                varied_titles = len({slot[2] for slot in left.slots & right.slots}) >= 2
                if shared >= 3 and varied_titles and minimum and shared / minimum >= .9:
                    join(a, b, "name-and-schedule")
    groups = defaultdict(list)
    for i, channel in enumerate(channels):
        groups[find(i)].append(channel)
    return list(groups.values()), evidence


def audit(paths, aliases=None, require_active=True):
    if aliases is None:
        aliases = configured_aliases()
    channels, errors, summaries = [], [], {}
    allowed = set().union(*(set(names) for names in FILES.values()))
    for label, path in paths.items():
        if label not in allowed:
            errors.append(f"uncontrolled release file: {label}")
            continue
        try:
            rows, issues, counts = inspect(Path(path), label, require_active)
            channels.extend(rows)
            errors.extend(issues)
            summaries[label] = counts
        except (OSError, ValueError, ET.ParseError) as exc:
            errors.append(f"{label}: {exc}")
    groups, evidence = identity_groups(channels, aliases)
    violations = []
    for group in groups:
        files = [c.file for c in group]
        owners = [f for f in files if not f.endswith("-MASTER.xml.gz")]
        # USA-MASTER is the existing national category, not a union of all USA.
        if files[0].startswith("USA-"):
            owners = files
        if len(group) > 2 or len(owners) > 1 or len(files) != len(set(files)):
            violations.append({"reason": "identity outside one owner plus optional master",
                               "occurrences": [{"file": c.file, "id": c.id, "programmes": c.count} for c in group]})
        elif len(group) == 2 and group[0].records != group[1].records:
            errors.append(f"owner/MASTER schedule mismatch: {files}: {[c.id for c in group]}")
        for c in group:
            prefixes = {"SamsungTVPlus.": "DE-SAMSUNG.xml.gz", "PlutoTV.": "DE-PLUTO.xml.gz",
                        "AmazonPrime.": "DE-AMAZON.xml.gz", "MagentaTV.": "DE-MAGENTA.xml.gz"}
            for prefix, owner in prefixes.items():
                if c.id.startswith(prefix) and c.file not in (owner, "DE-MASTER.xml.gz"):
                    errors.append(f"{c.file}: {c.id}: wrong provider (expected {owner})")
    return {"status": "failed" if errors or violations else "passed", "errors": errors,
            "identity_violations": violations, "identity_evidence": evidence, "outputs": summaries,
            "aliases": list(aliases)}
