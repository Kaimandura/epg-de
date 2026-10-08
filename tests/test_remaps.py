import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class SourceRemapTests(unittest.TestCase):
    def apply(self, directory, groups, rows):
        root = Path(directory)
        candidates, remaps = root/'candidates.json', root/'remaps.json'
        candidates.write_text(json.dumps({'channels': groups}))
        remaps.write_text(json.dumps({'remaps': rows}))
        result = subprocess.run([sys.executable, 'scripts/remap_candidates.py',
                                 '--candidates', str(candidates), '--remap-file', str(remaps),
                                 '--channels', str(root/'channels.xml')], text=True, capture_output=True,
                                cwd=Path(__file__).resolve().parents[1])
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(candidates.read_text())['channels']

    def candidate(self, cid, site, sid, name):
        return {'xmltv_id': cid, 'name': name,
                'attrs': {'xmltv_id': cid, 'site': site, 'site_id': sid}}

    def test_df1_remap_keeps_real_austrian_servus_source(self):
        old, target = 'ServusTV.at@SD', 'DF1.de@SD'
        groups = {old: [self.candidate(old, 'web.magentatv.de', '39', 'DF1'),
                        self.candidate(old, 'sky.com', 'AT#4913', 'ServusTV Österreich')]}
        with tempfile.TemporaryDirectory() as directory:
            output = self.apply(directory, groups, [{'site': 'web.magentatv.de', 'site_id': '39',
                                'from_xmltv_id': old, 'to_xmltv_id': target, 'name': 'DF1'}])
        self.assertEqual(output[old][0]['name'], 'ServusTV Österreich')
        self.assertEqual(output[target][0]['attrs']['site_id'], '39')
        self.assertEqual(sum(map(len, output.values())), 2)

    def test_renumbering_preserves_every_distinct_source_candidate(self):
        old1, old2 = 'SkySport1.de@SD', 'SkySport3.de@SD'
        groups = {old1: [self.candidate(old1, 'web.magentatv.de', '31', 'Sky Sport Top Event'),
                         self.candidate(old1, 'sky.com', 'DE#268', 'Sky Sport 1')],
                  old2: [self.candidate(old2, 'web.magentatv.de', '192', 'Sky Sport 1')]}
        rows = [{'site': 'web.magentatv.de', 'site_id': '31', 'from_xmltv_id': old1,
                 'to_xmltv_id': 'SkySportTopEvent.de@SD'},
                {'site': 'web.magentatv.de', 'site_id': '192', 'from_xmltv_id': old2,
                 'to_xmltv_id': old1}]
        with tempfile.TemporaryDirectory() as directory:
            output = self.apply(directory, groups, rows)
        sources = {(r['attrs']['site'],r['attrs']['site_id']) for bucket in output.values() for r in bucket}
        self.assertEqual(sources, {('web.magentatv.de','31'),('sky.com','DE#268'),('web.magentatv.de','192')})
        self.assertEqual(len(output[old1]), 2)
        self.assertNotIn(old2, output)
