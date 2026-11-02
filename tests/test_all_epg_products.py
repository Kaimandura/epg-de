"""Tests for the nine-product read-only EPG quality inventory."""
import gzip
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from audit_all_epg_products import PRODUCTS, audit, inspect


class AllEpgAuditTests(unittest.TestCase):
    def test_all_products_are_in_inventory(self):
        self.assertEqual(len(PRODUCTS), 9)
        self.assertEqual(len(set(PRODUCTS.values())), 9)

    def test_missing_required_product_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = audit(Path(tmp))
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["products"]["DE-MASTER"]["status"], "missing")
            self.assertEqual(result["products"]["DE-AMAZON"]["status"], "optional_absent")

    def test_detects_wrong_logo_and_orphan_programme(self):
        xml = b'''<tv><channel id="one"><display-name>One</display-name><icon src="http://invalid.test/logo.png"/></channel><programme channel="missing" start="20261101100000 +0000" stop="20261101110000 +0000"><title>Title</title></programme></tv>'''
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "de.xml.gz"
            with gzip.open(p, "wb") as stream:
                stream.write(xml)
            result = inspect(p)
            self.assertEqual({x["type"] for x in result["issues"]},
                             {"invalid_logo_url", "orphan_programme"})

    def test_missing_logo_is_release_blocking_with_channel_evidence(self):
        xml = b'''<tv><channel id="missing-logo"><display-name>Missing Logo</display-name></channel><programme channel="missing-logo" start="20261101100000 +0000" stop="20261101110000 +0000"><title>Title</title></programme></tv>'''
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "de.xml.gz"
            with gzip.open(p, "wb") as stream:
                stream.write(xml)
            result = inspect(p)
            self.assertEqual(result["missing_logos"], 1)
            self.assertIn({"type": "missing_logo", "channel": "missing-logo"}, result["issues"])
            self.assertEqual(result["issue_count"], 1)

    def test_valid_feed_preserves_inputs(self):
        xml = b'''<tv><channel id="one"><display-name>One</display-name><icon src="https://example.org/logo.png"/></channel><programme channel="one" start="20261101100000 +0000" stop="20261101110000 +0000"><title>Title</title></programme></tv>'''
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "de.xml.gz"
            with gzip.open(p, "wb") as stream:
                stream.write(xml)
            original = p.read_bytes()
            result = inspect(p)
            self.assertEqual(result["issue_count"], 0)
            self.assertEqual(p.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
