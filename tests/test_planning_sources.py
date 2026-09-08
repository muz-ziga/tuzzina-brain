import os
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from channels.profile import resolve_strategy
from channels.strategy import ChannelStrategy
from g3.planner import next_slots, plan
from contracts import ContentPackage


def _strat(**kw):
    from channels.strategy import (Brand, ContentPolicy, GenerationPolicy,
                                    HashtagPolicy, MediaPolicy,
                                    MentionPolicy, PlanningPolicy)
    d = dict(integration_id="i1", brand=Brand(), hashtags=HashtagPolicy(),
             links_policy="hide", mentions=MentionPolicy(),
             media=MediaPolicy(), sources=kw.pop("sources", []),
             content=ContentPolicy(), generation=GenerationPolicy(),
              planning=PlanningPolicy(),
              extras={})
    d.update(kw)
    return ChannelStrategy(**d)


def _proj(**kw):
    d = {"brand": {"name": "P"}, "language": {"default": "ar"},
         "hashtags": {"enabled": False}, "links_policy": "hide",
         "sources": [], "schedule": {"timezone": "UTC"}}
    d.update(kw)
    return d


class SourcesPrecedenceTest(unittest.TestCase):
    def test_strategy_sources_win(self):
        s = _strat(sources=[{"type": "website", "url": "https://a.test",
                             "n": 1}])
        proj = _proj(sources=[{"type": "website",
                               "url": "https://b.test", "n": 5}])
        r = resolve_strategy(proj, s)
        self.assertEqual(r["sources"],
                         [{"type": "website", "url": "https://a.test",
                           "n": 1}])
        self.assertEqual(r["sources_from"], "channel")

    def test_campaign_fallback(self):
        s = _strat()
        proj = _proj(sources=[{"type": "website",
                               "url": "https://b.test", "n": 5}])
        r = resolve_strategy(proj, s)
        self.assertEqual(r["sources"],
                         [{"type": "website", "url": "https://b.test",
                           "n": 5}])
        self.assertEqual(r["sources_from"], "campaign")

    def test_empty_channel_sources_fall_back(self):
        s = _strat(sources=[])
        proj = _proj(sources=[{"type": "website",
                               "url": "https://b.test", "n": 5}])
        r = resolve_strategy(proj, s)
        self.assertEqual(r["sources_from"], "campaign")

    def test_both_empty(self):
        r = resolve_strategy(_proj(), _strat())
        self.assertEqual(r["sources"], [])
        self.assertEqual(r["sources_from"], "campaign")


def _pkg():
    return ContentPackage(title="t", content="hello world", media=[],
                          platform="facebook",
                          settings={"__type": "facebook"})


class PlanningCountsTest(unittest.TestCase):
    def _now(self):
        return datetime(2026, 9, 7, 8, 0, tzinfo=timezone.utc)  # Monday

    def test_daily_count_caps(self):
        import g3.planner as P
        orig = P.datetime
        try:
            class FakeDT(datetime):
                @classmethod
                def now(cls, tz=None):
                    return datetime(2026, 9, 7, 8, 0, tzinfo=timezone.utc)
            P.datetime = FakeDT
            out = plan([_pkg() for _ in range(5)],
                       {"timezone": "UTC", "daily_count": 2})
        finally:
            P.datetime = orig
        self.assertEqual(len(out), 2)

    def test_spacing(self):
        slots = next_slots([], ["09:00", "09:10", "09:30"], "UTC", 3,
                           now=self._now(), spacing_minutes=15)
        self.assertEqual(len(slots), 3)
        dts = [datetime.fromisoformat(s) for s in slots]
        for a, b in zip(dts, dts[1:]):
            self.assertGreaterEqual((b - a).total_seconds(), 15 * 60)

    def test_window(self):
        slots = next_slots([], ["08:00", "12:00", "20:00"], "UTC", 5,
                           now=self._now(), start_time="09:00",
                           end_time="18:00")
        for s in slots:
            hh, mm = datetime.fromisoformat(s).hour, \
                datetime.fromisoformat(s).minute
            self.assertGreaterEqual((hh, mm), (9, 0))
            self.assertLessEqual((hh, mm), (18, 0))
        self.assertNotIn(8, [datetime.fromisoformat(s).hour
                             for s in slots])
        self.assertNotIn(20, [datetime.fromisoformat(s).hour
                              for s in slots])

    def test_timezone(self):
        slots = next_slots([], ["09:00"], "Europe/Madrid", 1,
                           now=self._now())
        dt = datetime.fromisoformat(slots[0])
        # 09:00 in the requested zone (offset varies by platform
        # tzdata; the hour in-zone is what matters).
        self.assertEqual(dt.hour, 9)
        self.assertEqual(dt.minute, 0)

    def test_monthly_weekly_caps(self):
        import g3.planner as P
        orig = P.datetime
        try:
            class FakeDT(datetime):
                @classmethod
                def now(cls, tz=None):
                    return datetime(2026, 9, 7, 8, 0, tzinfo=timezone.utc)
            P.datetime = FakeDT
            out = plan([_pkg() for _ in range(4)],
                       {"timezone": "UTC", "weekly_count": 3,
                        "monthly_count": 2})
        finally:
            P.datetime = orig
        self.assertEqual(len(out), 2)


class TextShapingTest(unittest.TestCase):
    def test_facebook_long(self):
        from adapters.facebook import FacebookAdapter
        out = FacebookAdapter().shape_text("x" * 70000, ["#a"])
        self.assertLessEqual(len(out), 63206)

    def test_instagram_long(self):
        from adapters.instagram import InstagramAdapter
        out = InstagramAdapter().shape_text("x" * 5000, ["#a"])
        self.assertLessEqual(len(out), 2200)

    def test_instagram_no_attach(self):
        from adapters.instagram import InstagramAdapter
        out = InstagramAdapter().apply_link("hi", "http://u.test",
                                            "attach", "")
        self.assertNotIn("u.test", out)

    def test_no_tuzzina_imports(self):
        # Source citations in comments (e.g. "# facebook.dto.ts:101")
        # are documentation, not copies. We check import lines only.
        import adapters.facebook as fb
        import adapters.instagram as ig
        import adapters.base as base
        for mod in (fb, ig, base):
            lines = open(mod.__file__, encoding="utf-8").read().splitlines()
            imports = [ln for ln in lines
                       if ln.strip().startswith(("import ", "from "))]
            blob = "\n".join(imports)
            for bad in ("gitroom", "postiz", "tuzzina", "nestjs",
                        ".dto"):
                self.assertNotIn(bad, blob)

    def test_no_r2_direct(self):
        import adapters.facebook as fb
        import adapters.instagram as ig
        for mod in (fb, ig):
            src = open(mod.__file__, encoding="utf-8").read()
            for bad in ("boto3", "r2.cloudflarestorage", "S3Client",
                        "PutObject"):
                self.assertNotIn(bad, src)

    def test_no_scheduler_side_effects(self):
        # Docstring mentions ("No cron...") are documentation.
        # Check for real scheduler constructs: imports + calls.
        import g3.planner as P
        lines = open(P.__file__, encoding="utf-8").read().splitlines()
        code = "\n".join(ln for ln in lines
                         if not ln.strip().startswith(("#", '"""', "'''")))
        for bad in ("import cron", "import sched", "import threading",
                    "import queue", "from apscheduler", "import celery",
                    "Temporal", "time.sleep(", "threading."):
            self.assertNotIn(bad, code)


if __name__ == "__main__":
    unittest.main()
