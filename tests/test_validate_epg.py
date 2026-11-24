import sys
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from validate_epg import covers_future_window, has_current_programme, programme_interval


class ProgrammeTimeValidationTests(unittest.TestCase):
    def test_programme_interval_uses_timezone_aware_instants(self):
        item = ET.Element(
            "programme",
            start="20260228090000 +0200",
            stop="20260228080000 +0000",
        )
        start, stop = programme_interval(item)
        self.assertEqual(start, datetime(2026, 2, 28, 7, tzinfo=timezone.utc))
        self.assertEqual(stop, datetime(2026, 2, 28, 8, tzinfo=timezone.utc))

    def test_programme_interval_rejects_impossible_dates_and_bad_durations(self):
        for attrs in [
            {"start": "20260230090000 +0000"},
            {"start": "20260228090000 +0000", "stop": "20260230093000 +0000"},
            {"start": "20260228090000 +0000", "stop": "20260228090000 +0000"},
            {"start": "20260228090000 +0000", "stop": "20260228085959 +0000"},
        ]:
            with self.subTest(attrs=attrs), self.assertRaises(ValueError):
                programme_interval(ET.Element("programme", attrs))

    def test_empty_stop_is_optional(self):
        self.assertEqual(
            programme_interval(ET.Element("programme", start="20240229090000 +0000")),
            (datetime(2024, 2, 29, 9, tzinfo=timezone.utc), None),
        )

    def test_current_coverage_includes_start_but_excludes_stop(self):
        now = datetime(2026, 11, 24, 8, tzinfo=timezone.utc)
        intervals = [
            (datetime(2026, 11, 24, 7, tzinfo=timezone.utc),
             datetime(2026, 11, 24, 8, tzinfo=timezone.utc)),
            (now, datetime(2026, 11, 24, 9, tzinfo=timezone.utc)),
            (datetime(2026, 11, 24, 9, tzinfo=timezone.utc), None),
        ]
        self.assertTrue(has_current_programme(intervals, now))
        self.assertFalse(has_current_programme(intervals, datetime(2026, 11, 24, 9, tzinfo=timezone.utc)))

    def test_future_coverage_requires_a_continuous_window(self):
        now = datetime(2026, 11, 24, 8, tzinfo=timezone.utc)
        first = (datetime(2026, 11, 24, 7, tzinfo=timezone.utc),
                 datetime(2026, 11, 24, 12, tzinfo=timezone.utc))
        second = (datetime(2026, 11, 24, 12, tzinfo=timezone.utc),
                  datetime(2026, 11, 25, 9, tzinfo=timezone.utc))
        self.assertTrue(covers_future_window([second, first], now, 24))
        self.assertFalse(covers_future_window([first], now, 24))
        gap = (datetime(2026, 11, 24, 12, 1, tzinfo=timezone.utc),
               datetime(2026, 11, 25, 9, tzinfo=timezone.utc))
        self.assertFalse(covers_future_window([first, gap], now, 24))


if __name__ == "__main__":
    unittest.main()
