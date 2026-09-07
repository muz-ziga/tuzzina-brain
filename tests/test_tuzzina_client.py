import io
import json
import os
import sys
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from tuzzina.client import TuzzinaClient, TuzzinaError

CALLS = []


class FakeResp:
    def __init__(self, payload, code=200):
        self.payload = payload
        self.code = code

    def read(self):
        return json.dumps(self.payload).encode()

    def __enter__(self):
        if self.code >= 400:
            raise urllib.error.HTTPError("u", self.code, "err", {}, None)
        return self

    def __exit__(self, *a):
        return False


def fake_urlopen(req, timeout=None):
    CALLS.append((req.full_url, req.get_method()))
    if req.full_url.endswith("/public/v1/upload-from-url"):
        return FakeResp({"id": "m1", "path": "https://pub.r2.dev/a.jpg"})
    if req.full_url.endswith("/public/v1/upload"):
        return FakeResp({"id": "m2", "path": "https://pub.r2.dev/b.png"})
    if req.full_url.endswith("/public/v1/posts") and \
            req.get_method() == "POST":
        return FakeResp({"ok": True})
    if req.full_url.endswith("/public/v1/integrations"):
        return FakeResp([{"id": "int1", "name": "My Page",
                          "providerIdentifier": "facebook"}])
    if req.full_url.endswith("/public/v1/posts"):
        return FakeResp([])
    return FakeResp({}, 404)


class ClientTest(unittest.TestCase):
    def setUp(self):
        CALLS.clear()
        self._orig = urllib.request.urlopen
        urllib.request.urlopen = fake_urlopen
        self.c = TuzzinaClient("http://x/api", "k")

    def tearDown(self):
        urllib.request.urlopen = self._orig

    def test_needs_key(self):
        with self.assertRaises(TuzzinaError):
            TuzzinaClient("http://x/api", "")

    def test_upload_from_url(self):
        r = self.c.upload_from_url("https://demo.test/i.jpg")
        self.assertEqual(r["id"], "m1")

    def test_upload_bytes(self):
        r = self.c.upload_bytes(b"123", "a.png", "image/png")
        self.assertEqual(r["id"], "m2")

    def test_create_draft(self):
        self.c.create_draft([], "2026-09-08T09:00:00+00:00")
        self.assertTrue(any("/public/v1/posts" in u for u, _ in CALLS))

    def test_find_integration(self):
        it = self.c.find_integration("facebook")
        self.assertEqual(it["id"], "int1")

    def test_find_missing(self):
        with self.assertRaises(TuzzinaError):
            self.c.find_integration("tiktok")

    def test_http_error_no_retry_duplicate(self):
        urllib.request.urlopen = lambda *a, **k: FakeResp({}, 400)
        with self.assertRaises(TuzzinaError):
            self.c.create_draft([], "2026-09-08T09:00:00+00:00")

    def test_connection_retry(self):
        n = {"i": 0}

        def flaky(*a, **k):
            n["i"] += 1
            if n["i"] < 2:
                raise ConnectionError("down")
            return FakeResp({"ok": True})
        urllib.request.urlopen = flaky
        c = TuzzinaClient("http://x/api", "k", retries=2)
        self.assertEqual(c.list_posts(), {"ok": True})
        self.assertEqual(n["i"], 2)


if __name__ == "__main__":
    unittest.main()
