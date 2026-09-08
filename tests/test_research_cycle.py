"""R5 tests: end-to-end research cycle, fully offline. Transport
faked (RSS/YouTube feeds + website HTML), models are Mocks, time
is an explicit string. No OpenAI, no Tuzzina, no MCP, no DB."""
import os
import socket
import sys
import unittest
import urllib.error

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import g1.rss as R
import g1.website as W
from research.analysis import MockAnalysisModel
from research.cycle import run_cycle
from research.models import MockResearchModel, ResearchError
from research.monitor import MemoryStateStore

RSS2 = """<?xml version="1.0"?><rss version="2.0"><channel>
<title>Spike</title><link>http://feed.test/</link>
<item><title>Harbor boats gather</title><link>http://feed.test/a</link>
<guid>g-a</guid><pubDate>Mon, 08 Sep 2026 09:00:00 +0000</pubDate>
<description>Boats gather at dawn near the harbor.</description></item>
<item><title>Gulls follow boats</title><link>http://feed.test/b</link>
<guid>g-b</guid><pubDate>Mon, 08 Sep 2026 10:00:00 +0000</pubDate>
<description>Gulls follow the fishing boats home.</description></item>
</channel></rss>"""

YT2 = """<?xml version="1.0"?><feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"
 xmlns="http://www.w3.org/2005/Atom"
 xmlns:media="http://search.yahoo.com/mrss/"><title>Chan</title>
<entry><id>yt:video:V1AAAAAA</id><yt:videoId>V1AAAAAA</yt:videoId>
<title>Harbor morning video</title>
<link rel="alternate" href="https://www.youtube.com/watch?v=V1AAAAAA"/>
<published>2026-09-08T09:30:00+00:00</published>
<media:group><media:description>Morning harbor footage.</media:description>
</media:group></entry>
<entry><id>yt:video:V2BBBBBB</id><yt:videoId>V2BBBBBB</yt:videoId>
<title>Evening boats video</title>
<link rel="alternate" href="https://www.youtube.com/watch?v=V2BBBBBB"/>
<published>2026-09-08T18:00:00+00:00</published>
<media:group><media:description>Evening boats footage.</media:description>
</media:group></entry>
</feed>"""

PAGE = """<html><head><title>Harbor article</title></head><body>
<p>Static page about the harbor festival boats and music.</p>
</body></html>"""

CID = "UCBR8-60-B28hp2BmDPdntcQ"
YTURL = ("https://www.youtube.com/feeds/videos.xml?channel_id=" + CID)
NOW = "2026-09-08T12:00:00+00:00"

POLICY = {"brand": {"tone": "direct", "audience": "Libya",
                    "banned_words": [], "preferred_words": [],
                    "cta_style": "Go"},
          "language": {"default": "ar"},
          "pillars": [], "generation": {}, "media": {}}
SCHEDULE = {"timezone": "UTC", "times": ["09:00"]}


class FakeResp:
    def __init__(self, body=b"", headers=None, url=""):
        self._body = body if isinstance(body, bytes) else \
            body.encode("utf-8")
        self.headers = headers or {}
        self.url = url

    def read(self, n=-1):
        return self._body if n is None or n < 0 else self._body[:n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


FETCHED = []


class FakeOpener:
    routes = {}

    def open(self, req, timeout=None):
        FETCHED.append(req.full_url)
        route = self.routes[req.full_url]
        if route[0] == "ok":
            _, body, ctype = route
            return FakeResp(body, {"Content-Type": ctype}, req.full_url)
        raise route[1]


def _website_fake(req, timeout=None):
    FETCHED.append(req.full_url)
    if req.full_url == "http://site.test/article":
        return FakeResp(PAGE, {"Content-Type": "text/html"},
                        req.full_url)
    raise urllib.error.HTTPError(req.full_url, 404, "e", {}, None)


_REAL_GETADDRINFO = socket.getaddrinfo
_REAL_ROPEN = R._opener
_REAL_WOPEN = W.urlopen


def _public_dns(host, port, *a, **k):
    return [(2, 1, 6, "", ("93.184.216.34", 0))]


class CountingResearch(MockResearchModel):
    def __init__(self):
        self.calls = 0

    def research(self, items):
        self.calls += 1
        return super().research(items)


class CountingAnalysis(MockAnalysisModel):
    def __init__(self):
        self.calls = 0

    def analyze(self, result, policy, items=None):
        self.calls += 1
        return super().analyze(result, policy, items)


class BoomResearch(MockResearchModel):
    def research(self, items):
        raise ResearchError("boom")


class CycleCase(unittest.TestCase):
    def setUp(self):
        del FETCHED[:]
        FakeOpener.routes = {
            "http://feed.test/rss": ("ok", RSS2, "application/rss+xml"),
            YTURL: ("ok", YT2, "text/xml"),
        }
        R._opener = FakeOpener
        W.urlopen = _website_fake
        socket.getaddrinfo = _public_dns

    def tearDown(self):
        R._opener = _REAL_ROPEN
        W.urlopen = _REAL_WOPEN
        socket.getaddrinfo = _REAL_GETADDRINFO

    def _run(self, sources, **kw):
        kw.setdefault("store", MemoryStateStore())
        kw.setdefault("policy", dict(POLICY))
        kw.setdefault("schedule", dict(SCHEDULE))
        kw.setdefault("now", NOW)
        kw.setdefault("integration_id", "int-1")
        return run_cycle(sources, **kw)

    def _rss(self, url="http://feed.test/rss"):
        return {"type": "rss", "url": url}

    def _yt(self):
        return {"type": "youtube", "channel_id": CID}

    def test_new_source_full_cycle(self):
        store = MemoryStateStore()
        out = self._run([self._rss()], store=store)
        self.assertEqual(out.error, "")
        self.assertEqual(len(out.items), 2)
        self.assertIsNotNone(out.research)
        self.assertTrue(out.opportunity.eligible)
        self.assertEqual(len(out.packages), 2)
        self.assertEqual(len(out.planned), 2)
        self.assertEqual(len(out.intents), 2)
        self.assertTrue(out.committed)
        self.assertEqual(len(store.load("http://feed.test/rss").seen), 2)
        self.assertIn("(direct)", out.packages[0].content)

    def test_unchanged_stops_before_research(self):
        store = MemoryStateStore()
        rm, am = CountingResearch(), CountingAnalysis()
        self._run([self._rss()], store=store, research_model=rm,
                  analysis_model=am)
        self.assertEqual((rm.calls, am.calls), (1, 1))
        rm2, am2 = CountingResearch(), CountingAnalysis()
        out = self._run([self._rss()], store=store, research_model=rm2,
                        analysis_model=am2)
        self.assertEqual((rm2.calls, am2.calls), (0, 0))
        self.assertEqual(out.items, [])
        self.assertIsNone(out.research)
        self.assertIsNone(out.opportunity)
        self.assertEqual(out.packages, [])
        self.assertEqual(out.intents, [])
        self.assertTrue(out.committed)
        self.assertEqual(out.error, "")

    def test_updated_not_new(self):
        store = MemoryStateStore()
        self._run([self._rss()], store=store)
        FakeOpener.routes["http://feed.test/rss"] = (
            "ok", RSS2.replace("Boats gather at dawn",
                               "Boats gather at DAWN edited"),
            "application/rss+xml")
        rm = CountingResearch()
        out = self._run([self._rss()], store=store, research_model=rm)
        self.assertEqual(rm.calls, 0)  # UPDATED is not NEW
        self.assertEqual(out.items, [])

    def test_multi_source_combined(self):
        out = self._run([self._rss(), self._yt()])
        self.assertEqual(len(out.items), 4)
        # one call, combined input (R4 single-opportunity contract)
        self.assertEqual(len(out.research.item_ids), 4)
        self.assertIsNotNone(out.opportunity)
        self.assertEqual(len(out.intents), 4)

    def test_research_traceability(self):
        out = self._run([self._rss(), self._yt()])
        known = {i.item_id or i.source_id for i in out.items}
        for fid in out.research.item_ids:
            self.assertIn(fid, known)

    def test_analysis_traceability(self):
        out = self._run([self._rss()])
        stmts = set()
        for f in out.research.findings:
            stmts.add(f.statement)
        for fact in out.opportunity.facts:
            self.assertIn(fact, stmts)
        self.assertTrue(set(out.opportunity.source_item_ids) <=
                        set(out.research.item_ids))

    def test_eligible_reaches_g2(self):
        out = self._run([self._rss()])
        self.assertTrue(out.opportunity.eligible)
        self.assertTrue(all(p.content.strip() for p in out.packages))
        self.assertTrue(all(p.hashtags for p in out.packages))

    def test_ineligible_stops_cleanly(self):
        pol = dict(POLICY)
        pol["pillars"] = ["cooking"]
        rm, am = CountingResearch(), CountingAnalysis()
        out = self._run([self._rss()], policy=pol, research_model=rm,
                        analysis_model=am)
        self.assertEqual((rm.calls, am.calls), (1, 1))
        self.assertFalse(out.opportunity.eligible)
        self.assertEqual(out.packages, [])
        self.assertEqual(out.intents, [])
        self.assertTrue(out.committed)  # reviewed = processed
        self.assertEqual(out.error, "")

    def test_g2_g3_intent_chain(self):
        out = self._run([self._rss()], integration_id="int-9",
                        )
        for p, it in zip(out.planned, out.intents):
            self.assertEqual(it.publish_at, p.planned_at)
            self.assertEqual(it.mode, "draft")
            self.assertEqual(it.integration_id, "int-9")
            self.assertEqual(it.media, [])  # unresolved: execution phase
            self.assertTrue(it.content)

    def test_no_tuzzina_contact(self):
        self._run([self._rss(), self._yt(),
                   {"type": "website",
                    "url": "http://site.test/article"}])
        allowed = {"http://feed.test/rss", YTURL,
                   "http://site.test/article"}
        self.assertTrue(set(FETCHED) <= allowed)
        for u in FETCHED:
            self.assertNotIn("/public/v1", u)
            self.assertNotIn("openai", u)

    def test_research_failure_blocks_commit(self):
        store = MemoryStateStore()
        out = self._run([self._rss()], store=store,
                        research_model=BoomResearch())
        self.assertIn("boom", out.error)
        self.assertFalse(out.committed)
        self.assertIsNone(out.opportunity)
        self.assertEqual(store.load("http://feed.test/rss").seen, {})
        # redelivery as NEW on the next cycle (at-least-once)
        out2 = self._run([self._rss()], store=store)
        self.assertEqual(len(out2.items), 2)

    def test_analysis_failure_blocks_commit(self):
        from research.analysis import AnalysisError

        class BoomA(MockAnalysisModel):
            def analyze(self, result, policy, items=None):
                raise AnalysisError("boom-a")

        store = MemoryStateStore()
        out = self._run([self._rss()], store=store,
                        analysis_model=BoomA())
        self.assertIn("boom-a", out.error)
        self.assertFalse(out.committed)
        self.assertEqual(store.load("http://feed.test/rss").seen, {})

    def test_g2_failure_blocks_commit(self):
        class BoomText:
            def generate(self, title, summary, brand):
                raise RuntimeError("g2-boom")

        store = MemoryStateStore()
        out = self._run([self._rss()], store=store, text_gen=BoomText())
        self.assertIn("g2-boom", out.error)
        self.assertFalse(out.committed)

    def test_g3_failure_blocks_commit(self):
        store = MemoryStateStore()
        out = self._run([self._rss()], store=store,
                        schedule="not-a-dict")
        self.assertNotEqual(out.error, "")
        self.assertFalse(out.committed)

    def test_website_always_new_documented(self):
        src = {"type": "website", "url": "http://site.test/article"}
        out1 = self._run([src])
        out2 = self._run([src])
        # No stable page identity exists, so no dedup is possible:
        # each cycle treats extracted pages as NEW (like run.py).
        self.assertEqual(len(out1.items), 1)
        self.assertEqual(len(out2.items), 1)
        self.assertFalse(out1.committed)  # no monitor state involved
        self.assertEqual(out1.error, "")

    def test_offline_deterministic(self):
        kw = dict(policy=dict(POLICY), schedule=dict(SCHEDULE), now=NOW,
                  integration_id="int-1")
        a = self._run([self._rss(), self._yt()],
                      store=MemoryStateStore(), **kw)
        b = self._run([self._rss(), self._yt()],
                      store=MemoryStateStore(), **kw)
        # Full behavioral equality except generator object identity
        # inside media dicts (same class, distinct instances).
        self.assertEqual(a.sources, b.sources)
        self.assertEqual(a.items, b.items)
        self.assertEqual(a.research, b.research)
        self.assertEqual(a.opportunity, b.opportunity)
        self.assertEqual(a.intents, b.intents)
        self.assertEqual(a.committed, b.committed)
        self.assertEqual(a.error, b.error)
        for pa, pb in zip(a.packages, b.packages):
            self.assertEqual(
                (pa.title, pa.content, pa.hashtags, pa.platform,
                 pa.settings),
                (pb.title, pb.content, pb.hashtags, pb.platform,
                 pb.settings))
        for pa, pb in zip(a.planned, b.planned):
            self.assertEqual(
                (pa.platform, pa.content, pa.planned_at, pa.settings,
                 pa.tags, pa.source_ref),
                (pb.platform, pb.content, pb.planned_at, pb.settings,
                 pb.tags, pb.source_ref))


if __name__ == "__main__":
    unittest.main()
