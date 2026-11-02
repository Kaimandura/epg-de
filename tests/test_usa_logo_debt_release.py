"""Release contract: USA logo debt is advisory, DE and security defects are not."""
import gzip
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from audit_all_epg_products import PRODUCTS
from audit_logo_reachability import main as run_logo_audit


class UsaLogoDebtReleaseTests(unittest.TestCase):
    def test_usa_404_is_reported_but_de_404_and_unsafe_redirect_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            directory = root / "epg"
            directory.mkdir()
            for stem in PRODUCTS:
                if stem == "amazon":
                    continue
                url = ("https://logos.example.org/usa.png" if stem.startswith("usa")
                       else "https://logos.example.org/de.png")
                xml = (f'<tv><channel id="{stem}"><display-name>{stem}</display-name>'
                       f'<icon src="{url}"/></channel><programme channel="{stem}" '
                       'start="20261101100000 +0000" stop="20261101110000 +0000">'
                       '<title>Program</title></programme></tv>').encode()
                with gzip.open(directory / (stem + ".xml.gz"), "wb") as output:
                    output.write(xml)
            policy = root / "policy.json"
            policy.write_text(json.dumps({"schema_version": 1, "max_missing_logos": {
                "USA-MASTER": 1, "USA-LOCAL": 257,
                "USA-FAST": 77, "USA-SPORTS": 1,
            }}), encoding="utf-8")
            report = root / "audit.json"
            argv = ["audit_logo_reachability.py", "--directory", str(directory),
                    "--report", str(report), "--usa-logo-debt", str(policy)]
            def check(url):
                return ({"url": url, "ok": False, "reason": "http_error", "http_status": 404}
                        if url.endswith("/usa.png")
                        else {"url": url, "ok": True, "http_status": 200, "content_type": "image/png"})
            with patch.object(sys, "argv", argv), patch("audit_logo_reachability.check_url", side_effect=check):
                run_logo_audit()
            result = json.loads(report.read_text())
            self.assertEqual(result["status"], "passed")
            self.assertEqual(len(result["deferred_usa"]["failed_urls"]), 1)
            self.assertEqual(result["release_blockers"]["failed_urls"], [])

            with patch.object(sys, "argv", argv), patch("audit_logo_reachability.check_url",
                 side_effect=lambda url: {"url": url, "ok": False, "reason": "http_error", "http_status": 404}):
                with self.assertRaises(SystemExit):
                    run_logo_audit()
            self.assertEqual(json.loads(report.read_text())["status"], "failed")

            with patch.object(sys, "argv", argv), patch("audit_logo_reachability.check_url",
                 side_effect=lambda url: {"url": url, "ok": False, "reason": "unsafe_redirect"}):
                with self.assertRaises(SystemExit):
                    run_logo_audit()
            self.assertEqual(json.loads(report.read_text())["status"], "failed")


if __name__ == "__main__":
    unittest.main()
