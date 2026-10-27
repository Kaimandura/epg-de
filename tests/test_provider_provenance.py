"""Regression checks for provider isolation and pseudo channel IDs."""
import sys
import tempfile
import unittest
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from export_amazon_epg import choose_source
from release_policy import inspect


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
