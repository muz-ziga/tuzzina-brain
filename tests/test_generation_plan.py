"""R6 tests: generation orchestration. Plan decisions are pure
and deterministic; cycle integration proves opportunity-shaped
inputs, media-intent gating, and video deferral. Fully offline."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from contracts import SourceItem
from research.analysis import ContentOpportunity
from research.generation import (GenerationError, image_gen_for,
                                 plan_generation, shape_item_for_g2)


def opp(media="none", topic="Harbor dawn",
        facts=None, eligible=True):
    return ContentOpportunity(
        eligible=eligible, topic=topic, angle="Dawn angle",
        rationale="r",
        facts=list(facts) if facts is not None
        else ["Boats gather at dawn", "Gulls follow home"],
        source_item_ids=["g-1"], content_format="post",
        media_intent=media, audience="Libya", language="ar",
        priority="normal", confidence="high",
        constraints=[], model="mock", meta={})


def item(title="Raw title", text="Raw body text here."):
    return SourceItem(
        source_id="g-1", source_type="rss",
        source_url="http://feed.test/a", title=title, text=text,
        published_at="2026-09-08T09:00:00+00:00", platform="rss",
        item_id="g-1", content_hash="h")


class PlanCase(unittest.TestCase):
    def test_text_only(self):
        p = plan_generation(opp("none"))
        self.assertEqual(p.text.title, "Harbor dawn")
        self.assertIn("Boats gather", p.text.summary)
        self.assertIsNone(p.image)
        self.assertIsNone(p.video)
        self.assertEqual(p.media_intent, "none")

    def test_image_request(self):
        p = plan_generation(opp("image"))
        self.assertIn("Harbor dawn", p.image.prompt)
        self.assertIn("Boats gather", p.image.prompt)
        self.assertIsNone(p.video)

    def test_video_request_intent_only(self):
        p = plan_generation(opp("video"))
        self.assertIsNotNone(p.video)
        self.assertIn("Harbor dawn", p.video.prompt)
        self.assertIsNone(p.image)

    def test_ineligible_empty_plan(self):
        p = plan_generation(opp("image", eligible=False))
        self.assertIsNone(p.text)
        self.assertIsNone(p.image)
        self.assertIsNone(p.video)

    def test_none_opportunity_empty_plan(self):
        p = plan_generation(None)
        self.assertIsNone(p.text)

    def test_topic_fallback_to_item(self):
        o = opp("none", topic="", facts=["Fact words"])
        p = plan_generation(o, [item(title="Fallback title")])
        self.assertEqual(p.text.title, "Fallback title")

    def test_unknown_intent_normalized(self):
        p = plan_generation(opp("hologram"))
        self.assertEqual(p.media_intent, "none")
        self.assertIsNone(p.image)
        self.assertIsNone(p.video)

    def test_shape_preserves_identity(self):
        p = plan_generation(opp("image"))
        s = shape_item_for_g2(item(), p)
        self.assertEqual(s.item_id, "g-1")
        self.assertEqual(s.source_id, "g-1")
        self.assertEqual(s.source_url, "http://feed.test/a")
        self.assertEqual(s.content_hash, "h")
        self.assertEqual(s.title, "Harbor dawn")
        self.assertIn("Boats gather", s.text)
        self.assertNotIn("Raw body", s.text)

    def test_shape_without_text_raises(self):
        with self.assertRaises(GenerationError):
            shape_item_for_g2(item(), plan_generation(None))

    def test_image_gen_gating(self):
        engine = object()
        self.assertIs(image_gen_for(plan_generation(opp("image")),
                                    engine), engine)
        self.assertIsNone(image_gen_for(plan_generation(opp("none")),
                                        engine))
        self.assertIsNone(image_gen_for(plan_generation(None), engine))

    def test_deterministic(self):
        a = plan_generation(opp("image"), [item()])
        b = plan_generation(opp("image"), [item()])
        self.assertEqual(a, b)

    def test_no_execution_constructs(self):
        import pathlib
        src = (pathlib.Path(__file__).parent.parent / "src" /
               "research" / "generation.py").read_text(encoding="utf-8")
        code = []
        in_doc = False
        for ln in src.splitlines():
            s = ln.strip()
            if '"""' in s:
                if s.count('"""') == 2:
                    continue
                in_doc = not in_doc
                continue
            if in_doc or not s or s.startswith("#"):
                continue
            code.append(ln)
        blob = "\n".join(code)
        import re as _re
        for bad in ("urlopen", "Tuzzina", "create_post",
                    "upload", "schedule", "cron", "oauth", "registry",
                    "sqlite", "sleep", "subprocess", "threading"):
            self.assertNotIn(bad, blob, bad)
        # Bare Request( = HTTP transport; Text/Image/VideoRequest
        # dataclass names must not trip the guard.
        self.assertEqual(len(_re.findall(r"\bRequest\(", blob)), 0)


class CycleIntegrationCase(unittest.TestCase):
    def _cycle(self, sources, **kw):
        import g1.rss as R
        import socket
        from research.cycle import run_cycle
        from research.monitor import MemoryStateStore

        routes = kw.pop("routes")
        CCTV = kw.pop("cctv", [])

        class FakeResp:
            def __init__(self, body):
                self._body = body.encode()
                self.headers = {"Content-Type": "text/xml"}

            def read(self, n=-1):
                return self._body

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        class FakeOpener:
            def open(self, req, timeout=None):
                CCTV.append(req.full_url)
                return FakeResp(routes[req.full_url])

        real_opener, real_dns = R._opener, socket.getaddrinfo
        R._opener = FakeOpener
        socket.getaddrinfo = lambda h, p, *a, **k: [
            (2, 1, 6, "", ("93.184.216.34", 0))]
        try:
            return run_cycle(sources, store=MemoryStateStore(),
                             policy=dict(kw.pop("policy")),
                             schedule={"timezone": "UTC",
                                       "times": ["09:00"]},
                             now="2026-09-08T12:00:00+00:00",
                             integration_id="int-1", **kw)
        finally:
            R._opener = real_opener
            socket.getaddrinfo = real_dns

    def _rss(self, n=1):
        items = "".join(
            f"<item><title>Harbor note {i}</title>"
            f"<link>http://feed.test/{i}</link><guid>g-{i}</guid>"
            f"<pubDate>Mon, 08 Sep 2026 09:00:00 +0000</pubDate>"
            f"<description>Boats gather at dawn near pier {i}.</description>"
            f"</item>" for i in range(n))
        body = ("<?xml version=\"1.0\"?><rss version=\"2.0\"><channel>"
                f"<title>S</title>{items}</channel></rss>")
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

    def test_media_none_stays_text_only(self):
        # THE R6 gap proof: without the gate, the pipeline adds a
        # generator entry whenever image_gen is present.
        out = self._cycle([self._src()], routes=self._rss(),
                          policy=self._policy())
        self.assertEqual(out.error, "")
        self.assertTrue(out.packages)
        for pkg in out.packages:
            self.assertEqual(pkg.media, [])
        self.assertEqual(out.video_deferred, [])
        self.assertTrue(out.committed)

    def test_media_image_propagates_reference(self):
        out = self._cycle(
            [self._src()],
            routes=self._rss(),
            policy=self._policy(media={"prefer": "image"}))
        self.assertEqual(out.error, "")
        kinds = [m["kind"] for pkg in out.packages for m in pkg.media]
        self.assertTrue(kinds)
        self.assertTrue(all(k == "generator" for k in kinds))
        prompts = [m["prompt"] for pkg in out.packages
                   for m in pkg.media]
        self.assertTrue(all("Harbor" in pr or "harbor" in pr
                            for pr in prompts))

    def test_video_deferred_not_executed(self):
        out = self._cycle(
            [self._src()],
            routes=self._rss(),
            policy=self._policy(media={"prefer": "video"}))
        self.assertEqual(out.error, "")  # no failure ...
        self.assertEqual(len(out.video_deferred), 1)  # ... recorded ...
        self.assertTrue(out.packages)  # ... text path intact ...
        self.assertTrue(out.committed)  # ... state advances normally
        for pkg in out.packages:
            self.assertEqual(pkg.media, [])

    def test_opportunity_shapes_text(self):
        out = self._cycle([self._src()], routes=self._rss(),
                          policy=self._policy())
        self.assertTrue(out.packages)
        for pkg in out.packages:
            self.assertIn("(direct)", pkg.content)  # brand preserved
            self.assertIn("Harbor note 0", pkg.content)  # fact-derived
            self.assertNotIn("pier", pkg.content)  # raw item body replaced
            self.assertTrue(pkg.hashtags)  # G2 policy intact

    def test_banned_words_enforced(self):
        pol = self._policy()
        pol["brand"]["banned_words"] = ["boats"]
        out = self._cycle(
            [self._src()], routes=self._rss(), policy=pol)
        for pkg in out.packages:
            self.assertNotIn("boats", pkg.content.lower())

    def test_no_tuzzina_contact(self):
        seen = []
        self._cycle([self._src()], routes=self._rss(),
                    policy=self._policy(), cctv=seen)
        self.assertTrue(seen)
        for u in seen:
            self.assertNotIn("/public/v1", u)
            self.assertNotIn("openai", u)
            self.assertNotIn("/mcp", u)


if __name__ == "__main__":
    unittest.main()
