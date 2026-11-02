#!/usr/bin/env python3
"""Read-only HTTPS logo reachability and image-content audit for all XMLTV products."""
import argparse
import csv
import gzip
import json
import ssl
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import nullcontext
from threading import BoundedSemaphore
from pathlib import Path
from urllib.parse import urlsplit

from audit_all_epg_products import PRODUCTS, OPTIONAL, load_usa_logo_debt

WIKIMEDIA_SLOTS = BoundedSemaphore(3)
AUDIT_AGENT = "EPG-DE-Logo-Audit/1.2 (https://github.com/Kaimandura/epg-de; public-logo-check)"

IMAGE_TYPES = ("image/png", "image/jpeg", "image/webp", "image/gif", "image/svg+xml")
SIGNATURES = (bytes.fromhex("89504e470d0a1a0a"), bytes.fromhex("ffd8ff"), b"RIFF", b"GIF87a", b"GIF89a")


def check_url(url, timeout=10, retries=2):
    """Verify the exact published URL; never silently accept a fallback URL.

    Retry only transient 429/5xx errors. Permanent HTTP failures and invalid
    image bytes remain hard failures with actionable HTTP status evidence.
    """
    def fail(reason, status=None):
        result = {"url": url, "ok": False, "reason": reason}
        if status is not None:
            result["http_status"] = status
        return result

    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        return fail("non_https_or_credentials")

    transient = {429, 500, 502, 503, 504}
    # Wikimedia asks automated consumers to identify themselves and to use
    # no more than three simultaneous requests (see its User-Agent policy).
    # Other providers may use the normal worker pool.
    is_wikimedia = (parsed.hostname == "upload.wikimedia.org"
                    or parsed.hostname == "thumb.wikimedia.org")
    for attempt in range(retries + 1):
        req = urllib.request.Request(
            url,
            headers={"User-Agent": AUDIT_AGENT, "Accept": "image/*"},
        )
        try:
            with (WIKIMEDIA_SLOTS if is_wikimedia else nullcontext()):
                with urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context()) as response:
                    final = urlsplit(response.geturl())
                    if final.scheme != "https" or final.username or final.password:
                        return fail("unsafe_redirect")
                    mime = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
                    sample = response.read(512)
                    if mime not in IMAGE_TYPES:
                        return fail("not_image_content_type", response.status)
                    signature = sample.lstrip().lower()
                    valid = (sample.startswith(SIGNATURES[:2]) or
                             (sample.startswith(b"RIFF") and sample[8:12] == b"WEBP") or
                             sample.startswith(SIGNATURES[3:]) or
                             (mime == "image/svg+xml" and (b"<svg" in signature or b"<?xml" in signature)))
                    if not valid:
                        return fail("not_image_bytes", response.status)
                    return {"url": url, "ok": True, "http_status": response.status, "content_type": mime}
        except urllib.error.HTTPError as exc:
            # Wikimedia may respond with 403 when temporarily throttling
            # automated image requests. A repeated 403 still fails closed.
            if (exc.code in transient or (is_wikimedia and exc.code == 403)) and attempt < retries:
                time.sleep(min(2 ** (attempt + 1), 4))
                continue
            return fail("http_error", exc.code)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            if attempt < retries:
                time.sleep(min(2 ** attempt, 2))
                continue
            return fail(type(exc).__name__)
    raise AssertionError("unreachable retry state")


def collect(directory, layout="source"):
    if layout not in {"source", "pages"}:
        raise ValueError(f"unknown XMLTV layout: {layout}")
    usage = {}
    missing = []
    for stem, product in PRODUCTS.items():
        path = directory / ((stem if layout == "source" else product) + ".xml.gz")
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
    parser.add_argument("--layout", choices=("source", "pages"), default="source")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--overrides", type=Path, help="Audit every configured replacement asset separately")
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--gap-csv", type=Path, help="Write exact channel/product logo gaps for repair")
    parser.add_argument("--usa-logo-debt", type=Path, help="Track known USA-only logo deficits without blocking verified XMLTV")
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
        usage, missing = collect(args.directory, args.layout)
    checks = []
    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, 24))) as pool:
        futures = {pool.submit(check_url, url): url for url in usage}
        for future in as_completed(futures):
            check = future.result()
            if not check["ok"]:
                checks.append({**check, "channels": usage[check["url"]]})
    # Stable output is diffable across runs and groups repeated upstream faults.
    checks.sort(key=lambda item: (item["reason"], item["url"]))
    reasons = {}
    for check in checks:
        key = str(check.get("http_status") or check["reason"])
        reasons[key] = reasons.get(key, 0) + 1
    limits = load_usa_logo_debt(args.usa_logo_debt)
    if args.overrides and limits:
        raise ValueError("USA logo debt is not allowed for the mandatory replacement asset gate")
    by_product = {}
    for item in missing:
        product = item.get("product")
        by_product[product] = by_product.get(product, 0) + 1
    extra_debt = {product: count for product, count in by_product.items()
                  if product in limits and count > limits[product]}
    hard_missing = [item for item in missing if item.get("product") not in limits
                    or item.get("reason") != "missing_logo"]
    hard_failures = []
    deferred_failures = []
    for check in checks:
        products = {item.get("product") for item in check["channels"]}
        if (limits and products <= set(limits)
                and check["reason"] not in {"non_https_or_credentials", "unsafe_redirect"}):
            deferred_failures.append(check)
        else:
            hard_failures.append(check)
    if extra_debt:
        hard_missing.append({"reason": "usa_logo_debt_increased", "counts": extra_debt})
    report = {"scope": "overrides" if args.overrides else "published_products",
              "products": 0 if args.overrides else len(PRODUCTS),
              "unique_urls_checked": len(usage),
              "missing": missing, "failed_urls": checks,
              "failure_counts": dict(sorted(reasons.items())),
              "deferred_usa": {"missing": [item for item in missing if item.get("product") in limits],
                               "failed_urls": deferred_failures,
                               "limits": limits},
              "release_blockers": {"missing": hard_missing, "failed_urls": hard_failures},
              "status": "failed" if hard_missing or hard_failures else "passed"}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.gap_csv:
        rows = [{"product": item.get("product", ""), "channel": item.get("channel", ""),
                 "reason": item["reason"], "url": ""} for item in missing]
        for failure in checks:
            for item in failure["channels"]:
                rows.append({"product": item.get("product", ""), "channel": item.get("channel", ""),
                             "reason": str(failure.get("http_status") or failure["reason"]),
                             "url": failure["url"]})
        rows.sort(key=lambda item: (item["product"], item["channel"], item["reason"], item["url"]))
        args.gap_csv.parent.mkdir(parents=True, exist_ok=True)
        with args.gap_csv.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=("product", "channel", "reason", "url"))
            writer.writeheader()
            writer.writerows(rows)
    print(f"Logo audit: checked={len(usage)} missing={len(missing)} invalid={len(checks)} status={report['status']}")
    for check in checks:
        print("Failed logo: " + json.dumps(check, ensure_ascii=True))
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
