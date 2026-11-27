"""Logo deferral must never waive XMLTV or publication safety failures."""
import gzip
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from test_all_epg_products import with_required_de_sky
import audit_all_epg_products as products
import audit_logo_reachability as logos


class AdvisoryLogoTests(unittest.TestCase):
    def test_missing_logos_warn_but_orphans_and_missing_feeds_block(self):
        xml = b'<tv><channel id="a"><display-name>A</display-name></channel><programme channel="a" start="20261125090000 +0000" stop="20261125100000 +0000"><title>A</title></programme></tv>'
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for stem in products.PRODUCTS:
                with gzip.open(root / (stem + '.xml.gz'), 'wb') as out:
                    out.write(with_required_de_sky(xml) if stem == 'de' else xml)
            self.assertEqual(products.audit(root)['status'], 'failed')
            self.assertEqual(products.audit(root, allow_logo_gaps=True)['status'], 'passed_with_logo_warnings')
            with gzip.open(root / 'usa.xml.gz', 'wb') as out:
                out.write(xml.replace(b'programme channel="a"', b'programme channel="orphan"'))
            self.assertEqual(products.audit(root, allow_logo_gaps=True)['status'], 'failed')
            (root / 'usa.xml.gz').unlink()
            self.assertEqual(products.audit(root, allow_logo_gaps=True)['status'], 'failed')

    def test_live_image_faults_warn_but_unsafe_urls_and_missing_feeds_block(self):
        for reason, missing, blocks in [
            ('upstream_gateway_error', [], False),
            ('TimeoutError', [], False),
            ('http_error', [{'product': 'DE-MASTER', 'reason': 'missing_logo', 'channel': 'b'}], False),
            ('unsafe_redirect', [], True),
            ('non_https_or_credentials', [], True),
            ('http_error', [{'product': 'DE-MASTER', 'reason': 'missing_feed'}], True),
        ]:
            with self.subTest(reason=reason, missing=missing), tempfile.TemporaryDirectory() as tmp:
                report = Path(tmp) / 'report.json'
                usage = {'https://example.org/a.png': [{'product': 'DE-MASTER', 'channel': 'a'}]}
                with patch.object(sys, 'argv', ['audit', '--allow-logo-gaps', '--report', str(report)]), patch.object(logos, 'collect', return_value=(usage, missing)), patch.object(logos, 'check_url', return_value={'url': next(iter(usage)), 'ok': False, 'reason': reason}):
                    if blocks:
                        with self.assertRaises(SystemExit):
                            logos.main()
                    else:
                        logos.main()
                result = json.loads(report.read_text())
                self.assertEqual(result['status'], 'failed' if blocks else 'passed_with_logo_warnings')
                self.assertEqual(len(result['failed_urls']), 1)
