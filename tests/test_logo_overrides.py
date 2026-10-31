import gzip
import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
import test_release
from enrich_epg_logos import enrich_file


class LogoOverrideTests(unittest.TestCase):
    def test_replace_dead_logo_preserve_other_channels_and_programmes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'de.xml'
            root = ET.Element('tv')
            ids = ['SkyCinemaPremieren.de@SD','SkyCinemaPremieren.de@HD',
                   'SkyCinemaPremieren.de@Plus24HD','ProSiebenFun.de@SD',
                   'ProSiebenMaxx.de@SD']
            for cid in ids:
                c = ET.SubElement(root,'channel',id=cid)
                ET.SubElement(c,'display-name').text=cid
                ET.SubElement(c,'icon',src='https://example.org/original.png')
            programme = ET.SubElement(root,'programme',channel=ids[0],start='20261031000000 +0000')
            ET.SubElement(programme,'title').text='Unchanged title'
            ET.SubElement(programme,'desc').text='Complete description'
            path.write_bytes(ET.tostring(root))
            overrides = json.loads((Path(__file__).resolve().parents[1]/'config/logo-overrides.json').read_text())
            result = enrich_file(path,set(),{}, {},overrides)
            updated = ET.parse(path).getroot()
            channels = {c.get('id'):c.find('icon').get('src') for c in updated.findall('channel')}
            self.assertEqual(channels[ids[0]],overrides['SkyCinemaPremieren.de'])
            self.assertEqual(channels[ids[1]],overrides['SkyCinemaPremieren.de'])
            self.assertEqual(channels[ids[2]],overrides[ids[2]])
            self.assertEqual(channels[ids[4]],'https://example.org/original.png')
            self.assertEqual(updated.find('programme').attrib,programme.attrib)
            self.assertEqual(updated.findtext('programme/desc'),'Complete description')
            self.assertEqual(result['logo_overrides_applied'],4)
            self.assertEqual(gzip.decompress(path.with_suffix('.xml.gz').read_bytes()),path.read_bytes())
            first=path.with_suffix('.xml.gz').read_bytes()
            self.assertEqual(enrich_file(path,set(),{}, {},overrides)['logo_overrides_applied'],0)
            self.assertEqual(first,path.with_suffix('.xml.gz').read_bytes())
