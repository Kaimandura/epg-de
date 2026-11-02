"""Regression guard: Sky source identities must never be silently reassigned."""
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class SkyIdentityIntegrityTests(unittest.TestCase):
    def test_sky_remaps_are_source_pinned_and_named(self):
        rules = json.loads((ROOT / "config/channel-remaps.json").read_text())["remaps"]
        sky = [r for r in rules if r["to_xmltv_id"].startswith("Sky")]
        self.assertTrue(sky)
        seen = set()
        for r in sky:
            self.assertEqual(r["site"], "web.magentatv.de")
            self.assertTrue(r["site_id"])
            self.assertEqual(r["name"], r["expected_source_name"])
            self.assertTrue(r["to_xmltv_id"].endswith(".de@SD"))
            key = (r["site"], r["site_id"])
            self.assertNotIn(key, seen)
            seen.add(key)

    def test_four_sky_cinema_channels_are_explicitly_mapped(self):
        rules = json.loads((ROOT / "config/channel-overrides.json").read_text())["channels"]
        expected = {
            "SkyCinemaBlockbuster.de@HD": "DE#330",
            "SkyCinemaFeelgood.de@HD": "DE#331",
            "SkyCinemaBlockbuster.de@Plus24HD": "DE#332",
            "SkyCinemaFeelgood.de@Plus24HD": "DE#333",
        }
        for channel, site_id in expected.items():
            matches = [r for r in rules if r["xmltv_id"] == channel]
            self.assertEqual(len(matches), 1, channel)
            self.assertEqual(matches[0]["site"], "sky.com")
            self.assertEqual(matches[0]["site_id"], site_id)
            self.assertEqual(matches[0]["name"], matches[0]["expected_source_name"])

if __name__ == "__main__":
    unittest.main()
