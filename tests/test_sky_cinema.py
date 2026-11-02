import csv
import gzip
import json
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from enrich_epg_logos import enrich_file

class CinemaTests(unittest.TestCase):
    def run_collection(self, changed=False):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            site = tmp / 'sites/sky.com'
            site.mkdir(parents=True)
            rules = json.loads((ROOT / 'config/channel-overrides.json').read_text())
            rules['channels'] = [r for r in rules['channels'] if r.get('site_id') in {'DE#330','DE#331','DE#332','DE#333'}]
            self.assertEqual(len(rules['channels']), 4)
            channels = ET.Element('channels')
            for rule in rules['channels']:
                # Real upstream source rows have empty XMLTV IDs.
                node = ET.SubElement(channels, 'channel', site='sky.com', site_id=rule['site_id'], lang='de', xmltv_id='')
                node.text = 'Unexpected station' if changed else rule['expected_source_name']
            ET.ElementTree(channels).write(site / 'sky.com.channels.xml')
            (tmp / 'feeds.csv').write_text('channel,id,broadcast_area\n')
            (tmp / 'rules.json').write_text(json.dumps(rules))
            result = subprocess.run([sys.executable, str(ROOT / 'scripts/collect_channels.py'), '--epg-root', str(tmp), '--feeds-csv', str(tmp/'feeds.csv'), '--override-file', str(tmp/'rules.json'), '--candidates', str(tmp/'candidates.json'), '--channels', str(tmp/'channels.xml')], capture_output=True, text=True)
            if changed:
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('identity changed', result.stderr)
                return
            self.assertEqual(result.returncode, 0, result.stderr)
            actual = json.loads((tmp/'candidates.json').read_text())['channels']
            self.assertEqual(set(actual), {'SkyCinemaBlockbuster.de@HD','SkyCinemaFeelgood.de@HD','SkyCinemaBlockbuster.de@Plus24HD','SkyCinemaFeelgood.de@Plus24HD'})
            for rule in rules['channels']:
                candidate = actual[rule['xmltv_id']][0]
                self.assertEqual(candidate['attrs']['site_id'], rule['site_id'])
                self.assertEqual(candidate['name'], rule['name'])

    def test_empty_ids_are_collected(self):
        self.run_collection()

    def test_changed_source_identity_is_rejected(self):
        self.run_collection(changed=True)

    def test_logos_use_exact_plus24_and_preserve_programmes(self):
        overrides = json.loads((ROOT/'config/logo-overrides.json').read_text())
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'guide.xml'
            root=ET.Element('tv')
            ids=['SkyCinemaBlockbuster.de@HD','SkyCinemaFeelgood.de@HD','SkyCinemaBlockbuster.de@Plus24HD','SkyCinemaFeelgood.de@Plus24HD']
            for cid in ids:
                ch=ET.SubElement(root,'channel',id=cid)
                ET.SubElement(ch,'icon',src='https://example.org/dead.png')
            # Empty schedule: no invented programme fixtures enter production.
            p.write_bytes(ET.tostring(root))
            enrich_file(p,set(),{}, {},overrides)
            output=ET.parse(p).getroot()
            for ch in output.findall('channel'):
                cid=ch.get('id')
                self.assertEqual(ch.find('icon').get('src'), overrides.get(cid,overrides[cid.split('@')[0]]))
                self.assertEqual('plus24' in ch.find('icon').get('src'), 'Plus24' in cid)
            self.assertEqual(output.findall('programme'),[])
            self.assertEqual(gzip.decompress(p.with_suffix('.xml.gz').read_bytes()),p.read_bytes())

    def test_de_master_audit_rejects_missing_required_channels(self):
        from audit_all_epg_products import inspect
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "DE-MASTER.xml.gz"
            root = ET.Element("tv")
            ch = ET.SubElement(root, "channel", id="Other.de")
            ET.SubElement(ch, "display-name").text = "Other"
            ET.SubElement(ch, "icon", src="https://example.org/other.png")
            p = ET.SubElement(root, "programme", channel="Other.de",
                              start="20261102160000 +0100", stop="20261102170000 +0100")
            ET.SubElement(p, "title").text = "Real title"
            with gzip.open(path, "wb") as stream:
                stream.write(ET.tostring(root))
            issues = inspect(path)["issues"]
            missing = [item["channel"] for item in issues if item["type"] == "missing_required_channel"]
            self.assertEqual(len(missing), 4)

    def test_producer_requires_all_four_active_sky_feeds(self):
        workflow=(ROOT/".github/workflows/update-epg.yml").read_text()
        for brand in ("Feelgood","Blockbuster"):
            for feed in ("HD","Plus24HD"):
                self.assertIn("--required-active-channel SkyCinema"+brand+".de@"+feed, workflow)
