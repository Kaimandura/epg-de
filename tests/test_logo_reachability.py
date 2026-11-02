"""Offline contract tests for the HTTPS logo response gate."""
import io
import urllib.error
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

    def test_rate_limited_image_is_retried_then_validated(self):
        url = "https://assets.example.org/logo.png"
        limited = urllib.error.HTTPError(url, 429, "Rate limited", {}, io.BytesIO())
        png = FakeResponse(b"\x89PNG\r\n\x1a\n" + bytes(64))
        with patch("urllib.request.urlopen", side_effect=[limited, png]) as opened, \
                patch("time.sleep") as sleep:
            result = check_url(url)
        self.assertTrue(result["ok"])
        self.assertEqual(opened.call_count, 2)
        sleep.assert_called_once()

    def test_rate_limit_honors_numeric_retry_after(self):
        url = "https://assets.example.org/logo.png"
        limited = urllib.error.HTTPError(url, 429, "Rate limited",
                                         {"Retry-After": "12"}, io.BytesIO())
        png = FakeResponse(bytes.fromhex("89504e470d0a1a0a") + bytes(64))
        with patch("urllib.request.urlopen", side_effect=[limited, png]), \
                patch("time.sleep") as sleep:
            result = check_url(url)
        self.assertTrue(result["ok"])
        sleep.assert_called_once_with(12.0)

    def test_wikimedia_403_retried_but_never_silenced(self):
        url = "https://upload.wikimedia.org/wikipedia/commons/ab.png"
        blocked = urllib.error.HTTPError(url, 403, "Forbidden", {}, io.BytesIO())
        with patch("urllib.request.urlopen", side_effect=blocked) as opened, \
                patch("time.sleep") as sleep:
            result = check_url(url, retries=2)
        self.assertEqual(result["http_status"], 403)
        self.assertEqual(result["reason"], "http_error")
        self.assertFalse(result["ok"])
        self.assertEqual(opened.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    def test_permanent_failure_includes_real_http_status(self):
        url = "https://assets.example.org/deleted-logo.png"
        missing = urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO())
        with patch("urllib.request.urlopen", side_effect=missing) as opened, \
                patch("time.sleep") as sleep:
            result = check_url(url)
        self.assertEqual(result["reason"], "http_error")
        self.assertEqual(result["http_status"], 404)
        self.assertFalse(result["ok"])
        opened.assert_called_once()
        sleep.assert_not_called()

    def test_downgrade_redirect_refused(self):
        with patch("urllib.request.urlopen", return_value=FakeResponse(
            b"\x89PNG\r\n\x1a\n", url="http://assets.example.org/logo.png")):
            self.assertEqual(check_url("https://assets.example.org/logo.png")["reason"],
                             "unsafe_redirect")


if __name__ == "__main__":
    unittest.main()
