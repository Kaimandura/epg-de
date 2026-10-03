import gzip
import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from test_release import guide
from quarantine_invalid_programmes import sanitize


class QuarantineTests(unittest.TestCase):
    def test_only_invalid_records_quarantined_with_full_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory)
            guide(base/'source.gz',[f'channel{i}' for i in range(100)])
            root=ET.fromstring(gzip.decompress((base/'source.gz').read_bytes()))
            valid=root.find('programme')
            ET.SubElement(ET.SubElement(valid,'credits'),'actor',role='lead').text='Retained actor'
            invalid=ET.SubElement(root,'programme',channel='channel0',start='20261003000000 +0000',stop='20261003000000 +0000')
            ET.SubElement(invalid,'desc').text='Original rejected detail'
            original=ET.tostring(root)
            source=base/'source.xml'
            source.write_bytes(original)
            result=sanitize(source,base/'out.xml')
            self.assertEqual(result['quarantined_programmes'],1)
            self.assertEqual(result['retained_programmes'],300)
            self.assertEqual(source.read_bytes(),original)
            self.assertIn(b'Retained actor',(base/'out.xml').read_bytes())
            rejected=json.loads((base/'out.rejected.jsonl').read_text())
            self.assertEqual(len(rejected['reasons']),2)
            self.assertIn('Original rejected detail',rejected['source_xml'])
            self.assertEqual(gzip.decompress((base/'out.xml.gz').read_bytes()),(base/'out.xml').read_bytes())

    def test_large_upstream_corruption_stops_release(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory)
            source=base/'bad.xml'
            source.write_text('<tv><channel id="a"/><programme channel="a" start="invalid"/></tv>')
            with self.assertRaisesRegex(ValueError,'corruption'):
                sanitize(source,base/'out.xml')
            self.assertIn('invalid',(base/'out.rejected.jsonl').read_text())
