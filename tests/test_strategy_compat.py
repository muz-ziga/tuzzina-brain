"""Phase 12 Brain compat tests: language/colors/logo_url as real
Strategy fields. Old YAMLs load unchanged; new fields roundtrip;
channel wins with campaign fallback; the text-side builder never
leaks visual identity while image/video prompts receive it."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from channels.instructions import build, build_visual
from channels.profile import resolve_strategy
from channels.strategy import (Brand, ChannelStrategy, ContentPolicy,
                                GenerationPolicy, HashtagPolicy,
                                LanguagePolicy, MediaPolicy, MentionPolicy,
                                PlanningPolicy, VisualPolicy)


OLD_YAML = {
    "integration_id": "old-1",
    "strategy": {
        "brand": {"tone": "bold", "audience": "creators"},
        "hashtags": {"enabled": True, "max": 3},
        "content": {"content_pillars": ["tips"]},
        "generation": {"prompt_style": "punchy"},
    },
}


def _project(**kw):
    base = {
        "brand": {"tone": "neutral", "audience": "general",
                  "banned_words": [], "preferred_words": [],
                  "cta_style": "Learn more",
                  "colors": ["#111111"],
                  "logo_url": "https://cdn.test/logo.png"},
        "language": {"default": "ar"},
        "hashtags": {"enabled": True, "per_platform": {}},
        "links_policy": "hide", "sources": [], "schedule": {},
    }
    base.update(kw)
    return base


def _strategy(**kw):
    d = dict(
        integration_id="skill-1",
        brand=Brand(), language=LanguagePolicy(),
        hashtags=HashtagPolicy(), content=ContentPolicy(),
        generation=GenerationPolicy(), planning=PlanningPolicy(),
        mentions=MentionPolicy(), media=MediaPolicy(),
        visual=VisualPolicy(), sources=[], links_policy="hide",
        extras={},
    )
    d.update(kw)
    return ChannelStrategy(**d)


class OldCompatTest(unittest.TestCase):
    def test_old_yaml_loads_with_safe_defaults(self):
        from channels.strategy_store import _from_yaml_dict
        s = _from_yaml_dict(OLD_YAML)
        self.assertEqual(s.integration_id, "old-1")
        self.assertEqual(s.brand.tone, "bold")
        from channels.strategy import _MISSING
        self.assertIs(s.brand.colors, _MISSING)
        self.assertIs(s.brand.logo_url, _MISSING)
        self.assertIs(s.language.default, _MISSING)
        self.assertIs(s.language.output_override, _MISSING)

    def test_old_yaml_resolves_with_campaign_fallback(self):
        from channels.strategy_store import _from_yaml_dict
        cfg = resolve_strategy(_project(), _from_yaml_dict(OLD_YAML),
                               channel_meta={"identifier": "facebook"})
        self.assertEqual(cfg["brand"]["colors"], ["#111111"])
        self.assertEqual(cfg["brand"]["logo_url"],
                         "https://cdn.test/logo.png")
        self.assertEqual(cfg["language"]["default"], "ar")
        # Campaign visual identity falls back into the merged
        # profile and reaches the visual builder (Phase 11 rules
        # stay in the text builder by design; only the NEW
        # colors/logo fields are visual-exclusive).
        self.assertIn("#111111", build_visual(cfg))


class RoundtripTest(unittest.TestCase):
    def test_language_colors_logo_roundtrip(self):
        import tempfile
        from channels.strategy_store import save, load
        tmp = tempfile.mkdtemp(prefix="tbra_compat_")
        s = _strategy(
            brand=Brand(tone="bold", colors=["#ff0000", "navy"],
                        logo_url="https://cdn.test/l.png"),
            language=LanguagePolicy(default="en",
                                    output_override="en"),
            visual=VisualPolicy(visual_rules=["no text overlays"],
                                video_rules=["vertical 9:16"]))
        save(s, base_dir=tmp)
        back = load("skill-1", base_dir=tmp)
        self.assertEqual(list(back.brand.colors), ["#ff0000", "navy"])
        self.assertEqual(back.brand.logo_url, "https://cdn.test/l.png")
        self.assertEqual(back.language.default, "en")
        self.assertEqual(back.language.output_override, "en")
        self.assertEqual(list(back.visual.visual_rules),
                         ["no text overlays"])

    def test_invalid_new_fields_fail_closed(self):
        from channels.strategy_store import _from_yaml_dict
        with self.assertRaises(ValueError):
            _from_yaml_dict({"integration_id": "x",
                             "strategy": {"brand": {"colors": "#fff"}}})
        with self.assertRaises(ValueError):
            _from_yaml_dict({"integration_id": "x",
                             "strategy": {"brand": {"logo_url": 7}}})
        with self.assertRaises(ValueError):
            _from_yaml_dict({"integration_id": "x",
                             "strategy": {"language": "en"}})
        with self.assertRaises(ValueError):
            _from_yaml_dict({"integration_id": "x",
                             "strategy": {"language": {"default": 5}}})


class MergeTest(unittest.TestCase):
    def test_channel_override_wins(self):
        s = _strategy(
            brand=Brand(colors=["#00ff00"],
                        logo_url="https://ch.test/l.png"),
            language=LanguagePolicy(default="en"))
        cfg = resolve_strategy(_project(), s,
                               channel_meta={"identifier": "facebook"})
        self.assertEqual(cfg["brand"]["colors"], ["#00ff00"])
        self.assertEqual(cfg["brand"]["logo_url"],
                         "https://ch.test/l.png")
        self.assertEqual(cfg["language"]["default"], "en")

    def test_output_override_wins_with_default_fallback(self):
        s = _strategy(language=LanguagePolicy(output_override="fr"))
        cfg = resolve_strategy(_project(), s,
                               channel_meta={"identifier": "facebook"})
        self.assertEqual(cfg["language"]["default"], "ar")
        self.assertEqual(cfg["language"]["output_override"], "fr")

    def test_no_provider_fields_introduced(self):
        s = _strategy(
            brand=Brand(colors=["#00ff00"],
                        logo_url="https://ch.test/l.png"),
            language=LanguagePolicy(default="en"))
        cfg = resolve_strategy(_project(), s,
                               channel_meta={"identifier": "facebook"})
        blob = repr(cfg)
        for bad in ("api_key", "token", "secret", "credential",
                    "oauth", "password"):
            self.assertNotIn(bad, blob)


class BuilderRoutingTest(unittest.TestCase):
    def _cfg(self):
        s = _strategy(
            brand=Brand(tone="bold", voice="direct",
                        colors=["#ff0000"],
                        logo_url="https://cdn.test/l.png"),
            language=LanguagePolicy(default="en"),
            visual=VisualPolicy(visual_rules=["no text overlays"],
                                video_rules=["vertical 9:16"]))
        return resolve_strategy(_project(), s,
                                channel_meta={"identifier": "facebook"})

    def test_text_builder_has_language_no_visual(self):
        out = build(self._cfg())
        self.assertIn("en", out)
        self.assertIn("direct", out)
        # NEW visual-identity fields never leak into text-side
        # prompts. (Phase 11 visual_rules/video_rules lines stay
        # in build() by design — established behavior, not new.)
        for leak in ("#ff0000", "cdn.test", "Brand colors",
                     "Brand logo"):
            self.assertNotIn(leak, out)

    def test_visual_builder_emits_identity(self):
        out = build_visual(self._cfg())
        for needle in ("#ff0000", "https://cdn.test/l.png",
                       "no text overlays", "vertical 9:16"):
            self.assertIn(needle, out)
        self.assertEqual(build_visual({}), "")
        self.assertEqual(build_visual(None), "")

    def test_image_prompt_receives_visual(self):
        from g2.pipeline import build_package
        from g2.generators import MockImageGenerator, MockTextGenerator
        from contracts import SourceItem
        item = SourceItem(source_id="s1", source_type="website",
                          source_url="https://t.test/x", title="T",
                          text="Body here.", images=[])
        cfg = self._cfg()
        pkg = build_package(item, cfg["brand"], cfg["language"],
                            {"enabled": False}, "hide",
                            MockTextGenerator(), MockImageGenerator(),
                            platform="facebook", policy=cfg)
        prompt = pkg.media[0]["prompt"]
        for needle in ("#ff0000", "no text overlays"):
            self.assertIn(needle, prompt)

    def test_video_plan_receives_visual(self):
        from types import SimpleNamespace
        from research.generation import plan_generation
        cfg = self._cfg()
        plan = plan_generation(SimpleNamespace(
            eligible=True, topic="T", facts=["fact one"],
            media_intent="video"), policy=cfg)
        for needle in ("#ff0000", "https://cdn.test/l.png",
                       "vertical 9:16"):
            self.assertIn(needle, plan.video.prompt)


class PillarGatingTest(unittest.TestCase):
    def _res(self):
        from research.models import MockResearchModel
        from contracts import SourceItem
        item = SourceItem(source_id="s1", source_type="website",
                          source_url="https://t.test/x",
                          title="Mastering tips",
                          text="Mastering basics here.", images=[])
        return MockResearchModel().research([item]), [item]

    def _policy(self, pillars):
        s = _strategy(content=ContentPolicy(content_pillars=pillars))
        return resolve_strategy(_project(), s,
                                channel_meta={"identifier": "facebook"})

    def test_pillars_reach_analysis_from_resolved_cfg(self):
        from research.analysis import MockAnalysisModel
        res, items = self._res()
        ok = MockAnalysisModel().analyze(
            res, self._policy(["mastering tips"]), items)
        self.assertTrue(ok.eligible)
        self.assertIn("pillar=mastering tips", ok.constraints)
        bad = MockAnalysisModel().analyze(
            res, self._policy(["unrelated vertical"]), items)
        self.assertFalse(bad.eligible)
        self.assertIn("pillar-mismatch", bad.meta["reason"])

    def test_no_pillars_stays_eligible(self):
        from research.analysis import MockAnalysisModel
        res, items = self._res()
        out = MockAnalysisModel().analyze(res, self._policy([]), items)
        self.assertTrue(out.eligible)


class SourcesTest(unittest.TestCase):
    def test_rss_youtube_enabled_roundtrip(self):
        import tempfile
        from channels.strategy_store import save, load
        tmp = tempfile.mkdtemp(prefix="tbra_src_")
        srcs = [
            {"type": "website", "url": "https://t.test/", "n": 3},
            {"type": "rss", "url": "https://t.test/feed",
             "n": 5, "source_id": "feed-1"},
            {"type": "youtube",
             "channel_id": "UC" + "A" * 22, "n": 2,
             "enabled": False},
        ]
        s = _strategy(sources=srcs)
        save(s, base_dir=tmp)
        back = load("skill-1", base_dir=tmp)
        self.assertEqual(back.sources, srcs)

    def test_old_website_sources_unchanged(self):
        from channels.strategy_store import _from_yaml_dict
        s = _from_yaml_dict({"integration_id": "o",
                             "strategy": {"sources": [
                                 {"type": "website",
                                  "url": "https://t.test/", "n": 3}]}})
        self.assertEqual(s.sources, [{"type": "website",
                                      "url": "https://t.test/", "n": 3}])

    def test_invalid_sources_fail_closed(self):
        from channels.strategy_store import _from_yaml_dict
        bad = [
            {"sources": [{"type": "rss"}]},
            {"sources": [{"type": "youtube",
                          "channel_id": "not-a-channel"}]},
            {"sources": [{"type": "podcast",
                          "url": "https://t.test/"}]},
            {"sources": [{"type": "rss", "url": "https://t.test/",
                          "n": 0}]},
        ]
        for strat in bad:
            with self.assertRaises(ValueError):
                _from_yaml_dict({"integration_id": "x",
                                 "strategy": strat})

    def test_cycle_skips_disabled_sources(self):
        from research.cycle import run_cycle
        out = run_cycle(
            [{"type": "website", "url": "https://t.test/",
              "n": 1, "enabled": False}],
            policy={"brand": {}, "language": {}})
        self.assertEqual(out.sources, [])
        self.assertEqual(out.items, [])

    def test_legacy_run_rejects_non_website(self):
        import run as run_mod

        class DeadClient:
            def get_integration_settings(self, _id):
                raise RuntimeError("offline")

        cfg = {"brand": {}, "language": {}, "hashtags": {},
               "links_policy": "hide", "schedule": {},
               "sources": [{"type": "rss",
                            "url": "https://t.test/feed", "n": 3}],
               "sources_from": "channel",
               "channel_meta": {"identifier": "facebook"},
               "media_policy": {}}
        rc = run_mod._execute(cfg, "--mock", DeadClient(),
                              "integ-1", "draft", True)
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
