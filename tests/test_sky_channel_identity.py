"""Regression tests for source-specific Sky channel identity remapping."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REMAPS = ROOT / "config" / "channel-remaps.json"


class SkyIdentityRemapTests(unittest.TestCase):
    def test_sky_remaps_are_unique_and_identity_consistent(self):
        rows = json.loads(REMAPS.read_text(encoding="utf-8"))["remaps"]
        sky = [r for r in rows if r["site"] == "web.magentatv.de" and r["from_xmltv_id"].startswith("SkySport")]
        self.assertEqual(len(sky), 11)
        self.assertEqual(len({(r["site"], r["site_id"]) for r in sky}), len(sky))
        expected = {
            "31": ("SkySportTopEvent.de@SD", "Sky Sport Top Event"),
            "198": ("SkySportTennis.de@SD", "Sky Sport Tennis"),
            "192": ("SkySport1.de@SD", "Sky Sport 1"),
            "212": ("SkySport2.de@SD", "Sky Sport 2"),
            "189": ("SkySport3.de@SD", "Sky Sport 3"),
            "111": ("SkySport4.de@SD", "Sky Sport 4"),
            "114": ("SkySport5.de@SD", "Sky Sport 5"),
            "113": ("SkySport6.de@SD", "Sky Sport 6"),
            "127": ("SkySport7.de@SD", "Sky Sport 7"),
            "261": ("SkySport8.de@SD", "Sky Sport 8"),
            "187": ("SkySport9.de@SD", "Sky Sport 9"),
        }
        self.assertEqual({r["site_id"]: (r["to_xmltv_id"], r["name"]) for r in sky}, expected)

    def test_mismatched_provider_name_fails_closed(self):
        rows = json.loads(REMAPS.read_text(encoding="utf-8"))["remaps"]
        rule = next(r for r in rows if r.get("expected_source_name") == "Sky Sport 8")
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            source = folder / "candidates.json"
            rules = folder / "rules.json"
            output = folder / "channels.xml"
            bad = {"attrs": {"site": rule["site"], "site_id": rule["site_id"],
                             "xmltv_id": rule["from_xmltv_id"]},
                   "name": "Sky Sport 10", "reasons": []}
            source.write_text(json.dumps({"channels": {rule["from_xmltv_id"]: [bad]}}),
                              encoding="utf-8")
            rules.write_text(json.dumps({"remaps": [rule]}), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "remap_candidates.py"),
                 "--candidates", str(source), "--remap-file", str(rules),
                 "--channels", str(output)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Source identity changed", result.stderr)
            self.assertFalse(output.exists())

    def test_remap_moves_only_matching_source(self):
        rows = json.loads(REMAPS.read_text(encoding="utf-8"))["remaps"]
        sky = next(r for r in rows if r["site_id"] == "261" and r["site"] == "web.magentatv.de")
        source = sky["from_xmltv_id"]
        good = {"attrs": {"site": sky["site"], "site_id": sky["site_id"], "xmltv_id": source}, "name": "Sky Sport 8", "reasons": []}
        other = {"attrs": {"site": "sky.com", "site_id": "DE#269", "xmltv_id": source}, "name": "Sky Sport 10 HD", "reasons": []}
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            candidates = tmp / "candidates.json"
            rules = tmp / "remaps.json"
            channels = tmp / "channels.xml"
            candidates.write_text(json.dumps({"channels": {source: [good, other]}}), encoding="utf-8")
            rules.write_text(json.dumps({"remaps": [sky]}), encoding="utf-8")
            subprocess.run([sys.executable, str(ROOT / "scripts" / "remap_candidates.py"),
                            "--candidates", str(candidates), "--remap-file", str(rules),
                            "--channels", str(channels)], check=True, capture_output=True)
            result = json.loads(candidates.read_text(encoding="utf-8"))["channels"]
            self.assertEqual(result[source][0]["attrs"]["site"], "sky.com")
            self.assertEqual(result[sky["to_xmltv_id"]][0]["attrs"]["site_id"], "261")
            self.assertEqual(result[sky["to_xmltv_id"]][0]["name"], "Sky Sport 8")


if __name__ == "__main__":
    unittest.main()
