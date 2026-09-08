import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from channels.profile import resolve_strategy
from channels.strategy import (Brand, ChannelStrategy, ContentPolicy,
                                GenerationPolicy, HashtagPolicy,
                                MediaPolicy, MentionPolicy, PlanningPolicy)
from g2 import generators as G
from g2.pipeline import build_package
from contracts import SourceItem


def _project():
    return {
        "brand": {"name": "Juzzir", "tone": "informative",
                  "audience": "general Arabic audience",
                  "banned_words": [], "preferred_words": ["juzzir"],
                  "cta_style": "Learn more"},
        "language": {"default": "ar"},
        "hashtags": {"enabled": True,
                     "per_platform": {"facebook": {"max": 5}}},
        "links_policy": "hide",
        "sources": [],
        "schedule": {"timezone": "Europe/Madrid", "days": [], "times": []},
    }


def _strategy(**kw):
    d = dict(
        integration_id="integ-fb-1",
        brand=Brand(tone="informative, slightly playful",
                    audience="music producers, Arabic creators",
                    cta_style="Try free"),
        hashtags=HashtagPolicy(enabled=True, max=3,
                               preferred=["juzzir", "mastering"]),
        links_policy="hide",
        mentions=MentionPolicy(),
        media=MediaPolicy(),
        sources=[],
        content=ContentPolicy(),
        generation=GenerationPolicy(),
        planning=PlanningPolicy(),
        extras={},
    )
    d.update(kw)
    return ChannelStrategy(**d)


def _item():
    return SourceItem(source_id="s1", source_type="website",
                      source_url="https://www.juzzir.com/x",
                      title="Juzzir — AI Audio Mastering",
                      text="One Mix. Multiple Personalities. Upload your track.",
                      images=[])


class IntegrationTest(unittest.TestCase):
    def test_resolved_payload_matches_facebook_dto(self):
        ch = _strategy()
        meta = {"identifier": "facebook", "name": "Juzzir",
                "integration_id": "integ-fb-1"}
        cfg = resolve_strategy(_project(), ch, channel_meta=meta)
        pkg = build_package(_item(), cfg["brand"], cfg["language"],
                            cfg["hashtags"], cfg["links_policy"],
                            G.MockTextGenerator(), G.MockImageGenerator(),
                            platform=meta["identifier"])
        self.assertEqual(pkg.platform, "facebook")
        self.assertEqual(pkg.settings, {"__type": "facebook"})
        self.assertEqual(len(pkg.hashtags), 3)
        self.assertIn("#juzzir", pkg.hashtags)
        self.assertIn("#mastering", pkg.hashtags)
        self.assertIn("informative, slightly playful", pkg.content)
        self.assertNotIn("juzzir.com/x", pkg.content)
        self.assertEqual(pkg.media[0]["kind"], "generator")
        self.assertEqual(cfg["integration_id"], "integ-fb-1")

    def test_project_defaults_still_work_without_channel_overrides(self):
        ch = _strategy(brand=Brand(), hashtags=HashtagPolicy())
        meta = {"identifier": "facebook"}
        cfg = resolve_strategy(_project(), ch, channel_meta=meta)
        pkg = build_package(_item(), cfg["brand"], cfg["language"],
                            cfg["hashtags"], cfg["links_policy"],
                            G.MockTextGenerator(), G.MockImageGenerator(),
                            platform=meta["identifier"])
        self.assertIn("#juzzir", pkg.hashtags)
        self.assertLessEqual(len(pkg.hashtags), 5)
        self.assertEqual(pkg.settings["__type"], "facebook")

    def test_strategy_store_roundtrip_integration(self):
        import tempfile
        from channels.strategy_store import save, load
        tmp = tempfile.mkdtemp(prefix="tbra_integ_")
        s = _strategy()
        save(s, base_dir=tmp)
        loaded = load("integ-fb-1", base_dir=tmp)
        meta = {"identifier": "facebook"}
        cfg = resolve_strategy(_project(), loaded, channel_meta=meta)
        self.assertEqual(cfg["brand"]["tone"],
                         "informative, slightly playful")
        self.assertEqual(cfg["hashtags"]["per_platform"]["facebook"]["max"],
                         3)
        self.assertEqual(cfg["hashtags"]["per_platform"]["facebook"]
                         ["preferred"], ["juzzir", "mastering"])
        self.assertEqual(cfg["integration_id"], "integ-fb-1")
        self.assertNotIn("provider_overrides", cfg)


if __name__ == "__main__":
    unittest.main()
