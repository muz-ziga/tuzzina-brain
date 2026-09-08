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
        return FakeResp([
            {"id": "int1", "name": "Juzzir Facebook",
             "identifier": "facebook", "picture": "x", "disabled": False},
            {"id": "int2", "name": "Muzziga",
             "identifier": "facebook", "picture": "y", "disabled": False},
        ])
    if req.full_url.endswith("/public/v1/posts"):
        return FakeResp([])
    if "/public/v1/integration-settings/" in req.full_url:
        return FakeResp({"output": {
            "rules": "Facebook posts can be text only.",
            "maxLength": 63206,
            "settings": {"type": "object",
                         "properties": {"post_type": {"type": "string"}}},
            "tools": [{"description": "t", "methodName": "m",
                       "dataSchema": []}],
        }})
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

    def test_integrations_returns_list(self):
        items = self.c.integrations()
        self.assertIsInstance(items, list)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["id"], "int1")
        self.assertEqual(items[1]["id"], "int2")

    def test_get_integration_by_id(self):
        it = self.c.get_integration("int2")
        self.assertEqual(it["id"], "int2")
        self.assertEqual(it["name"], "Muzziga")

    def test_get_integration_missing_raises(self):
        with self.assertRaises(TuzzinaError):
            self.c.get_integration("nonexistent")

    def test_get_integration_empty_id_raises(self):
        with self.assertRaises(TuzzinaError):
            self.c.get_integration("")

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
            return FakeResp([])
        urllib.request.urlopen = flaky
        c = TuzzinaClient("http://x/api", "k", retries=2)
        self.assertEqual(c.integrations(), [])
        self.assertEqual(n["i"], 2)

    def test_settings_exact_path_and_id(self):
        self.c.get_integration_settings("int9")
        self.assertIn(
            ("http://x/api/public/v1/integration-settings/int9", "GET"),
            CALLS)

    def test_settings_read_only_single_get(self):
        self.c.get_integration_settings("int9")
        self.assertEqual(len(CALLS), 1)
        self.assertEqual(CALLS[0][1], "GET")

    def test_settings_returned_unchanged(self):
        r = self.c.get_integration_settings("int9")
        self.assertEqual(r["output"]["maxLength"], 63206)
        self.assertIn("rules", r["output"])
        self.assertIn("settings", r["output"])
        self.assertIn("tools", r["output"])

    def test_settings_empty_id_raises_without_call(self):
        with self.assertRaises(TuzzinaError):
            self.c.get_integration_settings("")
        self.assertEqual(CALLS, [])

    def test_settings_http_error_propagates(self):
        urllib.request.urlopen = lambda *a, **k: FakeResp({}, 404)
        with self.assertRaises(TuzzinaError) as ctx:
            self.c.get_integration_settings("int9")
        self.assertIn("404", str(ctx.exception))

    def test_settings_no_secret_logging(self):
        import contextlib
        secret = "secret-key-xyz-123"
        c = TuzzinaClient("http://x/api", secret)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            c.get_integration_settings("int9")
        self.assertNotIn(secret, out.getvalue())
        self.assertNotIn(secret, err.getvalue())


if __name__ == "__main__":
    unittest.main()
