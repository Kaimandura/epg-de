import gzip
import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from test_release import guide
from release_policy import FILES, MAGENTA_REQUIRED, audit, identity_groups, inspect
from reconcile_release import reconcile, compare_lkg


class ReconciliationTests(unittest.TestCase):
    def test_owner_priority_metadata_and_accounting(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory)
            paths={}
            for label,stem in FILES['DE'].items():
                path=base/stem
                ids=MAGENTA_REQUIRED+['News'] if 'MAGENTA' in label else ['News'] if 'MASTER' in label or 'AMAZON' in label else [label]
                guide(path,ids)
                paths[label]=path
            # Source descriptions both survive a metadata merge in the same slot.
            for label,description in [('DE-MASTER.xml.gz','Long master detail'),('DE-MAGENTA.xml.gz','Provider detail')]:
                path=paths[label]
                root=ET.fromstring(gzip.decompress(path.read_bytes()))
                for node in root.findall('programme'):
                    if node.get('channel')=='News':
                        ET.SubElement(node,'desc').text=description
                path.write_bytes(gzip.compress(ET.tostring(root),mtime=0))
            report=reconcile(paths,base/'out','DE')
            self.assertEqual(report['status'],'passed')
            self.assertEqual(report['retired_empty_feeds'],['DE-AMAZON.xml.gz'])
            self.assertEqual(report['input_programmes'],report['unique_retained_programmes']+report['metadata_merged_records']+report['quarantined']+report['preserved_alternative_records'])
            data=gzip.decompress((base/'out/magenta.xml.gz').read_bytes())
            self.assertIn(b'Long master detail',data)
            self.assertIn(b'Provider detail',data)
            self.assertTrue(all(m['owner']=='DE-MAGENTA.xml.gz' for m in report['mapping'] if m['source_id']=='News'))
            canonical={label:base/'out'/stem for label,stem in FILES['DE'].items() if (base/'out'/stem).exists()}
            second=reconcile(canonical,base/'second','DE')
            self.assertEqual(report['output_sha256'],second['output_sha256'])

    def test_conflicting_secondary_interval_is_preserved_in_full(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory)
            paths={}
            for label,stem in FILES['USA'].items():
                path=base/stem
                guide(path,['shared',label] if label in ('USA-MASTER.xml.gz','USA-FAST.xml.gz') else [label])
                paths[label]=path
            path=paths['USA-FAST.xml.gz']
            root=ET.fromstring(gzip.decompress(path.read_bytes()))
            node=ET.SubElement(root,'programme',channel='shared',start='20260930003000 +0000',stop='20260930013000 +0000')
            ET.SubElement(node,'title').text='Alternative detail'
            ET.SubElement(node,'desc').text='Complete original description'
            path.write_bytes(gzip.compress(ET.tostring(root),mtime=0))
            # Equal counts prefer FAST lexically; give MASTER the longer owner schedule.
            owner=paths['USA-MASTER.xml.gz']
            ownroot=ET.fromstring(gzip.decompress(owner.read_bytes()))
            for hour in ('03','04'):
                p=ET.SubElement(ownroot,'programme',channel='shared',start=f'20260930{hour}0000 +0000',stop=f'20260930{int(hour)+1:02}0000 +0000')
                ET.SubElement(p,'title').text='Owner extra'
            owner.write_bytes(gzip.compress(ET.tostring(ownroot),mtime=0))
            report=reconcile(paths,base/'out','USA')
            self.assertEqual(report['preserved_alternative_records'],1)
            original=json.loads((base/'out/schedule-alternatives.jsonl').read_text())['source_xml']
            self.assertIn('Complete original description',original)
            self.assertIn('20260930003000 +0000',original)

    def test_lkg_rejects_normalized_output_loss(self):
        baseline={'outputs':{'USA-MASTER.xml.gz':{'channels':100,'programmes':1000}}}
        with self.assertRaisesRegex(ValueError,'LKG regression'):
            compare_lkg({'outputs':{'USA-MASTER.xml.gz':{'channels':100,'programmes':500}}},baseline,'USA')

    def test_affiliates_and_event_placeholders_do_not_prove_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'USA-LOCAL.xml.gz'
            guide(path,['WABC.us','KABC.us'],'ABC')
            self.assertEqual(len(identity_groups(inspect(path)[0])[0]),2)
            guide(path,['event1','event2'],'Live Event',['No event','No event','No event'])
            self.assertEqual(len(identity_groups(inspect(path)[0])[0]),2)

    def test_main_cannot_replace_required_region_feed(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'usa.xml.gz'
            guide(path,['ABC'])
            with self.assertRaisesRegex(ValueError,'missing'):
                reconcile({'USA-MASTER.xml.gz':path},Path(directory)/'out','USA')


if __name__=='__main__':
    unittest.main()
