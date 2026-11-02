#!/usr/bin/env python3
"""Read-only HTTPS logo reachability and image-content audit for all XMLTV products."""
import argparse
import gzip
import json
import ssl
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlsplit

from audit_all_epg_products import PRODUCTS, OPTIONAL

IMAGE_TYPES = ("image/png", "image/jpeg", "image/webp", "image/gif", "image/svg+xml")
SIGNATURES = (bytes.fromhex("89504e470d0a1a0a"), bytes.fromhex("ffd8ff"), b"RIFF", b"GIF87a", b"GIF89a")


def check_url(url, timeout=10):
    def fail(reason):
        return {"url": url, "ok": False, "reason": reason}
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        return fail("non_https_or_credentials")
    req = urllib.request.Request(url, headers={"User-Agent": "EPG-DE-Logo-Audit/1.0", "Accept": "image/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context()) as response:
            final = urlsplit(response.geturl())
            if final.scheme != "https" or final.username or final.password:
                return fail("unsafe_redirect")
            mime = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
            sample = response.read(512)
            if mime not in IMAGE_TYPES:
                return fail("not_image_content_type")
            signature = sample.lstrip().lower()
            valid = (sample.startswith(SIGNATURES[:2]) or
                     (sample.startswith(b"RIFF") and sample[8:12] == b"WEBP") or
                     sample.startswith(SIGNATURES[3:]) or
                     (mime == "image/svg+xml" and (b"<svg" in signature or b"<?xml" in signature)))
            if not valid:
                return fail("not_image_bytes")
            return {"url": url, "ok": True, "http_status": response.status, "content_type": mime}
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        return fail(type(exc).__name__)


def collect(directory):
    usage = {}
    missing = []
    for stem, product in PRODUCTS.items():
        path = directory / (stem + ".xml.gz")
        if not path.is_file():
            if stem not in OPTIONAL:
                missing.append({"product": product, "reason": "missing_feed"})
            continue
        with gzip.open(path, "rb") as handle:
            root = ET.parse(handle).getroot()
        for ch in root.findall("channel"):
            urls = [(icon.get("src") or "").strip() for icon in ch.findall("icon")]
            urls = [url for url in urls if url]
            if not urls:
                missing.append({"product": product, "channel": ch.get("id"), "reason": "missing_logo"})
            for url in urls:
                usage.setdefault(url, []).append({"product": product, "channel": ch.get("id")})
    return usage, missing


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, default=Path("epg"))
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--overrides", type=Path, help="Audit every configured replacement asset separately")
    parser.add_argument("--workers", type=int, default=12)
    args = parser.parse_args()
    if args.overrides:
        overrides = json.loads(args.overrides.read_text(encoding="utf-8"))
        if not isinstance(overrides, dict) or not overrides:
            raise ValueError("logo overrides must be a nonempty object")
        usage, missing = {}, []
        for cid, url in overrides.items():
            if not isinstance(url, str) or not url.strip():
                raise ValueError(f"{cid}: empty/non-string logo override")
            usage.setdefault(url, []).append({"channel": cid})
    else:
        usage, missing = collect(args.directory)
    checks = []
    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, 24))) as pool:
        futures = {pool.submit(check_url, url): url for url in usage}
        for future in as_completed(futures):
            check = future.result()
            if not check["ok"]:
                checks.append({"reason": check["reason"], "url": check["url"], "channels": usage[check["url"]]})
    report = {"scope": "overrides" if args.overrides else "published_products",
              "products": 0 if args.overrides else len(PRODUCTS), "unique_urls_checked": len(usage),
              "missing": missing, "failed_urls": checks,
              "status": "failed" if missing or checks else "passed"}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Logo audit: checked={len(usage)} missing={len(missing)} invalid={len(checks)} status={report['status']}")
    for check in checks:
        print("Failed logo: " + json.dumps(check, ensure_ascii=True))
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
