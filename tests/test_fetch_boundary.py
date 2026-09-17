"""Stage B security boundary tests: every external fetch goes
through g1.fetch.safe_get. Fully offline: DNS resolver and
transport opener are substituted doubles. No network, no DB."""
import os
import socket
import sys
import unittest
import urllib.error

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import g1.fetch as F


class FakeResp:
    def __init__(self, body=b"", headers=None, url=""):
        self._body = body if isinstance(body, bytes) else \
            body.encode("utf-8")
        self.headers = headers or {}
        self._url = url

    def geturl(self):
        return self._url

    def read(self, n=-1):
        return self._body if n is None or n < 0 else self._body[:n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeOpener:
    routes = {}

    def open(self, req, timeout=None):
        route = self.routes[req.full_url]
        kind = route[0]
        if kind == "ok":
            _, body, ctype = route
            return FakeResp(body, {"Content-Type": ctype},
                            req.full_url)
        if kind == "redirect":
            _, location = route
            handler = F._HopCheckedRedirect()
            followed = handler.redirect_request(
                req, None, 302, "m", {}, location)
            if followed is None:
                raise F.FetchError("redirect-refused")
            return self.open(followed, timeout)
        raise route[1]


_REAL_GETADDRINFO = socket.getaddrinfo
_REAL_OPENER = F._opener


def _public_dns(host, port, *a, **k):
    return [(2, 1, 6, "", ("93.184.216.34", 0))]


def _priv_dns(host, port, *a, **k):
    return [(2, 1, 6, "", ("10.1.2.3", 0))]


def _mixed_dns(host, port, *a, **k):
    # Rebinding-style answer: one public, one private. Must refuse.
    return [(2, 1, 6, "", ("93.184.216.34", 0)),
            (2, 1, 6, "", ("192.168.7.7", 0))]


class BoundaryCase(unittest.TestCase):
    def setUp(self):
        F._opener = FakeOpener
        socket.getaddrinfo = _public_dns

    def tearDown(self):
        F._opener = _REAL_OPENER
        socket.getaddrinfo = _REAL_GETADDRINFO

    def _get(self, url, **kw):
        kw.setdefault("timeout", 5)
        kw.setdefault("max_bytes", 1000)
        kw.setdefault("user_agent", "t")
        return F.safe_get(url, **kw)

    def test_success_returns_body_final_url_ctype(self):
        FakeOpener.routes = {
            "http://feed.test/rss": (
                "ok", b"<rss/>", "application/rss+xml")}
        body, final, ctype = self._get("http://feed.test/rss")
        self.assertEqual(body, b"<rss/>")
        self.assertEqual(final, "http://feed.test/rss")
        self.assertIn("rss", ctype)

    def test_content_type_gate(self):
        FakeOpener.routes = {
            "http://feed.test/f": ("ok", b"x", "image/png")}
        with self.assertRaises(F.FetchError) as ctx:
            self._get("http://feed.test/f",
                      allowed_content_types=("html", "text"))
        self.assertEqual(str(ctx.exception), "unsupported-content-type")

    def test_no_gate_when_allowed_none(self):
        FakeOpener.routes = {
            "http://feed.test/f": ("ok", b"x", "image/png")}
        body, _, _ = self._get("http://feed.test/f",
                               allowed_content_types=None)
        self.assertEqual(body, b"x")

    def test_localhost_refused(self):
        for url in ("http://localhost/x", "http://127.0.0.1/x",
                    "http://[::1]/x", "http://0.0.0.0/x"):
            with self.assertRaises(F.FetchError, msg=url):
                self._get(url)

    def test_private_ipv4_refused(self):
        for url in ("http://10.0.0.9/x", "http://192.168.1.4/x",
                    "http://172.16.9.9/x", "http://169.254.169.254/x"):
            with self.assertRaises(F.FetchError, msg=url):
                self._get(url)

    def test_private_ipv6_refused(self):
        for url in ("http://[fd00::1]/x", "http://[fe80::1]/x"):
            with self.assertRaises(F.FetchError, msg=url):
                self._get(url)

    def test_private_via_dns_refused(self):
        socket.getaddrinfo = _priv_dns
        with self.assertRaises(F.FetchError) as ctx:
            self._get("http://evil.test/feed")
        self.assertEqual(str(ctx.exception), "non-public-target")

    def test_mixed_dns_refused(self):
        socket.getaddrinfo = _mixed_dns
        with self.assertRaises(F.FetchError) as ctx:
            self._get("http://flip.test/feed")
        self.assertEqual(str(ctx.exception), "non-public-target")

    def test_dns_failure(self):
        def _boom(host, port, *a, **k):
            raise socket.gaierror("nope")
        socket.getaddrinfo = _boom
        with self.assertRaises(F.FetchError) as ctx:
            self._get("http://gone.test/feed")
        self.assertEqual(str(ctx.exception), "dns-failed")

    def test_invalid_scheme(self):
        for url in ("file:///etc/passwd", "ftp://feed.test/x",
                    "javascript:alert(1)", "gopher://x/", "",
                    "http://user:pass@feed.test/x"):
            with self.assertRaises(F.FetchError, msg=url):
                self._get(url)

    def test_malformed_url(self):
        with self.assertRaises(F.FetchError):
            self._get("http://[::1")

    def test_redirect_to_private_refused(self):
        FakeOpener.routes = {
            "http://feed.test/r": ("redirect", "http://10.9.9.9/x"),
        }
        with self.assertRaises(F.FetchError) as ctx:
            self._get("http://feed.test/r")
        self.assertEqual(str(ctx.exception), "non-public-target")

    def test_redirect_public_ok(self):
        FakeOpener.routes = {
            "http://feed.test/r": ("redirect", "http://feed.test/f"),
            "http://feed.test/f": ("ok", b"body", "text/xml"),
        }
        body, final, _ = self._get("http://feed.test/r")
        self.assertEqual(body, b"body")
        self.assertEqual(final, "http://feed.test/f")

    def test_redirect_loop_refused(self):
        FakeOpener.routes = {
            "http://feed.test/loop": (
                "redirect", "http://feed.test/loop"),
        }
        with self.assertRaises(F.FetchError) as ctx:
            self._get("http://feed.test/loop")
        self.assertEqual(str(ctx.exception), "too-many-redirects")

    def test_http_error_mapped(self):
        class Boom:
            def open(self, req, timeout=None):
                raise urllib.error.HTTPError(
                    req.full_url, 500, "e", {}, None)
        F._opener = Boom
        with self.assertRaises(F.FetchError) as ctx:
            self._get("http://feed.test/x")
        self.assertEqual(str(ctx.exception), "http-500")

    def test_timeout_mapped(self):
        class Boom:
            def open(self, req, timeout=None):
                raise TimeoutError("slow")
        F._opener = Boom
        with self.assertRaises(F.FetchError) as ctx:
            self._get("http://feed.test/x")
        self.assertEqual(str(ctx.exception), "timeout")

    def test_oversized_refused(self):
        FakeOpener.routes = {
            "http://feed.test/big": ("ok", b"x" * 2000, "text/xml")}
        with self.assertRaises(F.FetchError) as ctx:
            self._get("http://feed.test/big", max_bytes=100)
        self.assertEqual(str(ctx.exception), "oversized")


class WebsiteParityTest(unittest.TestCase):
    def setUp(self):
        F._opener = FakeOpener
        socket.getaddrinfo = _public_dns

    def tearDown(self):
        F._opener = _REAL_OPENER
        socket.getaddrinfo = _REAL_GETADDRINFO

    def test_website_refuses_private_without_connect(self):
        import g1.website as W
        opened = []
        real_open = FakeOpener.open

        def spy(inner_self, req, timeout=None):
            opened.append(req.full_url)
            return real_open(inner_self, req, timeout)
        FakeOpener.open = spy
        try:
            body, final = W._fetch("http://127.0.0.1/x")
            self.assertEqual((body, final), ("", ""))
            self.assertEqual(opened, [])
        finally:
            FakeOpener.open = real_open

    def test_website_mixed_dns_refused(self):
        import g1.website as W
        socket.getaddrinfo = _mixed_dns
        body, final = W._fetch("http://flip.test/page")
        self.assertEqual((body, final), ("", ""))


if __name__ == "__main__":
    unittest.main()
