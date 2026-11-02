"""Tests for the nine-product read-only EPG quality inventory."""
import gzip
import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from audit_all_epg_products import PRODUCTS, audit, inspect


REQUIRED_DE_SKY = (
    "SkyCinemaBlockbuster.de@HD",
    "SkyCinemaBlockbuster.de@Plus24HD",
    "SkyCinemaFeelgood.de@HD",
    "SkyCinemaFeelgood.de@Plus24HD",
)


def with_required_de_sky(xml):
    """Keep synthetic DE-MASTER fixtures valid under the production identity gate."""
    root = ET.fromstring(xml)
    for cid in REQUIRED_DE_SKY:
        channel = ET.SubElement(root, "channel", id=cid)
        ET.SubElement(channel, "display-name").text = cid
        ET.SubElement(channel, "icon", src="https://example.org/sky.png")
        programme = ET.SubElement(
            root, "programme", channel=cid,
            start="20261101100000 +0000", stop="20261101110000 +0000",
        )
        ET.SubElement(programme, "title").text = "Fixture programme"
    return ET.tostring(root)


class AllEpgAuditTests(unittest.TestCase):
    def test_all_products_are_in_inventory(self):
        self.assertEqual(len(PRODUCTS), 9)
        self.assertEqual(len(set(PRODUCTS.values())), 9)

    def test_exact_pages_filenames_are_supported(self):
        xml = b'''<tv><channel id="station"><display-name>Station</display-name><icon src="https://example.org/station.png"/></channel><programme channel="station" start="20261101100000 +0000" stop="20261101110000 +0000"><title>Programme</title></programme></tv>'''
        with tempfile.TemporaryDirectory() as tmp:
            for label in PRODUCTS.values():
                if label == "DE-AMAZON":
                    continue
                with gzip.open(Path(tmp) / (label + ".xml.gz"), "wb") as stream:
                    stream.write(with_required_de_sky(xml) if label == "DE-MASTER" else xml)
            result = audit(Path(tmp), "pages")
            self.assertEqual(result["status"], "passed")
            self.assertEqual(len(result["products"]), 9)

    def test_known_usa_logo_debt_does_not_hide_new_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            valid = (b'<tv><channel id="one"><display-name>One</display-name>'
                     b'<icon src="https://assets.example.org/one.png"/></channel>'
                     b'<programme channel="one" start="20261101100000 +0000" '
                     b'stop="20261101110000 +0000"><title>News</title></programme></tv>')
            missing = (b'<tv><channel id="one"><display-name>One</display-name></channel>'
                       b'<programme channel="one" start="20261101100000 +0000" '
                       b'stop="20261101110000 +0000"><title>News</title></programme></tv>')
            for stem in PRODUCTS:
                if stem == "amazon":
                    continue
                with gzip.open(root / (stem + ".xml.gz"), "wb") as output:
                    output.write(missing if stem == "usa-local" else
                                 with_required_de_sky(valid) if stem == "de" else valid)
            policy = root / "debt.json"
            policy.write_text(json.dumps({"schema_version": 1, "max_missing_logos": {
                "USA-MASTER": 1, "USA-LOCAL": 1, "USA-FAST": 0, "USA-SPORTS": 0,
            }}), encoding="utf-8")
            self.assertEqual(audit(root)["status"], "failed")
            result = audit(root, usa_logo_debt=policy)
            self.assertEqual(result["status"], "passed")
            self.assertEqual(result["products"]["USA-LOCAL"]["status"], "logo_debt")
            self.assertEqual(result["products"]["USA-LOCAL"]["hard_issue_count"], 0)
            policy.write_text(json.dumps({"schema_version": 1, "max_missing_logos": {
                "USA-MASTER": 1, "USA-LOCAL": 0, "USA-FAST": 0, "USA-SPORTS": 0,
            }}), encoding="utf-8")
            self.assertEqual(audit(root, usa_logo_debt=policy)["status"], "failed")
            with gzip.open(root / "usa-local.xml.gz", "wb") as output:
                output.write(missing.replace(b'channel="one"', b'channel="orphan"'))
            policy.write_text(json.dumps({"schema_version": 1, "max_missing_logos": {
                "USA-MASTER": 1, "USA-LOCAL": 1, "USA-FAST": 0, "USA-SPORTS": 0,
            }}), encoding="utf-8")
            self.assertEqual(audit(root, usa_logo_debt=policy)["status"], "failed")

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
                stream.write(with_required_de_sky(xml))
            result = inspect(p)
            self.assertEqual({x["type"] for x in result["issues"]},
                             {"invalid_logo_url", "orphan_programme"})

    def test_missing_logo_is_release_blocking_with_channel_evidence(self):
        xml = b'''<tv><channel id="missing-logo"><display-name>Missing Logo</display-name></channel><programme channel="missing-logo" start="20261101100000 +0000" stop="20261101110000 +0000"><title>Title</title></programme></tv>'''
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "de.xml.gz"
            with gzip.open(p, "wb") as stream:
                stream.write(with_required_de_sky(xml))
            result = inspect(p)
            self.assertEqual(result["missing_logos"], 1)
            self.assertIn({"type": "missing_logo", "channel": "missing-logo"}, result["issues"])
            self.assertEqual(result["issue_count"], 1)

    def test_valid_feed_preserves_inputs(self):
        xml = b'''<tv><channel id="one"><display-name>One</display-name><icon src="https://example.org/logo.png"/></channel><programme channel="one" start="20261101100000 +0000" stop="20261101110000 +0000"><title>Title</title></programme></tv>'''
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "de.xml.gz"
            with gzip.open(p, "wb") as stream:
                stream.write(with_required_de_sky(xml))
            original = p.read_bytes()
            result = inspect(p)
            self.assertEqual(result["issue_count"], 0)
            self.assertEqual(p.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
