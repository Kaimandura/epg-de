"""Check the committed Samsung XMLTV against both production pipeline stages.

platform-coverage.csv describes the *pre-reconciliation* platform export.
de-reconciliation.json records canonical ownership and the final product.
This is not an official Samsung lineup or public Pages byte verification.
"""
import csv
import gzip
import json
import re
import unittest
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUIDE = ROOT / "epg" / "samsung.xml.gz"
COVERAGE = ROOT / "reports" / "platform-coverage.csv"
RECONCILIATION = ROOT / "reports" / "de-reconciliation.json"
DATE = re.compile(r"^\d{12}(?:\d{2})?\s[+-]\d{4}$")


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
                    self.assertRegex(item.get("start", ""), DATE)
                    if item.get("stop"):
                        self.assertRegex(item.get("stop"), DATE)
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
