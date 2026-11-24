"""Check the committed Samsung XMLTV against both production pipeline stages.

platform-coverage.csv describes the *pre-reconciliation* platform export.
de-reconciliation.json records canonical ownership and the final product.
This is not an official Samsung lineup or public Pages byte verification.
"""
import csv
import gzip
import json
import sys
import unittest
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUIDE = ROOT / "epg" / "samsung.xml.gz"
COVERAGE = ROOT / "reports" / "platform-coverage.csv"
RECONCILIATION = ROOT / "reports" / "de-reconciliation.json"
sys.path.insert(0, str(ROOT / "scripts"))
from release_policy import timestamp


def validate_programme_times(item):
    start = timestamp(item.get("start", ""))
    stop_text = item.get("stop")
    if stop_text:
        stop = timestamp(stop_text)
        if stop <= start:
            raise ValueError("programme stop must be after start")


class SamsungTimestampContractTest(unittest.TestCase):
    def test_rejects_invalid_times(self):
        for start, stop in [
            ("20260230090000 +0000", None),
            ("20260228090000 +0000", "20260230093000 +0000"),
            ("20260228090000 +0000", "20260228090000 +0000"),
            ("20260228090000 +0000", "20260228085959 +0000"),
            ("20260228090000 +0000", "20260228100000 +0200"),
            ("202602280900 +0000", None),
            ("20260228090000", None),
            ("20260228090000 +2500", None),
        ]:
            with self.subTest(start=start, stop=stop):
                attrs = {"start": start}
                if stop is not None:
                    attrs["stop"] = stop
                with self.assertRaises(ValueError):
                    validate_programme_times(ET.Element("programme", attrs))

    def test_accepts_valid_times_and_optional_stop(self):
        for start, stop in [
            ("20240229090000 +0000", None),
            ("20260228090000 +0000", ""),
            ("20260228090000 +0000", "20260228090001 +0000"),
            ("20260228233000 +0000", "20260301003000 +0000"),
            ("20260228090000 +0200", "20260228080000 +0000"),
        ]:
            with self.subTest(start=start, stop=stop):
                attrs = {"start": start}
                if stop is not None:
                    attrs["stop"] = stop
                validate_programme_times(ET.Element("programme", attrs))


class SamsungPublishedSnapshotTest(unittest.TestCase):
    def test_samsung_snapshot_matches_reconciled_release(self):
        with COVERAGE.open(encoding="utf-8-sig", newline="") as handle:
            rows = [r for r in csv.DictReader(handle) if r.get("platform") == "samsung"]
        self.assertEqual(len(rows), 1)
        pre = rows[0]
        self.assertEqual(pre["output"], "samsung.xml.gz")
        reconciliation = json.loads(RECONCILIATION.read_text(encoding="utf-8"))
        self.assertEqual(reconciliation["region"], "DE")
        self.assertEqual(reconciliation["status"], "passed")
        final = reconciliation["outputs"]["DE-SAMSUNG.xml.gz"]
        mappings = [r for r in reconciliation["mapping"]
                    if r["source_file"] == "DE-SAMSUNG.xml.gz"]
        self.assertEqual(len(mappings), int(pre["channel_count"]),
                         "Pre-reconciliation count differs from its source mapping")
        self.assertEqual(len({r["source_id"] for r in mappings}), len(mappings),
                         "Duplicate Samsung source ID in reconciliation mapping")
        owned = {r["canonical_id"] for r in mappings
                 if r["owner"] == "DE-SAMSUNG.xml.gz"}
        self.assertEqual(len(owned), final["channels"],
                         "Samsung ownership does not match final release count")

        channel_ids = set()
        programme_counts = Counter()
        with gzip.open(GUIDE, "rb") as handle:
            for _, item in ET.iterparse(handle, events=("end",)):
                if item.tag == "channel":
                    ident = item.get("id")
                    self.assertTrue(ident)
                    self.assertNotIn(ident, channel_ids, f"Duplicate channel: {ident}")
                    self.assertTrue(any((n.text or "").strip()
                                        for n in item.findall("display-name")))
                    channel_ids.add(ident)
                    item.clear()
                elif item.tag == "programme":
                    ident = item.get("channel")
                    self.assertTrue(ident)
                    validate_programme_times(item)
                    programme_counts[ident] += 1
                    item.clear()

        self.assertEqual(channel_ids, owned,
                         "Published Samsung channel IDs differ from reconciled ownership")
        self.assertEqual(set(programme_counts), channel_ids,
                         "All published Samsung channels need programmes; no orphan refs")
        self.assertEqual(len(channel_ids), final["channels"])
        self.assertEqual(sum(programme_counts.values()), final["programmes"])
        self.assertGreater(final["programmes"], 0)


if __name__ == "__main__":
    unittest.main()
