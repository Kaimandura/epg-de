"""Regression guard for the checked-in Samsung XMLTV publication (issue #34).

This is an internal consistency test, NOT an official Samsung channel-list
reconciliation or a public GitHub Pages verification.
"""
import csv
import gzip
import re
import unittest
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUIDE = ROOT / "epg" / "samsung.xml.gz"
COVERAGE = ROOT / "reports" / "platform-coverage.csv"
XMLTV_DATE = re.compile(r"^\d{12}(?:\d{2})?\s[+-]\d{4}$")


class SamsungPublishedSnapshotTest(unittest.TestCase):
    def test_samsung_snapshot_matches_checked_in_coverage(self):
        self.assertTrue(GUIDE.is_file(), f"Missing candidate guide: {GUIDE}")
        self.assertTrue(COVERAGE.is_file(), f"Missing candidate coverage: {COVERAGE}")
        with COVERAGE.open(encoding="utf-8-sig", newline="") as handle:
            rows = [row for row in csv.DictReader(handle)
                    if row.get("platform") == "samsung"]
        self.assertEqual(len(rows), 1, "Expected exactly one Samsung coverage row")
        report = rows[0]
        self.assertEqual(report["output"], "samsung.xml.gz")

        channel_ids = set()
        programme_counts = Counter()
        channel_count = 0
        programme_count = 0
        with gzip.open(GUIDE, "rb") as handle:
            for _, element in ET.iterparse(handle, events=("end",)):
                if element.tag == "channel":
                    channel_id = element.get("id")
                    self.assertTrue(channel_id, "Channel without id")
                    self.assertNotIn(channel_id, channel_ids,
                                     f"Duplicate XMLTV channel ID: {channel_id}")
                    self.assertTrue(element.findall("display-name"),
                                    f"Missing display-name: {channel_id}")
                    channel_ids.add(channel_id)
                    channel_count += 1
                    element.clear()
                elif element.tag == "programme":
                    programme_count += 1
                    channel_id = element.get("channel")
                    self.assertTrue(channel_id, "Programme without channel")
                    self.assertRegex(element.get("start", ""), XMLTV_DATE)
                    self.assertRegex(element.get("stop", ""), XMLTV_DATE)
                    programme_counts[channel_id] += 1
                    element.clear()

        self.assertGreater(channel_count, 0)
        self.assertGreater(programme_count, 0)
        self.assertFalse(set(programme_counts) - channel_ids,
                         "Programmes refer to unknown channel IDs")
        active_count = sum(programme_counts[channel_id] > 0 for channel_id in channel_ids)
        self.assertEqual(channel_count, int(report["channel_count"]),
                         "Committed Samsung channel count differs from report")
        self.assertEqual(active_count, int(report["active_channel_count"]),
                         "Committed Samsung active count differs from report")
        self.assertEqual(programme_count, int(report["programme_count"]),
                         "Committed Samsung programme count differs from report")


if __name__ == "__main__":
    unittest.main()
