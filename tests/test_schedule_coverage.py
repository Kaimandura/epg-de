import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from validate_epg import channels_below_future_horizon, has_contiguous_coverage


class ScheduleCoverageTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 24, 10, 0, tzinfo=timezone.utc)

    def test_future_horizon_requires_each_channel_to_reach_cutoff(self):
        schedules = {
            "long": [(self.now, self.now + timedelta(hours=5))],
            "short": [(self.now, self.now + timedelta(hours=3))],
            "missing": [],
        }
        short = channels_below_future_horizon(
            set(schedules), schedules, self.now + timedelta(hours=4))
        self.assertEqual(short, ["missing", "short"])

    def test_contiguous_coverage_includes_current_programme_and_next_day(self):
        intervals = [
            (self.now - timedelta(hours=1), self.now + timedelta(hours=1)),
            (self.now + timedelta(hours=1), self.now + timedelta(hours=25)),
        ]
        self.assertTrue(has_contiguous_coverage(intervals, self.now, 24))

    def test_contiguous_coverage_rejects_missing_current_programme_or_gap(self):
        gap = [
            (self.now - timedelta(hours=1), self.now + timedelta(hours=1)),
            (self.now + timedelta(hours=2), self.now + timedelta(hours=26)),
        ]
        future_only = [(self.now + timedelta(minutes=1), self.now + timedelta(hours=25))]
        self.assertFalse(has_contiguous_coverage(gap, self.now, 24))
        self.assertFalse(has_contiguous_coverage(future_only, self.now, 24))


if __name__ == "__main__":
    unittest.main()
