"""Regressions for excluding synthetic upstream XMLTV identities."""
import unittest
from scripts.build_us_epgs import is_prohibited_source_identity


class ProhibitedIdentityTests(unittest.TestCase):
    def test_known_bad_upstream_namespace(self):
        for identifier in (
            "AdultSection.bossdummy",
            "CheckFixtures.bossdummy",
            "CheckTelegramFixturesChannel.bossdummy",
            "ClubberTV.bossdummy",
            "SkySports+.bossdummy",
            "StanSport.bossdummy",
            "StreamingOnThisService.bossdummy",
        ):
            with self.subTest(identifier=identifier):
                self.assertTrue(is_prohibited_source_identity(identifier))

    def test_real_sender_ids_not_filtered(self):
        for identifier in ("KABC.us", "WABC.us", "SkySports.us", "ClubberTV.us"):
            with self.subTest(identifier=identifier):
                self.assertFalse(is_prohibited_source_identity(identifier))


if __name__ == "__main__":
    unittest.main()
