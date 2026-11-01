"""Offline contract tests for the HTTPS logo response gate."""
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from audit_logo_reachability import check_url


class FakeResponse:
    status = 200
    headers = {"Content-Type": "image/png"}
    def __init__(self, data, url="https://assets.example.org/logo.png"):
        self.data = data
        self.url = url
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def geturl(self): return self.url
    def read(self, length): return self.data[:length]


class LogoReachabilityTests(unittest.TestCase):
    def test_http_and_userinfo_refused_without_network(self):
        with patch("urllib.request.urlopen") as opened:
            self.assertFalse(check_url("http://assets.example.org/logo.png")["ok"])
            self.assertFalse(check_url("https://user:pass@assets.example.org/logo.png")["ok"])
            opened.assert_not_called()

    def test_real_png_signature_accepted(self):
        with patch("urllib.request.urlopen", return_value=FakeResponse(
            b"\x89PNG\r\n\x1a\n" + bytes(64))):
            self.assertTrue(check_url("https://assets.example.org/logo.png")["ok"])

    def test_fake_html_rejected_even_with_image_header(self):
        with patch("urllib.request.urlopen", return_value=FakeResponse(b"<html>not image</html>")):
            self.assertFalse(check_url("https://assets.example.org/logo.png")["ok"])

    def test_downgrade_redirect_refused(self):
        with patch("urllib.request.urlopen", return_value=FakeResponse(
            b"\x89PNG\r\n\x1a\n", url="http://assets.example.org/logo.png")):
            self.assertEqual(check_url("https://assets.example.org/logo.png")["reason"],
                             "unsafe_redirect")


if __name__ == "__main__":
    unittest.main()
