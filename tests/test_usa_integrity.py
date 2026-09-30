import gzip
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from audit_usa_epgs import audit_xml


class USAIntegrityTests(unittest.TestCase):
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
