"""Release policy: broken USA logo URLs count against the same bounded debt as missing logos."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import audit_logo_reachability as audit


class UsaLogoDebtTests(unittest.TestCase):
    def run_audit(self, usage, missing, limit):
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "logo-audit.json"
            argv = ["audit_logo_reachability.py", "--directory", tmp, "--report", str(report),
                    "--usa-logo-debt", str(Path(tmp) / "policy.json"), "--workers", "1"]
            with patch.object(audit, "collect", return_value=(usage, missing)), \
                 patch.object(audit, "load_usa_logo_debt", return_value={"USA-MASTER": limit}), \
                 patch.object(audit, "check_url", side_effect=lambda url: {
                     "url": url, "ok": False, "reason": "not_image_bytes", "http_status": 200}), \
                 patch.object(sys, "argv", argv):
                try:
                    audit.main()
                    failed = False
                except SystemExit as exc:
                    self.assertEqual(exc.code, 1)
                    failed = True
            return failed, json.loads(report.read_text(encoding="utf-8"))

    def test_shared_broken_url_counts_each_channel(self):
        usage = {"https://example.org/broken.png": [
            {"product": "USA-MASTER", "channel": "First.us"},
            {"product": "USA-MASTER", "channel": "Second.us"}]}
        failed, report = self.run_audit(usage, [], 1)
        self.assertTrue(failed)
        self.assertEqual(report["deferred_usa"]["affected_channels"]["USA-MASTER"], 2)
        self.assertTrue(any(x["reason"] == "usa_logo_debt_increased"
                            for x in report["release_blockers"]["missing"]))

    def test_broken_logo_within_ceiling_is_documented(self):
        usage = {"https://example.org/broken.png": [
            {"product": "USA-MASTER", "channel": "First.us"}]}
        failed, report = self.run_audit(usage, [], 1)
        self.assertFalse(failed)
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["deferred_usa"]["affected_channels"]["USA-MASTER"], 1)

    def test_missing_and_broken_logos_share_ceiling(self):
        usage = {"https://example.org/broken.png": [
            {"product": "USA-MASTER", "channel": "First.us"}]}
        missing = [{"product": "USA-MASTER", "channel": "Second.us", "reason": "missing_logo"}]
        failed, report = self.run_audit(usage, missing, 1)
        self.assertTrue(failed)
        self.assertEqual(report["deferred_usa"]["affected_channels"]["USA-MASTER"], 2)

    def test_duplicate_failure_identity_counted_once(self):
        usage = {"https://example.org/broken.png": [
            {"product": "USA-MASTER", "channel": "First.us"},
            {"product": "USA-MASTER", "channel": "First.us"}]}
        failed, report = self.run_audit(usage, [], 1)
        self.assertFalse(failed)
        self.assertEqual(report["deferred_usa"]["affected_channels"]["USA-MASTER"], 1)


if __name__ == "__main__":
    unittest.main()
