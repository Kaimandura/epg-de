"""Offline integration test for non-destructive nine-product logo staging."""
import gzip
import json
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from audit_all_epg_products import PRODUCTS, OPTIONAL
from stage_snapshot_logo_repair import stage


class SnapshotLogoRepairTests(unittest.TestCase):
    def test_all_required_products_preserve_programmes_and_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            source.mkdir()
            xml = (b'<tv><channel id="Exact.de"><display-name>Exact TV</display-name>'
                   b'<icon src="https://example.org/outdated.png"/></channel>'
                   b'<channel id="Missing.de"><display-name>Missing TV</display-name></channel>'
                   b'<programme channel="Exact.de" start="20261101100000 +0000"'
                   b' stop="20261101110000 +0000"><title>News</title></programme>'
                   b'<programme channel="Missing.de" start="20261101100000 +0000"'
                   b' stop="20261101110000 +0000"><title>Arts</title></programme></tv>')
            originals = {}
            for stem in PRODUCTS:
                if stem in OPTIONAL:
                    continue
                path = source / (stem + ".xml.gz")
                with gzip.open(path, "wb") as handle:
                    handle.write(xml)
                originals[stem] = path.read_bytes()
            channels = root / "channels.csv"
            channels.write_text("id,name,alt_names,is_nsfw,closed\n"
                                "Exact.de,Exact TV,,, \nMissing.de,Missing TV,,, \n", encoding="utf-8")
            logos = root / "logos.csv"
            logos.write_text("channel,feed,url,format,width,height,in_use\n"
                             "Missing.de,,https://assets.example.org/missing.png,PNG,512,512,TRUE\n", encoding="utf-8")
            overrides = root / "overrides.json"
            overrides.write_text(json.dumps({"Exact.de": "https://assets.example.org/current.png"}), encoding="utf-8")
            output = root / "candidate"
            evidence = stage(source, output, channels, logos, overrides)
            self.assertEqual(set(evidence), set(PRODUCTS.values()) - {"DE-AMAZON"})
            for stem, data in originals.items():
                self.assertEqual((source / (stem + ".xml.gz")).read_bytes(), data)
                with gzip.open(output / (stem + ".xml.gz"), "rb") as f:
                    guide = ET.parse(f).getroot()
                urls = [node.find("icon").get("src") for node in guide.findall("channel")]
                self.assertEqual(urls, ["https://assets.example.org/current.png",
                                        "https://assets.example.org/missing.png"])
                self.assertEqual([p.findtext("title") for p in guide.findall("programme")],
                                 ["News", "Arts"])
            self.assertTrue((output / "logo-stage-evidence.json").is_file())


if __name__ == "__main__":
    unittest.main()
