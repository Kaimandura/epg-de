import json
import tempfile
import unittest
import urllib.error
import urllib.parse
from pathlib import Path
import test_release  # adds the scripts directory
from verify_deployed_release import verify_public


class PublicReleaseTests(unittest.TestCase):
    def test_exact_bytes_and_removed_feeds(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)
            (source/'release.json').write_text(json.dumps({'producers':{'DE':{'run_id':1}}}))
            (source/'DE-MASTER.xml.gz').write_bytes(b'exact compressed bytes')
            def fetch(url):
                name=urllib.parse.urlparse(url).path.rsplit('/',1)[1]
                path=source/name
                if path.exists():
                    return path.read_bytes()
                raise urllib.error.HTTPError(url,404,'missing',{},None)
            self.assertEqual(verify_public(source,'https://example.org/epg/',fetch)['status'],'passed')
            def corrupted(url):
                return b'corrupt' if 'DE-MASTER' in url else fetch(url)
            with self.assertRaisesRegex(ValueError,'public bytes differ'):
                verify_public(source,'https://example.org/epg/',corrupted)
            def stale(url):
                return b'old external output' if 'USA-SOURCE' in url else fetch(url)
            with self.assertRaisesRegex(ValueError,'retired product'):
                verify_public(source,'https://example.org/epg/',stale)
