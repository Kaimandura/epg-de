"""Regression checks for provider isolation and pseudo channel IDs."""
import sys
import gzip
import tempfile
import unittest
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from export_amazon_epg import choose_source
from release_policy import inspect
from build_us_epgs import is_pseudo_channel_id, collect_channels, collect_schedule_stats
from datetime import datetime, timezone


class ProviderProvenanceTests(unittest.TestCase):
    def test_foreign_provider_explicit_id_rejected(self):
        source = "PlutoTV.de.21-jump-street"
        channels = {source: ET.Element("channel", id=source)}
        programmes = {source: [ET.Element("programme", channel=source)]}
        self.assertEqual(choose_source({"source_ids": [source]}, channels, programmes), (None, 0))

    def test_foreign_provider_name_fallback_rejected(self):
        source = "SamsungTVPlus.de.all-romance"
        channel = ET.Element("channel", id=source)
        ET.SubElement(channel, "display-name").text = "All Romance"
        self.assertEqual(choose_source({"name": "All Romance"}, {source: channel},
                                       {source: [ET.Element("programme")]}), (None, 0))

    def test_upstream_pseudo_id_rejected(self):
        self.assertTrue(is_pseudo_channel_id("AdultSection.bossdummy"))
        self.assertTrue(is_pseudo_channel_id("SkySports+.BOSSDUMMY"))
        self.assertFalse(is_pseudo_channel_id("WABC.us"))

    def test_source_collection_excludes_pseudo_without_dropping_real_channel(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.xml.gz"
            with gzip.open(path, "wb") as stream:
                stream.write(
                    b'<tv><channel id="AdultSection.bossdummy"><display-name>Dummy</display-name></channel>'
                    b'<channel id="WABC.us"><display-name>WABC</display-name></channel>'
                    b'<programme channel="AdultSection.bossdummy" start="20261027100000 +0000" '
                    b'stop="20261027110000 +0000"><title>Fake</title></programme></tv>'
                )
            channels, categories = collect_channels(
                path, {}, {}, set(), [], [], []
            )
            self.assertNotIn("AdultSection.bossdummy", channels)
            self.assertIn("WABC.us", channels)
            self.assertNotIn("AdultSection.bossdummy", categories)

    def test_pseudo_id_fails_release_audit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "usa-local.xml"
            path.write_text(
                '<tv><channel id="SkySports+.bossdummy"><display-name>Sky Sports</display-name></channel>'
                '<programme channel="SkySports+.bossdummy" start="20261027100000 +0000" '
                'stop="20261027110000 +0000"><title>Sports</title></programme></tv>',
                encoding="utf-8")
            _channels, errors, _counts = inspect(path, "USA-LOCAL.xml.gz")
            self.assertTrue(any("prohibited pseudo channel ID" in error for error in errors), errors)


if __name__ == "__main__":
    unittest.main()
