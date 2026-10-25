#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import http.cookiejar
import json
import re
import shutil
import struct
import unicodedata
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LogoCandidate:
    channel_id: str
    feed: str
    url: str
    width: int
    height: int
    fmt: str
    source: str = "iptv-org/database/data/logos.csv"
    names: tuple[str, ...] = ()


def split_values(value: str) -> list[str]:
    return [part.strip() for part in re.split(r"[;,]", value or "") if part.strip()]


def as_bool(value: str) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "y"}


def split_feed_id(xmltv_id: str) -> tuple[str, str]:
    if "@" not in xmltv_id:
        return xmltv_id, ""
    base, feed = xmltv_id.split("@", 1)
    return base, feed


def normalized_name(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.casefold()
    text = re.sub(r"^de\s*(?:-|:|\|)\s*", "", text)
    text = re.sub(r"\[[^\]]+\]", " ", text)
    text = re.sub(r"\b(?:uhd|fhd|full\s*hd|hd|sd|4k|1080p|720p|576p|480p)\b", " ", text)
    # A plus sign is part of a brand (RTL and RTL+ are different channels).
    text = re.sub(r"[^a-z0-9+]+", " ", text)
    return " ".join(text.split())


def display_names(channel: ET.Element) -> list[str]:
    return [
        (node.text or "").strip()
        for node in channel.findall("display-name")
        if (node.text or "").strip()
    ]


def load_channel_name_index(path: Path) -> tuple[set[str], dict[str, set[str]]]:
    known_ids: set[str] = set()
    names: dict[str, set[str]] = defaultdict(set)

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            channel_id = (row.get("id") or "").strip()
            if not channel_id:
                continue
            if as_bool(row.get("is_nsfw") or ""):
                continue
            if (row.get("closed") or "").strip():
                continue

            known_ids.add(channel_id)
            values = [(row.get("name") or "").strip()] + split_values(row.get("alt_names") or "")
            for value in values:
                key = normalized_name(value)
                if key:
                    names[key].add(channel_id)

    return known_ids, names


def safe_int(value: str) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def load_logos(path: Path) -> dict[tuple[str, str], list[LogoCandidate]]:
    index: dict[tuple[str, str], list[LogoCandidate]] = defaultdict(list)

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            channel_id = (row.get("channel") or "").strip()
            feed = (row.get("feed") or "").strip()
            url = (row.get("url") or "").strip()
            if not channel_id or not url or not url.lower().startswith("https://"):
                continue
            if str(row.get("in_use") or "TRUE").strip().casefold() in {"false", "0", "no"}:
                continue
            index[(channel_id, feed)].append(
                LogoCandidate(
                    channel_id=channel_id,
                    feed=feed,
                    url=url,
                    width=safe_int(row.get("width") or "0"),
                    height=safe_int(row.get("height") or "0"),
                    fmt=(row.get("format") or "").strip().upper(),
                )
            )

    raster_formats = {"PNG", "WEBP", "JPEG", "JPG"}

    def score(candidate: LogoCandidate) -> tuple[int, int, int, str]:
        area = candidate.width * candidate.height
        raster = 1 if candidate.fmt in raster_formats else 0
        square = -abs(candidate.width - candidate.height) if candidate.width and candidate.height else 0
        return raster, area, square, candidate.url

    for key in index:
        index[key].sort(key=score, reverse=True)
    return index


def best_logo(channel_id: str, logos: dict[tuple[str, str], list[LogoCandidate]]) -> str | None:
    base, feed = split_feed_id(channel_id)
    keys: list[tuple[str, str]] = []
    if feed:
        keys.append((base, feed))
    keys.extend([(channel_id, ""), (base, "")])

    seen: set[tuple[str, str]] = set()
    for key in keys:
        if key in seen:
            continue
        seen.add(key)
        candidates = logos.get(key, [])
        if candidates:
            return candidates[0].url
    return None


def unique_name_match(channel: ET.Element, name_index: dict[str, set[str]]) -> str | None:
    matched: set[str] = set()
    for value in display_names(channel):
        key = normalized_name(value)
        if not key:
            continue
        ids = name_index.get(key, set())
        # An ambiguous alias must not be ignored just because another name
        # happens to produce one result.
        matched.update(ids)
    return next(iter(matched)) if len(matched) == 1 else None


def valid_logo_url(url: str) -> bool:
    try:
        parsed = urllib.parse.urlsplit(url)
        return (parsed.scheme == "https" and bool(parsed.hostname)
                and not parsed.username and not parsed.password
                and not any(char.isspace() for char in url))
    except ValueError:
        return False


def verify_logo(url: str) -> dict:
    """GET the actual asset; an HTTP 200 HTML error page is not a logo.

    Identity is established by the exact source record, not by this probe.
    Keep the content digest and final URL as separate transport evidence.
    """
    if not valid_logo_url(url):
        return {"status": "failed", "reason": "invalid HTTPS logo URL"}
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "epg-de-logo-qa/1.0"})
        with urllib.request.urlopen(request, timeout=15) as response:
            final_url = response.geturl()
            content_type = response.headers.get_content_type()
            data = response.read(4 * 1024 * 1024 + 1)
        if not valid_logo_url(final_url) or len(data) > 4 * 1024 * 1024:
            raise ValueError("insecure redirect or image exceeds 4 MiB")
        fmt = ""
        if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 33:
            width, height = struct.unpack(">II", data[16:24])
            if width <= 1 or height <= 1 or data[12:16] != b"IHDR" or b"IEND" not in data[-32:]:
                raise ValueError("invalid/incomplete PNG")
            fmt = "PNG"
        elif data.startswith(b"\xff\xd8\xff") and data.rstrip().endswith(b"\xff\xd9"):
            fmt = "JPEG"
        elif data.startswith(b"RIFF") and data[8:12] == b"WEBP" and len(data) >= 20:
            if struct.unpack("<I", data[4:8])[0] + 8 != len(data):
                raise ValueError("incomplete WebP")
            fmt = "WEBP"
        elif data.startswith((b"GIF87a", b"GIF89a")) and data.rstrip().endswith(b";"):
            fmt = "GIF"
        else:
            # SVG is a logo asset only, never rendered or executed here.
            node = ET.fromstring(data)
            shapes = {"path", "polyline", "polygon", "circle", "ellipse", "rect", "line", "text", "image", "use"}
            if (node.tag.rsplit("}", 1)[-1] == "svg"
                    and any(child.tag.rsplit("}", 1)[-1] in shapes for child in node.iter())):
                fmt = "SVG"
        if not fmt or content_type in {"text/html", "application/json"}:
            raise ValueError("response is not a supported image")
        return {"status": "passed", "format": fmt, "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(), "final_url": final_url}
    except (OSError, ValueError, ET.ParseError) as exc:
        return {"status": "failed", "reason": str(exc)}


def load_provider_logos(config: Path, cache_dir: Path) -> tuple[dict[str, LogoCandidate], list[dict]]:
    """Use native provider IDs, preserving the explicit country namespace.

    The same versioned platform configuration owns provider selection and logo
    sources. Never construct asset URLs or infer a provider ID from a name.
    """
    result, evidence = {}, []
    cache_dir.mkdir(parents=True, exist_ok=True)
    definitions = json.loads(config.read_text(encoding="utf-8"))["platforms"]
    for definition in definitions.values():
        for source in definition.get("logo_sources", []):
            namespace, region, url = source["namespace"], source["region"], source["url"]
            path = cache_dir / f"{namespace}-{region}.xml.gz"
            entry = {"url": url, "namespace": namespace, "region": region}
            try:
                if not valid_logo_url(url):
                    raise ValueError("invalid provider source URL")
                if not path.exists():
                    with urllib.request.urlopen(url, timeout=45) as response:
                        payload = response.read(32 * 1024 * 1024 + 1)
                    if len(payload) > 32 * 1024 * 1024:
                        raise ValueError("provider guide exceeds size limit")
                    path.write_bytes(payload)
                entry["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
                count = 0
                for channel in read_nodes(path):
                    if channel.tag != "channel":
                        continue
                    native = channel.get("id", "")
                    urls = {node.get("src", "").strip() for node in channel.findall("icon")}
                    urls = {value for value in urls if valid_logo_url(value)}
                    if not native or len(urls) != 1:
                        continue
                    cid = f"{namespace}.{region}.{native}"
                    candidate = LogoCandidate(cid, "", next(iter(urls)), 0, 0, "", url,
                                              tuple(display_names(channel)))
                    if cid in result and result[cid].url != candidate.url:
                        raise ValueError("conflicting native provider logo: " + cid)
                    result[cid] = candidate
                    count += 1
                entry.update(status="passed", channels=count)
            except (OSError, ValueError, ET.ParseError) as exc:
                entry.update(status="failed", reason=str(exc))
            evidence.append(entry)
    return result, evidence


def legacy_pluto_logo(channel, provider_logos):
    """Join explicit USA .pluto IDs using both ID stem and provider name."""
    cid = channel.get("id", "")
    if not cid.endswith(".pluto"):
        return None
    stem = normalized_name(cid.removesuffix(".pluto")).replace(" ", "")
    named = {normalized_name(re.sub(r"^USA?:\s*PLUTO\s*-\s*", "", name, flags=re.I)).replace(" ", "")
             for name in display_names(channel) if re.match(r"^USA?:\s*PLUTO\s*-\s*", name, re.I)}
    if stem not in named:
        return None
    matches = [candidate for key, candidate in provider_logos.items() if key.startswith("PlutoTV.us.")
               and stem in {normalized_name(name).replace(" ", "") for name in candidate.names}]
    return matches[0] if len(matches) == 1 else None


def read_nodes(path: Path):
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rb") as stream:
        iterator = ET.iterparse(stream, events=("start", "end"))
        _, root = next(iterator)
        if root.tag != "tv":
            raise ValueError(f"{path}: expected tv root")
        for event, node in iterator:
            if event == "end" and node.tag in {"channel", "programme"}:
                yield node
                root.remove(node)


def magenta_logo_records(records: list[dict], definitions: Path) -> dict[str, LogoCandidate]:
    native = {str(row["contentId"]): row for row in records if row.get("contentId")}
    result = {}
    for node in ET.parse(definitions).getroot().findall("channel"):
        if node.get("site") != "web.magentatv.de":
            continue
        row = native.get(node.get("site_id"))
        if not row:
            continue
        pictures = [p for p in row.get("pictures", []) if p.get("imageType") == "15" and p.get("href")]
        urls = {p["href"] for p in pictures}
        if len(urls) != 1:
            continue
        original = next(iter(urls))
        # Only upgrade this provider's documented asset host; GET validation
        # subsequently proves that the HTTPS asset really exists.
        if original.startswith("http://ngiss.t-online.de/"):
            original = "https://" + original[len("http://"):]
        cid = node.get("xmltv_id")
        if cid and valid_logo_url(original):
            result[cid] = LogoCandidate(cid, "", original, 120, 48, "PNG",
                                       "https://api.prod.sngtv.magentatv.de/EPG/JSON/AllChannel#" + str(row["contentId"]),
                                       (row.get("name", ""),))
    return result


def load_magenta_logos(definitions: Path, cache_dir: Path) -> tuple[dict, dict]:
    """Read the same public anonymous API used by the existing DE grabber.

    No credentials are used or written. Join contentId to site_id, never a
    similar sender name, and select channel logo type 15, not programme art.
    """
    base = "https://api.prod.sngtv.magentatv.de/EPG/JSON/"
    evidence = {"url": base + "AllChannel"}
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / "magenta-channels.json"
    try:
        if not path.exists():
            opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
            def post(endpoint, data, headers=None):
                request = urllib.request.Request(base + endpoint, data=json.dumps(data).encode(),
                    headers={"Content-Type": "application/json", **(headers or {})})
                with opener.open(request, timeout=30) as response:
                    return json.loads(response.read(8 * 1024 * 1024))
            auth = post("Authenticate?SID=firstup&T=Windows_chrome_118", {
                "terminalid": "00:00:00:00:00:00", "mac": "00:00:00:00:00:00", "terminaltype": "WEBTV",
                "utcEnable": 1, "timezone": "Etc/GMT0", "userType": 3, "terminalvendor": "Unknown"})
            if not auth.get("csrfToken"):
                raise ValueError("anonymous provider bootstrap unavailable")
            data = post("AllChannel", {"channelNamespace": 2, "filterlist": [{"key": "IsHide", "value": "-1"}],
                        "metaDataVer": "Channel/1.1", "returnSatChannel": 0}, {"X_CSRFTOKEN": auth["csrfToken"]})
            if not data.get("channellist"):
                raise ValueError("provider returned no channel metadata")
            path.write_text(json.dumps({"channellist": data["channellist"]}), encoding="utf-8")
        payload = path.read_bytes()
        result = magenta_logo_records(json.loads(payload)["channellist"], definitions)
        evidence.update(status="passed", channels=len(result), sha256=hashlib.sha256(payload).hexdigest())
        return result, evidence
    except (OSError, ValueError, ET.ParseError) as exc:
        evidence.update(status="failed", reason=str(exc))
        return {}, evidence


def programme_digest(path: Path) -> tuple[int, str]:
    digest, count = hashlib.sha256(), 0
    for node in read_nodes(path):
        if node.tag == "programme":
            node.tail = None
            digest.update(ET.tostring(node, encoding="utf-8"))
            count += 1
    return count, digest.hexdigest()


def write_xml_and_gzip(root: ET.Element, xml_path: Path) -> None:
    ET.indent(root, space="  ")
    payload = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    xml_path.write_bytes(payload)
    gzip_path = xml_path.with_suffix(xml_path.suffix + ".gz")
    with gzip_path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as target:
            target.write(payload)
    with gzip.open(gzip_path, "rb") as handle:
        if handle.read() != payload:
            raise RuntimeError(f"gzip roundtrip mismatch: {gzip_path}")


def enrich_file(
    path: Path,
    known_ids: set[str],
    name_index: dict[str, set[str]],
    logos: dict[tuple[str, str], list[LogoCandidate]],
    provider_logos: dict[str, LogoCandidate] | None = None,
    verification: dict | None = None,
    evidence_rows: list | None = None,
    probe=verify_logo,
) -> dict[str, int | str | float]:
    provider_logos = provider_logos or {}
    verification = verification if verification is not None else {}
    evidence_rows = evidence_rows if evidence_rows is not None else []
    # Keep only channel declarations in memory. USA-LOCAL contains hundreds of
    # thousands of programme records; a full ElementTree is unnecessary.
    channels = [ET.fromstring(ET.tostring(node)) for node in read_nodes(path) if node.tag == "channel"]
    original_ids = [node.get("id", "") for node in channels]
    if not all(original_ids) or len(original_ids) != len(set(original_ids)):
        raise RuntimeError(f"{path}: empty or duplicate channel IDs before logo enrichment")

    programmes_before = programme_digest(path)
    before = 0
    exact_added = 0
    name_added = 0
    replaced = 0
    existing_verified = 0
    assignments = {}
    for channel in channels:
        existing = [
            node.attrib.get("src", "").strip()
            for node in channel.findall("icon")
            if node.attrib.get("src", "").strip()
        ]
        xmltv_id = (channel.attrib.get("id") or "").strip()
        candidate = provider_logos.get(xmltv_id)
        if existing:
            before += 1
            # Rebranding corrections require the exact native ID AND a
            # current provider name. A catalog name or shared logo URL alone
            # never authorizes replacing an existing sender's artwork.
            provider_names = {normalized_name(n) for n in candidate.names} if candidate else set()
            if not provider_names.intersection(normalized_name(n) for n in display_names(channel)):
                continue
        method = "native-provider-id"
        matched_id = xmltv_id
        if not candidate:
            candidate = legacy_pluto_logo(channel, provider_logos)
            if candidate:
                method = "unique-native-provider-name-and-id-stem"
                matched_id = candidate.channel_id
        if candidate:
            logo_url = candidate.url
        else:
            logo_url = best_logo(xmltv_id, logos) if split_feed_id(xmltv_id)[0] in known_ids else None
            method = "database-id"
            if not logo_url:
                matched_id = unique_name_match(channel, name_index)
                logo_url = best_logo(matched_id, logos) if matched_id else None
                method = "unique-database-name"
        if logo_url:
            assignments[xmltv_id] = {"url": logo_url, "method": method, "matched_id": matched_id,
                                     "source": candidate.source if candidate else "iptv-org/database/data/logos.csv",
                                     "action": ("verify-existing" if existing == [logo_url] else
                                                "replace-native-provider-logo") if existing else "add",
                                     "previous_icons": existing}
    pending = sorted({row["url"] for row in assignments.values()} - verification.keys())
    with ThreadPoolExecutor(max_workers=8) as pool:
        verification.update(zip(pending, pool.map(probe, pending)))
    additions, replacements = {}, {}
    for cid, row in assignments.items():
        transport = verification[row["url"]]
        evidence_rows.append({"file": path.name, "xmltv_id": cid, **row, "transport": transport})
        if transport.get("status") != "passed":
            continue
        if row["action"] == "verify-existing":
            existing_verified += 1
            continue
        if row["action"] == "replace-native-provider-logo":
            replacements[cid] = row["url"]
            replaced += 1
            continue
        additions[cid] = row["url"]
        if row["method"] in {"unique-database-name", "unique-native-provider-name-and-id-stem"}:
            name_added += 1
        else:
            exact_added += 1
    after = before + len(additions)
    missing = len(channels) - after
    coverage = round((after / len(channels) * 100.0), 2) if channels else 0.0
    if additions or replacements:
        temporary = path.with_name(path.name + ".logos.tmp")
        with path.open("rb") as stream:
            _, root = next(ET.iterparse(stream, events=("start",)))
        opening = ET.tostring(ET.Element("tv", root.attrib), encoding="utf-8").replace(b" />", b">")
        with temporary.open("wb") as target:
            target.write(b'<?xml version="1.0" encoding="utf-8"?>\n' + opening + b"\n")
            for node in read_nodes(path):
                if node.tag == "channel":
                    cid = node.get("id")
                    if cid in replacements:
                        for old in node.findall("icon"):
                            node.remove(old)
                    if cid in additions or cid in replacements:
                        node.append(ET.Element("icon", {"src": (additions | replacements)[cid]}))
                node.tail = None
                target.write(ET.tostring(node, encoding="utf-8") + b"\n")
            target.write(b"</tv>\n")
        ids_after = [node.get("id") for node in read_nodes(temporary) if node.tag == "channel"]
        if ids_after != original_ids or programme_digest(temporary) != programmes_before:
            temporary.unlink()
            raise RuntimeError(f"{path}: enrichment changed IDs or programme metadata")
        temporary.replace(path)
        gzip_path = path.with_suffix(path.suffix + ".gz")
        with path.open("rb") as source, gzip_path.open("wb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as target:
                shutil.copyfileobj(source, target)
        if programme_digest(gzip_path) != programmes_before:
            raise RuntimeError(f"{gzip_path}: gzip programme roundtrip mismatch")
    return {
        "file": path.name,
        "channel_count": len(channels),
        "with_logo_before": before,
        "logos_added_exact": exact_added,
        "logos_added_unique_name": name_added,
        "logos_replaced_native": replaced,
        "existing_native_logos_verified": existing_verified,
        "with_logo_after": after,
        "missing_logo_after": missing,
        "coverage_percent": coverage,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fill missing XMLTV channel logos from iptv-org/database without changing channel identity or schedules."
    )
    parser.add_argument("--channels-csv", required=True, type=Path)
    parser.add_argument("--logos-csv", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--platform-config", type=Path, help="Existing platform config with native-ID logo sources")
    parser.add_argument("--provider-cache", type=Path, default=Path("build/logo-sources"))
    parser.add_argument("--evidence-report", type=Path)
    parser.add_argument("--channel-definitions", type=Path, help="Collected channel XML for exact Magenta contentId joins")
    parser.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args()

    known_ids, name_index = load_channel_name_index(args.channels_csv)
    logos = load_logos(args.logos_csv)
    if not logos:
        raise RuntimeError("No usable logo records loaded")

    rows: list[dict[str, int | str | float]] = []
    providers, source_evidence = load_provider_logos(args.platform_config, args.provider_cache) if args.platform_config else ({}, [])
    if args.channel_definitions:
        magenta, magenta_evidence = load_magenta_logos(args.channel_definitions, args.provider_cache)
        providers.update(magenta)
        source_evidence.append(magenta_evidence)
    verification, evidence = {}, []
    for path in args.files:
        if not path.exists():
            raise RuntimeError(f"EPG file not found: {path}")
        row = enrich_file(path, known_ids, name_index, logos, providers, verification, evidence)
        rows.append(row)
        print(
            "Logo enrichment: "
            f"{row['file']} channels={row['channel_count']} "
            f"before={row['with_logo_before']} "
            f"added_exact={row['logos_added_exact']} "
            f"added_name={row['logos_added_unique_name']} "
            f"after={row['with_logo_after']} "
            f"missing={row['missing_logo_after']} "
            f"coverage={row['coverage_percent']}%",
            flush=True,
        )

    args.report.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "file",
        "channel_count",
        "with_logo_before",
        "logos_added_exact",
        "logos_added_unique_name",
        "logos_replaced_native",
        "existing_native_logos_verified",
        "with_logo_after",
        "missing_logo_after",
        "coverage_percent",
    ]
    with args.report.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    evidence_path = args.evidence_report or args.report.with_suffix(".json")
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(json.dumps({"provider_sources": source_evidence,
                                        "additions": [r for r in evidence if r["action"] == "add"],
                                        "native_corrections": [r for r in evidence if r["action"] == "replace-native-provider-logo"],
                                        "existing_native_verifications": [r for r in evidence if r["action"] == "verify-existing"],
                                        "image_probes": verification,
                                        "coverage": rows, "existing_logos_reverified": False},
                                       ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
