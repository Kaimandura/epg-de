import gzip
import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
import test_release
from enrich_epg_logos import LogoCandidate, enrich_file


class LogoOverrideTests(unittest.TestCase):
    def test_dead_iptv_epg_logo_uses_an_alternate_database_logo(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "usa.xml"
            root = ET.Element("tv")
            channel = ET.SubElement(root, "channel", id="CoziTV.us")
            ET.SubElement(channel, "display-name").text = "Cozi TV"
            ET.SubElement(channel, "icon", src="https://iptv-epg.org/images/dead")
            path.write_bytes(ET.tostring(root))
            candidate = "https://raw.githubusercontent.com/logos/cozi-tv.png"
            logos = {
                ("CoziTV.us", ""): [
                    LogoCandidate("CoziTV.us", "", candidate, 500, 500, "PNG")
                ]
            }
            result = enrich_file(path, {"CoziTV.us"}, {}, logos)
            updated = ET.parse(path).getroot()
        self.assertEqual(updated.find("channel/icon").get("src"), candidate)
        self.assertEqual(result["logos_replaced_unreliable_source"], 1)

    def test_dead_iptv_epg_logo_is_kept_when_no_alternate_exists(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "usa.xml"
            root = ET.Element("tv")
            channel = ET.SubElement(root, "channel", id="CoziTV.us")
            ET.SubElement(channel, "display-name").text = "Cozi TV"
            source = "https://iptv-epg.org/images/dead"
            ET.SubElement(channel, "icon", src=source)
            path.write_bytes(ET.tostring(root))
            logos = {
                ("CoziTV.us", ""): [
                    LogoCandidate(
                        "CoziTV.us", "", "https://cdn.iptv-epg.org/images/alternate",
                        500, 500, "PNG")
                ]
            }
            result = enrich_file(path, {"CoziTV.us"}, {}, logos)
            updated = ET.parse(path).getroot()
        self.assertEqual(updated.find("channel/icon").get("src"), source)
        self.assertEqual(result["logos_replaced_unreliable_source"], 0)

    def test_verified_usa_replacements_override_dead_source_logos(self):
        root = Path(__file__).resolve().parents[1]
        overrides = json.loads((root / "config/logo-overrides.json").read_text())
        expected = {
            "BETPlutoTV.us@US": "https://images.pluto.tv/channels/5ff768b6a4c8b80008498610/colorLogoPNG.png",
            "PickleballTV.us@HD": "https://pickleballtv.com/assets/pbtv-logo.svg",
            "PlutoTVAnime.us@US": "https://images.pluto.tv/channels/5f12136385bccc00070142ed/colorLogoPNG.png",
        }
        self.assertEqual({channel: overrides[channel] for channel in expected}, expected)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "usa-fast.xml"
            tree = ET.Element("tv")
            for channel_id in expected:
                channel = ET.SubElement(tree, "channel", id=channel_id)
                ET.SubElement(channel, "display-name").text = channel_id
                ET.SubElement(channel, "icon", src="https://invalid.example/dead.png")
            path.write_bytes(ET.tostring(tree))
            result = enrich_file(path, set(), {}, {}, overrides)
            channels = {c.get("id"): c.find("icon").get("src")
                        for c in ET.parse(path).getroot().findall("channel")}
        self.assertEqual(channels, expected)
        self.assertEqual(result["logo_overrides_applied"], len(expected))

    def test_replace_dead_logo_preserve_other_channels_and_programmes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'de.xml'
            root = ET.Element('tv')
            ids = ['SkyCinemaPremieren.de@SD','SkyCinemaPremieren.de@HD',
                   'SkyCinemaPremieren.de@Plus24HD','ProSiebenFun.de@SD',
                   'ProSieben.de@SD','SkyCinemaPremieren.de@Plus24','ProSiebenMaxx.de@SD']
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
            self.assertEqual(channels[ids[5]],overrides[ids[5]])
            self.assertEqual(channels[ids[6]],overrides['ProSiebenMaxx.de'])
            self.assertEqual(result['logo_overrides_applied'],6)
            self.assertEqual(gzip.decompress(path.with_suffix('.xml.gz').read_bytes()),path.read_bytes())
            first=path.with_suffix('.xml.gz').read_bytes()
            self.assertEqual(enrich_file(path,set(),{}, {},overrides)['logo_overrides_applied'],0)
            self.assertEqual(first,path.with_suffix('.xml.gz').read_bytes())
