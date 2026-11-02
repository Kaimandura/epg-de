"""Ensure logo repair queue reports exact product/channel identity."""
import csv
import gzip
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class LogoRepairQueueTests(unittest.TestCase):
    def test_missing_logo_yields_channel_scoped_queue(self):
        xml = b'<tv><channel id="MissingLogo.de"><display-name>Missing Logo</display-name></channel></tv>'
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            (folder / "epg").mkdir()
            with gzip.open(folder / "epg" / "de.xml.gz", "wb") as handle:
                handle.write(xml)
            report = folder / "audit.json"
            queue = folder / "queue.csv"
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "audit_logo_reachability.py"),
                 "--directory", str(folder / "epg"), "--report", str(report),
                 "--gap-csv", str(queue), "--workers", "1"],
                capture_output=True, text=True, timeout=30)
            self.assertNotEqual(result.returncode, 0)
            with queue.open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertTrue(any(row["product"] == "DE-MASTER"
                                and row["channel"] == "MissingLogo.de"
                                and row["reason"] == "missing_logo" for row in rows))


if __name__ == "__main__":
    unittest.main()
