import base64
import gzip
import hashlib
import json
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from audit_epg_quality import audit_guide, parse_xmltv_time
from enrich_epg_logos import (LogoCandidate, enrich_file, load_provider_logos,
                             legacy_pluto_logo,
                             magenta_logo_records, normalized_name,
                             programme_digest, unique_name_match, valid_logo_url, verify_logo)
from export_platform_epgs import refresh_provider_schedules


class QualityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.now = datetime(2026, 10, 8, 16, tzinfo=timezone.utc)

    def tearDown(self):
        self.temp.cleanup()

    def guide(self, cid="A.de", icon=None, start="20261008150000 +0000",
              stop="20261009150000 +0000"):
        root = ET.Element("tv", {"generator-info-name": "test"})
        channel = ET.SubElement(root, "channel", id=cid)
        ET.SubElement(channel, "display-name").text = "A"
        if icon:
            ET.SubElement(channel, "icon", src=icon)
        programme = ET.SubElement(root, "programme", channel=cid, start=start)
        if stop:
            programme.set("stop", stop)
        ET.SubElement(programme, "title", lang="de").text = "Über den Fluss"
        ET.SubElement(programme, "desc", lang="de").text = "Mehrzeilige Notiz\nZweite Zeile"
        path = self.root / "guide.xml"
        path.write_bytes(ET.tostring(root, xml_declaration=True, encoding="utf-8"))
        return path, root

    def test_strict_timestamp_rejects_trailing_text_and_missing_timezone(self):
        self.assertIsNone(parse_xmltv_time("20261008150000 +0000 garbage"))
        self.assertIsNone(parse_xmltv_time("20261008150000"))
        self.assertIsNone(parse_xmltv_time("20260230000000 +0000"))

    def test_dst_offsets_are_compared_as_utc_instants(self):
        path, root = self.guide(start="20261025023000 +0200", stop="20261025023000 +0100")
        result, issues, _ = audit_guide(path, datetime(2026, 10, 25, 1, tzinfo=timezone.utc))
        self.assertEqual(result["hard_errors"], 0)
        self.assertNotIn("non_positive_duration", [i["check"] for i in issues])

    def test_optional_stop_is_preserved_and_reported(self):
        path, _ = self.guide(stop=None)
        before = path.read_bytes()
        result, issues, _ = audit_guide(path, self.now)
        self.assertEqual(result["hard_errors"], 0)
        self.assertIn("missing_stop_time", [i["check"] for i in issues])
        self.assertEqual(before, path.read_bytes())

    def test_gzip_expiry_missing_logos_and_no_input_mutation(self):
        path, _ = self.guide(stop="20261008153000 +0000")
        archive = path.with_suffix(".xml.gz")
        archive.write_bytes(gzip.compress(path.read_bytes(), mtime=0))
        before = hashlib.sha256(archive.read_bytes()).hexdigest()
        result, issues, rows = audit_guide(archive, self.now)
        self.assertEqual(result["missing_icons"], 1)
        self.assertEqual(result["expired_channels"], 1)
        self.assertEqual(rows[0]["programmes"], 1)
        self.assertEqual(before, hashlib.sha256(archive.read_bytes()).hexdigest())
        self.assertEqual(result["qualification_status"], "blocked")

    def test_nested_overlaps_and_large_gaps_are_detected(self):
        path, root = self.guide()
        for start, stop, title in [("20261008160000 +0000", "20261008170000 +0000", "B"),
                                   ("20261009140000 +0000", "20261009160000 +0000", "C"),
                                   ("20261010000000 +0000", "20261010010000 +0000", "D")]:
            p = ET.SubElement(root, "programme", channel="A.de", start=start, stop=stop)
            ET.SubElement(p, "title").text = title
        path.write_bytes(ET.tostring(root))
        result, _, _ = audit_guide(path, self.now)
        self.assertEqual(result["schedule_overlaps"], 2)
        self.assertEqual(result["large_schedule_gaps"], 1)

    def test_image_presence_is_not_verified_coverage(self):
        path, _ = self.guide(icon="https://images.test/a.png")
        result, _, _ = audit_guide(path, self.now)
        self.assertEqual(result["logo_presence_percent"], 100)
        self.assertEqual(result["image_assets_verified_channels"], 0)
        self.assertEqual(result["qualification_status"], "blocked")

    def test_catalog_logo_conflict_is_reported_without_deleting(self):
        path, _ = self.guide(icon="https://images.test/b.png")
        before = path.read_bytes()
        _, issues, _ = audit_guide(path, self.now, logo_owners={"https://images.test/b.png": {"B.de"}},
                                  known_ids={"A.de", "B.de"})
        self.assertIn("logo_identity_conflict", [i["check"] for i in issues])
        self.assertEqual(path.read_bytes(), before)

    def test_plus_brand_and_ambiguous_alias_cannot_merge(self):
        self.assertNotEqual(normalized_name("RTL"), normalized_name("RTL+"))
        channel = ET.fromstring("<channel><display-name>News</display-name><display-name>A</display-name></channel>")
        self.assertIsNone(unique_name_match(channel, {"news": {"a", "b"}, "a": {"a"}}))

    def test_failed_asset_is_not_added(self):
        path, _ = self.guide()
        candidates = {("A.de", ""): [LogoCandidate("A.de", "", "https://images.test/a.png", 10, 10, "PNG")]}
        row = enrich_file(path, {"A.de"}, {}, candidates, probe=lambda u: {"status": "failed"})
        self.assertEqual(row["with_logo_after"], 0)
        self.assertIsNone(ET.parse(path).getroot().find("channel/icon"))

    def test_logo_enrichment_preserves_all_programme_metadata_and_gzip(self):
        path, _ = self.guide()
        before = programme_digest(path)
        candidate = LogoCandidate("A.de", "", "https://images.test/a.png", 10, 10, "PNG")
        evidence = []
        row = enrich_file(path, {"A.de"}, {}, {("A.de", ""): [candidate]},
                          evidence_rows=evidence, probe=lambda u: {"status": "passed"})
        self.assertEqual(row["with_logo_after"], 1)
        self.assertEqual(before, programme_digest(path))
        self.assertEqual(before, programme_digest(path.with_suffix(".xml.gz")))
        self.assertEqual(evidence[0]["matched_id"], "A.de")

    def test_native_rebranding_preserves_programmes_and_original_logo_evidence(self):
        old = "https://images.test/old-brand.png"
        path, _ = self.guide(icon=old)
        before = programme_digest(path)
        native = LogoCandidate("A.de", "", "https://images.test/current.png", 10, 10,
                               "PNG", "https://provider.test/channels#42", ("A",))
        evidence = []
        row = enrich_file(path, set(), {}, {}, {"A.de": native}, evidence_rows=evidence,
                          probe=lambda u: {"status": "passed"})
        self.assertEqual(row["logos_replaced_native"], 1)
        self.assertEqual(row["with_logo_after"], 1)
        self.assertEqual(ET.parse(path).getroot().find("channel/icon").get("src"), native.url)
        self.assertEqual(evidence[0]["previous_icons"], [old])
        self.assertEqual(programme_digest(path), before)
        self.assertEqual(programme_digest(path.with_suffix(".xml.gz")), before)

    def test_native_name_mismatch_cannot_replace_existing_logo(self):
        old = "https://images.test/old.png"
        path, _ = self.guide(icon=old)
        before = path.read_bytes()
        native = LogoCandidate("A.de", "", "https://images.test/new.png", 10, 10,
                               "PNG", "https://provider.test/channels#42", ("Different sender",))
        probe = unittest.mock.Mock(return_value={"status": "passed"})
        row = enrich_file(path, set(), {}, {}, {"A.de": native}, probe=probe)
        self.assertEqual(row["logos_replaced_native"], 0)
        self.assertEqual(path.read_bytes(), before)
        probe.assert_not_called()

    def test_failed_native_replacement_preserves_existing_asset(self):
        path, _ = self.guide(icon="https://images.test/old.png")
        before = path.read_bytes()
        native = LogoCandidate("A.de", "", "https://images.test/new.png", 10, 10,
                               "PNG", "https://provider.test/channels#42", ("A",))
        row = enrich_file(path, set(), {}, {}, {"A.de": native},
                          probe=lambda u: {"status": "failed"})
        self.assertEqual(row["logos_replaced_native"], 0)
        self.assertEqual(path.read_bytes(), before)

    def test_provider_ids_remain_region_specific_and_interleaved_are_supported(self):
        config = self.root / "platforms.json"
        config.write_text(json.dumps({"platforms": {"samsung": {"logo_sources": [
            {"namespace": "SamsungTVPlus", "region": "de", "url": "https://guide.test/de.xml.gz"}]}}}))
        payload = ('<tv><channel id="DE1"><display-name>A</display-name><icon src="https://img.test/a.png"/></channel>'
                   '<programme channel="DE1"/><channel id="DE2"><display-name>B</display-name>'
                   '<icon src="https://img.test/b.png"/></channel></tv>')
        (self.root / "SamsungTVPlus-de.xml.gz").write_bytes(gzip.compress(payload.encode()))
        logos, evidence = load_provider_logos(config, self.root)
        self.assertEqual(set(logos), {"SamsungTVPlus.de.DE1", "SamsungTVPlus.de.DE2"})
        self.assertNotIn("SamsungTVPlus.at.DE1", logos)
        self.assertEqual(evidence[0]["status"], "passed")

    def test_magenta_uses_exact_content_id_and_logo_type_not_programme_art(self):
        definitions = self.root / "channels.xml"
        definitions.write_text('<channels><channel xmltv_id="MagentaTV.de@MyTeamTVSport01" site="web.magentatv.de" site_id="550">Sport 1</channel></channels>')
        rows = [{"contentId": "551", "name": "Sport 1", "pictures": [{"imageType": "15", "href": "https://img.test/wrong.png"}]},
                {"contentId": "550", "name": "Sport 1", "pictures": [{"imageType": "14", "href": "https://img.test/art.png"},
                    {"imageType": "15", "href": "http://ngiss.t-online.de/logo.png"}]}]
        result = magenta_logo_records(rows, definitions)
        self.assertEqual(result["MagentaTV.de@MyTeamTVSport01"].url, "https://ngiss.t-online.de/logo.png")

    def test_legacy_pluto_requires_unique_us_provider_name_and_matching_id(self):
        channel = ET.fromstring('<channel id="Crime360.pluto"><display-name>USA: PLUTO - Crime 360</display-name></channel>')
        a = LogoCandidate("PlutoTV.us.1", "", "https://img.test/a.png", 0, 0, "PNG", names=("Crime 360",))
        self.assertIs(legacy_pluto_logo(channel, {a.channel_id: a}), a)
        b = LogoCandidate("PlutoTV.us.2", "", "https://img.test/b.png", 0, 0, "PNG", names=("Crime 360",))
        self.assertIsNone(legacy_pluto_logo(channel, {a.channel_id: a, b.channel_id: b}))
        channel.set("id", "Different.pluto")
        self.assertIsNone(legacy_pluto_logo(channel, {a.channel_id: a}))

    def test_http_html_is_not_a_verified_logo(self):
        class Response:
            headers = type("Headers", (), {"get_content_type": lambda s: "text/html"})()
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def geturl(self): return "https://img.test/a.png"
            def read(self, limit): return b"<html><body>Not found</body></html>"
        with patch("enrich_epg_logos.urllib.request.urlopen", return_value=Response()):
            self.assertEqual(verify_logo("https://img.test/a.png")["status"], "failed")
        class EmptySVG(Response):
            def read(self, limit): return b'<svg xmlns="http://www.w3.org/2000/svg"/>'
            headers = type("Headers", (), {"get_content_type": lambda s: "image/svg+xml"})()
        with patch("enrich_epg_logos.urllib.request.urlopen", return_value=EmptySVG()):
            self.assertEqual(verify_logo("https://img.test/a.svg")["status"], "failed")
        self.assertFalse(valid_logo_url("https://user:password@img.test/a.png"))
        self.assertFalse(valid_logo_url("http://img.test/a.png"))

    def test_native_refresh_preserves_full_alternatives_and_complementary_history(self):
        path, root = self.guide(cid="SamsungTVPlus.de.DE1", start="20261008150000 +0000", stop="20261008170000 +0000")
        historical = ET.SubElement(root, "programme", channel="SamsungTVPlus.de.DE1",
                                   start="20261008100000 +0000", stop="20261008110000 +0000")
        ET.SubElement(historical, "title").text = "Historical"
        payload = ('<tv><channel id="DE1"><display-name>A</display-name></channel>'
                   '<programme channel="DE1" start="20261008150000 +0000" stop="20261008180000 +0000">'
                   '<title>Correct</title><desc>Source metadata</desc></programme></tv>')
        (self.root / "SamsungTVPlus-de.xml.gz").write_bytes(gzip.compress(payload.encode()))
        config = {"samsung": {"logo_sources": [{"namespace": "SamsungTVPlus", "region": "de", "url": "https://guide.test/de.xml.gz"}]}}
        proof = self.root / "alternatives.jsonl"
        result = refresh_provider_schedules(root, config, self.root, proof, self.now)
        self.assertEqual(result["input_programmes"], result["retained_originals"] + result["preserved_alternatives"])
        self.assertEqual(result["preserved_alternatives"], 1)
        self.assertEqual(root.find("programme/title").text, "Historical")
        self.assertIn("Mehrzeilige Notiz", json.loads(proof.read_text())["original_xml"])
        self.assertEqual(root.findall("programme")[-1].findtext("desc"), "Source metadata")

    def test_stale_native_source_cannot_replace_valid_programmes(self):
        _, root = self.guide(cid="SamsungTVPlus.de.DE1")
        before = ET.tostring(root)
        payload = ('<tv><channel id="DE1"><display-name>A</display-name></channel>'
                   '<programme channel="DE1" start="20261008000000 +0000" stop="20261008010000 +0000"><title>Old</title></programme></tv>')
        (self.root / "SamsungTVPlus-de.xml.gz").write_bytes(gzip.compress(payload.encode()))
        config = {"samsung": {"logo_sources": [{"namespace": "SamsungTVPlus", "region": "de", "url": "https://guide.test/de.xml.gz"}]}}
        result = refresh_provider_schedules(root, config, self.root, self.root / "alternatives.jsonl", self.now)
        self.assertEqual(result["refreshed_channels"], 0)
        self.assertEqual(ET.tostring(root), before)


if __name__ == "__main__":
    unittest.main()
