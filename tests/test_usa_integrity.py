import gzip
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from audit_usa_epgs import audit_xml
from normalize_tivimate_xmltv import clean_programme, normalize
import xml.etree.ElementTree as ET
import json


class USAIntegrityTests(unittest.TestCase):
    def test_normalizer_rejects_invalid_duration_with_original_record(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'usa.xml'
            path.write_text('<tv><channel id="a"><display-name>A</display-name></channel>'
                            '<programme channel="a" start="20260930000000 +0000" stop="20260930010000 +0000"><title>Valid</title></programme>'
                            '<programme channel="a" start="20260930010000 +0000" stop="20260930010000 +0000"><title>Invalid</title></programme></tv>')
            self.assertEqual(normalize(path), (1, 1, 1))
            rejected = json.loads(path.with_suffix('.xml.rejected.jsonl').read_text())
            self.assertIn('Invalid', rejected['source_xml'])
            self.assertEqual(audit_xml(path)[2], [])

    def test_optional_xmltv_stop_is_preserved(self):
        node = ET.fromstring('<programme channel="a" start="20260930000000 +0000"><title>A</title></programme>')
        self.assertIsNotNone(clean_programme(node))

    def test_invalid_calendar_and_nonpositive_duration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'usa.xml'
            for start, stop, expected in [
                ('20260930000000 +0000', '20260930000000 +0000', 'non-positive'),
                ('20260930010000 +0000', '20260930000000 +0000', 'non-positive'),
                ('20260230010000 +0000', '20260301010000 +0000', 'start date'),
                ('20260930000000 +0000', '20261301000000 +0000', 'stop date'),
            ]:
                path.write_text(f'<tv><channel id="a"><display-name>A</display-name></channel>'
                                f'<programme channel="a" start="{start}" stop="{stop}"><title>Test</title></programme></tv>')
                self.assertTrue(any(expected in e for e in audit_xml(path)[2]))


if __name__ == '__main__':
    unittest.main()
