"""Phase 2 tests: video delegation. All transport faked (no
network, no keys, no credits, no live calls). Proves: request
contract, response contract, failures, no-execution guarantees,
cycle wiring, and untouched text/image behavior."""
import io
import json
import os
import sys
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from research.generation import VideoRequest, plan_generation
from research.analysis import ContentOpportunity
from tuzzina.client import TuzzinaClient, TuzzinaError


def opp(media="video"):
    return ContentOpportunity(
        eligible=True, topic="Harbor dawn", angle="A", rationale="R",
        facts=["Boats gather at dawn"], source_item_ids=["g-1"],
        content_format="post", media_intent=media, audience="Libya",
        language="ar", priority="normal", confidence="high",
        constraints=[], model="mock", meta={})


class FakeResp:
    def __init__(self, payload, code=200):
        self.payload = payload
        self.code = code

    def read(self):
        return self.payload.encode("utf-8")

    def __enter__(self):
        if self.code >= 400:
            raise urllib.error.HTTPError("u", self.code, "err", {}, None)
        return self

    def __exit__(self, *a):
        return False


CALLS = []


def fake_urlopen(req, timeout=None):
    CALLS.append({"url": req.full_url, "timeout": timeout,
                  "body": json.loads(req.data.decode())})
    kind = getattr(fake_urlopen, "mode", "ok")
    if kind == "ok":
        return FakeResp(json.dumps({"id": "v9",
                                    "path": "https://cdn.test/v9.mp4",
                                    "name": "v9.mp4"}))
    if kind == "http-500":
        return FakeResp({}, 500)
    if kind == "http-402":
        return FakeResp(json.dumps({"message": "no credits"}), 402)
    if kind == "timeout":
        raise TimeoutError("slow")
    if kind == "conn":
        raise ConnectionError("down")
    if kind == "garbage":
        return FakeResp("not json{{{")
    if kind == "no-ref":
        return FakeResp(json.dumps({"ok": True}))
    raise AssertionError(kind)


class RequestCase(unittest.TestCase):
    def test_defaults_without_config(self):
        v = VideoRequest(prompt="p")
        self.assertEqual(v.video_type, "")
        self.assertEqual(v.output, "")
        self.assertEqual(v.params, {})

    def test_valid_request_fields(self):
        p = plan_generation(
            opp(), None,
            video_config={"video_type": "sometype", "output": "vertical",
                          "video_params": {"voice": "v1"}})
        self.assertIsNotNone(p.video)
        self.assertEqual(p.video.video_type, "sometype")
        self.assertEqual(p.video.output, "vertical")
        self.assertEqual(p.video.params, {"voice": "v1"})
        self.assertIn("Harbor dawn", p.video.prompt)

    def test_no_config_keeps_intent_only(self):
        p = plan_generation(opp())
        self.assertIsNotNone(p.video)
        self.assertEqual(p.video.video_type, "")
        self.assertEqual(p.video.params, {})


class ClientCase(unittest.TestCase):
    def setUp(self):
        CALLS.clear()
        self._orig = urllib.request.urlopen
        urllib.request.urlopen = fake_urlopen
        fake_urlopen.mode = "ok"
        self.c = TuzzinaClient("http://x/api", "k")

    def tearDown(self):
        urllib.request.urlopen = self._orig

    def _call(self, **kw):
        d = dict(video_type="sometype", output="vertical",
                 prompt="tiny clip", params={"voice": "v1"})
        d.update(kw)
        return self.c.generate_video(**d)

    def test_type_passthrough(self):
        self._call(video_type="other-type")
        self.assertEqual(CALLS[0]["body"]["type"], "other-type")

    def test_output_passthrough(self):
        self._call(output="horizontal")
        self.assertEqual(CALLS[0]["body"]["output"], "horizontal")

    def test_custom_params_passthrough(self):
        self._call()
        cp = CALLS[0]["body"]["customParams"]
        self.assertEqual(cp["prompt"], "tiny clip")
        self.assertEqual(cp["voice"], "v1")

    def test_endpoint_and_auth(self):
        self._call()
        self.assertEqual(
            CALLS[0]["url"], "http://x/api/public/v1/generate-video")

    def test_response_to_ref(self):
        ref = self._call()
        self.assertEqual(ref, {"id": "v9",
                               "path": "https://cdn.test/v9.mp4"})

    def test_malformed_response(self):
        fake_urlopen.mode = "garbage"
        with self.assertRaises(TuzzinaError):
            self._call()

    def test_no_ref_response(self):
        fake_urlopen.mode = "no-ref"
        with self.assertRaises(TuzzinaError) as ctx:
            self._call()
        self.assertIn("no media reference", str(ctx.exception))

    def test_http_error_surfaces(self):
        fake_urlopen.mode = "http-500"
        with self.assertRaises(TuzzinaError) as ctx:
            self._call()
        self.assertIn("HTTP 500", str(ctx.exception))
        self.assertEqual(len(CALLS), 1)  # never retried

    def test_credit_error_surfaces(self):
        fake_urlopen.mode = "http-402"
        with self.assertRaises(TuzzinaError) as ctx:
            self._call()
        self.assertIn("402", str(ctx.exception))

    def test_timeout_surfaces_no_retry(self):
        fake_urlopen.mode = "timeout"
        with self.assertRaises(TimeoutError):
            self._call()
        self.assertEqual(len(CALLS), 1)

    def test_connection_single_attempt(self):
        fake_urlopen.mode = "conn"
        with self.assertRaises(TuzzinaError):
            self._call()
        self.assertEqual(len(CALLS), 1)

    def test_long_default_timeout(self):
        self._call()
        self.assertGreaterEqual(CALLS[0]["timeout"], 300)

    def test_missing_type_rejected_locally(self):
        with self.assertRaises(TuzzinaError):
            self._call(video_type="")
        self.assertEqual(CALLS, [])

    def test_no_bytes_no_upload(self):
        import pathlib
        src = (pathlib.Path(__file__).parent.parent / "src" /
               "tuzzina" / "client.py").read_text(encoding="utf-8")
        start = src.index("def generate_video")
        body = src[start:]
        self.assertNotIn("upload_bytes", body)
        self.assertNotIn("R2", body)
        self.assertNotIn("boto", body)

    def test_no_secrets_logged(self):
        import contextlib
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            self._call()
        self.assertNotIn("k", out.getvalue() + err.getvalue())


class CycleCase(unittest.TestCase):
    def _cycle(self, sources, **kw):
        import g1.rss as R
        import socket
        from research.cycle import run_cycle
        from research.monitor import MemoryStateStore

        routes = kw.pop("routes")

        class Resp:
            def __init__(self, body):
                self._body = body.encode()
                self.headers = {"Content-Type": "text/xml"}

            def read(self, n=-1):
                return self._body

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        class Opener:
            def open(self, req, timeout=None):
                return Resp(routes[req.full_url])

        real_opener, real_dns = R._opener, socket.getaddrinfo
        R._opener = Opener
        socket.getaddrinfo = lambda h, p, *a, **k: [
            (2, 1, 6, "", ("93.184.216.34", 0))]
        try:
            store = kw.pop("store", MemoryStateStore())
            policy = kw.pop("policy")
            return run_cycle(
                sources, store=store, policy=policy,
                schedule={"timezone": "UTC", "times": ["09:00"]},
                now="2026-09-08T12:00:00+00:00",
                integration_id="int-1", **kw)
        finally:
            R._opener = real_opener
            socket.getaddrinfo = real_dns

    def _rss(self):
        body = ("<?xml version=\"1.0\"?><rss version=\"2.0\"><channel>"
                "<title>S</title>"
                "<item><title>Harbor boats</title>"
                "<link>http://feed.test/0</link><guid>g-0</guid>"
                "<pubDate>Mon, 08 Sep 2026 09:00:00 +0000</pubDate>"
                "<description>Boats gather at dawn.</description>"
                "</item></channel></rss>")
        return {"http://feed.test/rss": body}

    def _policy(self, **over):
        p = {"brand": {"tone": "direct", "audience": "Libya",
                       "banned_words": [], "preferred_words": [],
                       "cta_style": "Go"},
             "language": {"default": "ar"},
             "pillars": [], "generation": {}, "media": {}}
        p.update(over)
        return p

    def _src(self):
        return {"type": "rss", "url": "http://feed.test/rss"}

    def test_video_resolve_attaches_ref(self):
        seen = []

        def fake_resolve(req):
            seen.append(req)
            return {"id": "v9", "path": "https://cdn.test/v9.mp4"}

        out = self._cycle(
            [self._src()], routes=self._rss(),
            policy=self._policy(media={"prefer": "video"}),
            video_resolve=fake_resolve)
        self.assertEqual(out.error, "")
        self.assertEqual(out.video_media,
                         [{"id": "v9", "path": "https://cdn.test/v9.mp4"}])
        self.assertEqual(len(out.video_deferred), 1)
        self.assertTrue(out.committed)
        self.assertEqual(seen[0].video_type, "")
        self.assertIn("Harbor", seen[0].prompt)

    def test_video_failure_fails_cycle(self):
        def boom(req):
            raise TuzzinaError("HTTP 500 down")

        from research.monitor import MemoryStateStore
        store = MemoryStateStore()
        out = self._cycle(
            [self._src()], routes=self._rss(), store=store,
            policy=self._policy(media={"prefer": "video"}),
            video_resolve=boom)
        self.assertIn("500", out.error)
        self.assertFalse(out.committed)
        self.assertEqual(out.video_media, [])
        self.assertEqual(store.load("http://feed.test/rss").seen, {})

    def test_no_resolve_no_call(self):
        seen = []

        def spy(req):
            seen.append(req)
            return {"id": "v", "path": "p"}

        out = self._cycle(
            [self._src()], routes=self._rss(),
            policy=self._policy(), video_resolve=spy)
        self.assertEqual(seen, [])  # media:none -> no video request
        self.assertEqual(out.video_media, [])
        self.assertTrue(out.committed)


if __name__ == "__main__":
    unittest.main()
