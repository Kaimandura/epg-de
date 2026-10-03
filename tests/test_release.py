import gzip
import hashlib
import io
import json
import sys
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from release_policy import FILES, MAGENTA_REQUIRED, audit, inspect
from release_bundle import seal, verify
from stage_release import extract_bundle, select_run, stage


def guide(path, ids, name=None, titles=None, bad_ref=False, inactive=False):
    root = ET.Element("tv")
    for cid in ids:
        c = ET.SubElement(root, "channel", id=cid)
        ET.SubElement(c, "display-name").text = name or cid
    if not inactive:
        for cid in ids:
            for i in range(3):
                start = datetime(2026, 9, 30, tzinfo=timezone.utc) + timedelta(hours=i)
                p = ET.SubElement(root, "programme", channel="unknown" if bad_ref else cid,
                                  start=start.strftime("%Y%m%d%H%M%S %z"),
                                  stop=(start + timedelta(hours=1)).strftime("%Y%m%d%H%M%S %z"))
                ET.SubElement(p, "title").text = (titles or ["A", "B", "C"])[i]
    data = ET.tostring(root)
    with path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as stream:
            stream.write(data)


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.provenance = {"repository": "owner/repo", "source_sha": "a" * 40,
                           "run_id": 1, "run_attempt": 1, "policy_sha256": "b" * 64, 'test_only':False}

    def tearDown(self):
        self.temp.cleanup()

    def files(self, specifications):
        paths = {}
        for label, ids, name in specifications:
            path = self.root / label
            guide(path, ids, name)
            paths[label] = path
        return paths

    def test_owner_plus_master_allowed(self):
        paths = self.files([("DE-MASTER.xml.gz", ["News"], "News"),
                            ("DE-MAGENTA.xml.gz", MAGENTA_REQUIRED + ["News"], None)])
        self.assertEqual(audit(paths, [])['status'], 'passed')

    def test_third_occurrence_fails(self):
        paths = self.files([("DE-MASTER.xml.gz", ["News"], "News"),
                            ("DE-SAMSUNG.xml.gz", ["News"], "News"),
                            ("DE-PLUTO.xml.gz", ["News"], "News")])
        self.assertTrue(audit(paths, [])['identity_violations'])

    def test_two_owners_without_master_fail(self):
        paths = self.files([("DE-SAMSUNG.xml.gz", ["first"], "News HD"),
                            ("DE-PLUTO.xml.gz", ["second"], "News [Pluto TV]")])
        self.assertTrue(audit(paths, [])['identity_violations'])

    def test_aliases_with_different_names_fail(self):
        paths = self.files([("USA-FAST.xml.gz", ["a"], "Foo"),
                            ("USA-LOCAL.xml.gz", ["b"], "Bar")])
        result = audit(paths, [{"region": "USA", "ids": ["a", "b"]}])
        self.assertTrue(result['identity_violations'])

    def test_name_and_times_without_same_titles_do_not_merge(self):
        paths = self.files([("USA-LOCAL.xml.gz", ["a"], "ABC")])
        other = self.root / "USA-FAST.xml.gz"
        guide(other, ["b"], "ABC", ["D", "E", "F"])
        paths[other.name] = other
        self.assertEqual(audit(paths, [])['status'], 'passed')

    def test_regional_and_language_variants_survive(self):
        paths = self.files([("DE-MASTER.xml.gz", ["Feed@German", "Feed@French"], "Feed")])
        self.assertEqual(audit(paths, [])['status'], 'passed')

    def test_generic_alias_cannot_bridge_two_regions(self):
        paths = self.files([("DE-MASTER.xml.gz", ["Feed@German", "Feed", "Feed@French"], "Feed")])
        from release_policy import identity_groups
        channels = inspect(next(iter(paths.values())))[0]
        groups, _ = identity_groups(channels)
        self.assertEqual(sorted(len(g) for g in groups), [1, 2])

    def test_master_owner_different_programme_content_rejected(self):
        paths = self.files([("DE-MASTER.xml.gz", ["News"], "News"),
                            ("DE-PLUTO.xml.gz", ["News"], "News")])
        guide(paths['DE-PLUTO.xml.gz'], ['News'], 'News', ['D', 'E', 'F'])
        self.assertTrue(any('mismatch' in e for e in audit(paths, [])['errors']))

    def test_deterministic_gzip_and_existing_lkg_gate(self):
        from cleanup_published_epgs import write_xml_and_gzip
        path = self.root / 'candidate.xml'
        archive = self.root / 'candidate.xml.gz'
        guide(archive, ['a'])
        root = ET.fromstring(gzip.decompress(archive.read_bytes()))
        write_xml_and_gzip(root, path)
        original = archive.read_bytes()
        write_xml_and_gzip(root, path)
        self.assertEqual(original, archive.read_bytes())
        baseline = self.root / 'baseline.xml'
        baseline.write_bytes(path.read_bytes())
        root.remove(root.findall('programme')[-1])
        root.remove(root.findall('programme')[-1])
        write_xml_and_gzip(root, path)
        script = Path(__file__).resolve().parents[1] / 'scripts/validate_epg.py'
        result = subprocess.run([sys.executable, str(script), str(path), '--gzip', str(archive),
                                 '--min-channels', '1', '--min-programmes', '1', '--baseline', str(baseline),
                                 '--min-baseline-programme-ratio', '0.9', '--skip-pre-validation-cleanup'],
                                text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('baseline', (result.stdout + result.stderr).lower())

    def test_quality_variants_are_same_identity(self):
        paths = self.files([("DE-MASTER.xml.gz", ["Feed@HD", "Feed@SD"], "Feed")])
        self.assertTrue(audit(paths, [])['identity_violations'])

    def test_wrong_provider_namespace_fails(self):
        paths = self.files([("DE-PLUTO.xml.gz", ["SamsungTVPlus.de.123"], "News")])
        self.assertTrue(audit(paths, [])['errors'])

    def test_magenta_21_required_with_programmes(self):
        paths = self.files([("DE-MAGENTA.xml.gz", MAGENTA_REQUIRED, None)])
        self.assertEqual(audit(paths, [])['status'], 'passed')
        guide(paths['DE-MAGENTA.xml.gz'], MAGENTA_REQUIRED[:-1])
        self.assertTrue(audit(paths, [])['errors'])

    def test_bad_reference_and_inactive_fail(self):
        path = self.root / 'USA-FAST.xml.gz'
        guide(path, ['a'], bad_ref=True)
        self.assertTrue(inspect(path)[1])
        guide(path, ['a'], inactive=True)
        self.assertTrue(inspect(path)[1])

    def test_negative_duration_fails(self):
        path = self.root / 'USA-FAST.xml.gz'
        guide(path, ['a'])
        root = ET.fromstring(gzip.decompress(path.read_bytes()))
        p = root.find('programme')
        p.set('stop', p.get('start'))
        path.write_bytes(gzip.compress(ET.tostring(root)))
        self.assertTrue(any('non-positive' in e for e in inspect(path)[1]))

    def bundle(self, region='USA', run_id=1):
        source = self.root / ('source-' + region)
        source.mkdir()
        for name, stem in FILES[region].items():
            guide(source / stem, MAGENTA_REQUIRED if name == 'DE-MAGENTA.xml.gz' else [name])
        output = self.root / ('bundle-' + region)
        provenance = {**self.provenance, 'run_id': run_id}
        seal(source, output, region, provenance, [])
        return output, provenance

    def test_bundle_hash_and_provenance(self):
        bundle, provenance = self.bundle()
        self.assertEqual(verify(bundle, 'USA', provenance)['region'], 'USA')
        with self.assertRaisesRegex(ValueError, 'provenance'):
            verify(bundle, 'USA', {**provenance, 'run_id': 99})
        (bundle / 'USA-FAST.xml.gz').write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            verify(bundle, 'USA', provenance)

    def test_external_feed_in_bundle_rejected(self):
        bundle, provenance = self.bundle()
        (bundle / 'USA-SOURCE-EXTERNAL.xml.gz').write_bytes(b'external')
        with self.assertRaisesRegex(ValueError, 'entries'):
            verify(bundle, 'USA', provenance)

    def test_zip_traversal_duplicate_and_missing_rejected(self):
        for names in [['../escape'], ['manifest.json', 'manifest.json'], ['audit.json']]:
            data = io.BytesIO()
            with zipfile.ZipFile(data, 'w') as archive:
                for name in names:
                    archive.writestr(name, '{}')
            with self.assertRaises(ValueError):
                extract_bundle(data.getvalue(), self.root / 'extract', 'USA')

    def run_record(self, region, run_id):
        return {'id': run_id, 'run_attempt': 1, 'head_sha': 'a' * 40,
                'status': 'completed', 'conclusion': 'success', 'event': 'schedule',
                'head_branch': 'main', 'repository': {'full_name': 'owner/repo'},
                'head_repository': {'full_name': 'owner/repo'},
                'path': '.github/workflows/' + ('update-epg.yml' if region == 'DE' else 'update-usa-epg.yml')}

    def test_reject_untrusted_producers(self):
        run = self.run_record('DE', 1)
        for change in [{'head_branch': 'feature'}, {'conclusion': 'failure'}, {'event': 'pull_request'},
                       {'head_repository': {'full_name': 'attacker/fork'}}, {'path': '.github/workflows/other.yml'}]:
            with self.assertRaises(ValueError):
                select_run([{**run, **change}], 'owner/repo', 'update-epg.yml')
        self.assertEqual(select_run([run, {**run, 'id': 2}], 'owner/repo', 'update-epg.yml')['id'], 2)

    def test_multi_producer_staging_exact_bytes_and_lkg_on_failure(self):
        responses = {}
        originals = {}
        for region, run_id, workflow in [('DE', 1, 'update-epg.yml'), ('USA', 2, 'update-usa-epg.yml')]:
            bundle, _ = self.bundle(region, run_id)
            data = io.BytesIO()
            with zipfile.ZipFile(data, 'w') as archive:
                for path in bundle.iterdir():
                    archive.writestr(path.name, path.read_bytes())
                    if path.name.endswith('.gz'):
                        originals[path.name] = path.read_bytes()
            raw = data.getvalue()
            responses[f'/actions/workflows/{workflow}/runs?branch=main&status=success&per_page=100'] = {'workflow_runs': [self.run_record(region, run_id)]}
            responses[f'/actions/runs/{run_id}/artifacts?per_page=100'] = {'artifacts': [
                {'id': run_id, 'name': f'epg-{region.lower()}-{run_id}-1', 'expired': False,
                 'digest': 'sha256:' + hashlib.sha256(raw).hexdigest()}]}
            responses[f'/actions/artifacts/{run_id}/zip'] = raw
        class FakeAPI:
            def get(self, path, binary=False):
                return responses[path]
        pages = self.root / 'pages'
        stage(FakeAPI(), 'owner/repo', pages, self.root / 'downloads', 'b' * 64)
        for name, data in originals.items():
            self.assertEqual((pages / name).read_bytes(), data)
        before = {p.name: p.read_bytes() for p in pages.iterdir()}
        responses['/actions/artifacts/2/zip'] += b'tampered'
        with self.assertRaisesRegex(ValueError, 'digest'):
            stage(FakeAPI(), 'owner/repo', self.root / 'next', self.root / 'next-downloads', 'b' * 64)
        self.assertFalse((self.root / 'next').exists())
        self.assertEqual(before, {p.name: p.read_bytes() for p in pages.iterdir()})


if __name__ == '__main__':
    unittest.main()
