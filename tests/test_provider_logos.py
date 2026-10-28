import gzip
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from enrich_epg_logos import enrich_file, load_samsung_logos


class ProviderLogoTests(unittest.TestCase):
    def test_exact_region_ids_only_and_programmes_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            provider = base/'provider.xml'
            provider.write_text('<tv><channel id="ABC"><icon src="https://provider.test/logo.png"/></channel></tv>')
            logos = load_samsung_logos([f'de={provider}'])
            target = base/'guide.xml'
            target.write_text('''<tv>
              <channel id="SamsungTVPlus.de.ABC"><display-name>News</display-name></channel>
              <channel id="SamsungTVPlus.at.ABC"><display-name>News</display-name></channel>
              <channel id="unrelated"><display-name>News</display-name></channel>
              <programme channel="SamsungTVPlus.de.ABC" start="20261019000000 +0000" stop="20261019010000 +0000">
                <title lang="de">Original title</title><desc lang="de">Full original description</desc>
              </programme></tv>''')
            before = ET.parse(target).getroot()
            row = enrich_file(target, set(), {}, {}, logos)
            after = ET.parse(target).getroot()
            self.assertEqual(row['logos_added_provider'], 1)
            self.assertEqual([c.get('id') for c in before.findall('channel')],
                             [c.get('id') for c in after.findall('channel')])
            self.assertIsNone(after.findall('channel')[1].find('icon'))
            self.assertIsNone(after.findall('channel')[2].find('icon'))
            old, new = before.find('programme'), after.find('programme')
            self.assertEqual(old.attrib, new.attrib)
            self.assertEqual([(c.tag, c.attrib, c.text) for c in old],
                             [(c.tag, c.attrib, c.text) for c in new])
            self.assertEqual(gzip.decompress(target.with_suffix('.xml.gz').read_bytes()), target.read_bytes())
            row = enrich_file(target, set(), {}, {}, {'SamsungTVPlus.de.ABC': 'https://other.test/replacement.png'})
            self.assertEqual(row['logos_added_provider'], 0)
            self.assertEqual(ET.parse(target).find('channel/icon').get('src'), 'https://provider.test/logo.png')

    def test_ambiguous_and_non_https_icons_are_not_invented(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'source.xml'
            path.write_text('''<tv>
              <channel id="a"><icon src="https://provider.test/a"/><icon src="https://provider.test/b"/></channel>
              <channel id="b"><icon src="http://provider.test/b"/></channel>
              <channel id="c"/>
            </tv>''')
            self.assertEqual(load_samsung_logos([f'de={path}']), {})

    def test_duplicate_or_colliding_ids_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'source.xml'
            for ids in (('same', 'same'), ('a/b', 'a?b')):
                path.write_text(f'<tv><channel id="{ids[0]}"/><channel id="{ids[1]}"/></tv>')
                with self.assertRaisesRegex(ValueError, 'duplicate provider'):
                    load_samsung_logos([f'de={path}'])
            with self.assertRaisesRegex(ValueError, 'Samsung guide must'):
                load_samsung_logos([f'us={path}'])


if __name__ == '__main__':
    unittest.main()
