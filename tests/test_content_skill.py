"""Content Skill (Phase 11) tests: provider-agnostic Strategy ->
prompt instructions reach every generation prompt (research,
analysis, text, image, video), mocks stay deterministic, legacy
empty-policy behavior is byte-identical, and invalid/secret-shaped
input fails closed or stays out of prompts."""
import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from channels.instructions import build
from channels.profile import resolve_strategy
from channels.strategy import (Brand, ChannelStrategy, ContentPolicy,
                                GenerationPolicy, HashtagPolicy,
                                MediaPolicy, MentionPolicy,
                                PlanningPolicy, VisualPolicy)
from contracts import SourceItem


def _policy(**kw):
    base = {
        "brand": {"tone": "bold", "audience": "creators",
                  "voice": "direct, no fluff",
                  "banned_words": ["spam"],
                  "preferred_words": ["juzzir"],
                  "cta_style": "Try free"},
        "content": {"content_pillars": ["mastering tips"],
                    "sources_policy": "official sources only"},
        "generation": {"prompt_style": "short punchy lines"},
        "visual": {"visual_rules": ["no text overlays"],
                   "video_rules": ["vertical 9:16"]},
        "language": {"default": "ar"},
    }
    base.update(kw)
    return base


def _item():
    return SourceItem(source_id="s1", source_type="website",
                      source_url="https://www.juzzir.com/x",
                      title="Juzzir tips", text="Mastering basics here.",
                      images=[])


class BuildTest(unittest.TestCase):
    def test_a_empty_policy_yields_empty(self):
        self.assertEqual(build(None), "")
        self.assertEqual(build({}), "")

    def test_b_brand_lines(self):
        out = build(_policy())
        for needle in ("bold", "creators", "direct, no fluff", "spam",
                       "juzzir", "Try free"):
            self.assertIn(needle, out)

    def test_c_content_generation_lines(self):
        out = build(_policy())
        for needle in ("mastering tips", "official sources only",
                       "short punchy lines"):
            self.assertIn(needle, out)

    def test_d_visual_lines(self):
        # Visual rules live ONLY in the visual builder (image/video
        # roles); the text-side builder must not carry them.
        from channels.instructions import build_visual
        out = build(_policy())
        self.assertNotIn("no text overlays", out)
        self.assertNotIn("vertical 9:16", out)
        vis = build_visual(_policy())
        self.assertIn("no text overlays", vis)
        self.assertIn("vertical 9:16", vis)
        self.assertEqual(build_visual(None), "")
        self.assertEqual(build_visual({}), "")

    def test_e_language_override_wins(self):
        self.assertIn("ar", build(_policy()))
        p = _policy(language={"default": "ar",
                              "output_override": "en"})
        self.assertIn("en", build(p))

    def test_o_secret_shaped_keys_never_leak(self):
        p = _policy(credentials={"api_key": "sk-live-xyz"},
                    oauth={"token": "tok-abc"})
        out = build(p)
        self.assertNotIn("sk-live-xyz", out)
        self.assertNotIn("tok-abc", out)


class StrategyRoundtripTest(unittest.TestCase):
    def test_f_voice_visual_survive_store(self):
        import tempfile
        from channels.strategy_store import save, load
        tmp = tempfile.mkdtemp(prefix="tbra_skill_")
        s = ChannelStrategy(
            integration_id="skill-1",
            brand=Brand(tone="bold", voice="direct"),
            hashtags=HashtagPolicy(), content=ContentPolicy(),
            generation=GenerationPolicy(), planning=PlanningPolicy(),
            mentions=MentionPolicy(), media=MediaPolicy(),
            visual=VisualPolicy(visual_rules=["no text overlays"],
                                video_rules=["vertical 9:16"]),
            sources=[], links_policy="hide", extras={})
        save(s, base_dir=tmp)
        loaded = load("skill-1", base_dir=tmp)
        self.assertEqual(loaded.brand.voice, "direct")
        self.assertEqual(list(loaded.visual.visual_rules),
                         ["no text overlays"])
        self.assertEqual(list(loaded.visual.video_rules),
                         ["vertical 9:16"])

    def test_g_resolve_merges_skill_fields(self):
        proj = {"brand": {"tone": "neutral", "audience": "general",
                          "banned_words": [], "preferred_words": [],
                          "cta_style": "Learn more"},
                "language": {"default": "ar"},
                "hashtags": {"enabled": True, "per_platform": {}},
                "links_policy": "hide", "sources": [], "schedule": {}}
        s = ChannelStrategy(
            integration_id="skill-1",
            brand=Brand(tone="bold", voice="direct"),
            hashtags=HashtagPolicy(), content=ContentPolicy(
                content_pillars=["mastering tips"]),
            generation=GenerationPolicy(prompt_style="punchy"),
            planning=PlanningPolicy(), mentions=MentionPolicy(),
            media=MediaPolicy(),
            visual=VisualPolicy(visual_rules=["no text overlays"]),
            sources=[], links_policy="hide", extras={})
        cfg = resolve_strategy(proj, s,
                               channel_meta={"identifier": "facebook"})
        self.assertEqual(cfg["brand"]["voice"], "direct")
        self.assertEqual(cfg["content"]["content_pillars"],
                         ["mastering tips"])
        self.assertEqual(cfg["generation"]["prompt_style"], "punchy")
        self.assertEqual(cfg["visual"]["visual_rules"],
                         ["no text overlays"])

    def test_n_invalid_visual_fails_closed(self):
        from channels.strategy_store import _from_yaml_dict
        with self.assertRaises(ValueError):
            _from_yaml_dict({"integration_id": "skill-1",
                             "strategy": {"visual": {
                                 "visual_rules": "not-a-list"}}})


class FakeAdapter:
    """Captures the prompt; returns a canned envelope."""
    def __init__(self, inner):
        import json
        self.seen = []
        self.payload = {"choices": [{"message": {"content": json.dumps(
            inner)}}]}

    def complete(self, prompt, context, **kw):
        import json
        self.seen.append((prompt, context))
        return json.dumps(self.payload)


def _research_inner():
    return {"summary": "s",
            "findings": [{"statement": "Juzzir launched X",
                          "kind": "fact", "confidence": "high",
                          "item_ids": ["s1"], "excerpt": "q"}],
            "topics": ["mastering"], "entities": []}


def _analysis_inner():
    return {"eligible": True, "topic": "mastering", "angle": "how",
            "rationale": "r", "facts": ["Juzzir launched X"],
            "source_item_ids": ["s1"], "content_format": "post",
            "media_intent": "none", "audience": "creators",
            "language": "ar", "priority": "normal",
            "confidence": "high", "constraints": []}


class LivePromptTest(unittest.TestCase):
    def test_h_skill_reaches_live_prompts(self):
        from research.models import OpenAIResearchModel
        from research.analysis import OpenAIAnalysisModel
        from g2.generators import OpenAITextGenerator
        pol = _policy()
        rm = OpenAIResearchModel(adapter=FakeAdapter(_research_inner()))
        res = rm.research([_item()], pol)
        self.assertTrue(res.findings)
        self.assertIn("direct, no fluff", rm._adapter.seen[0][0])
        am = OpenAIAnalysisModel(adapter=FakeAdapter(_analysis_inner()))
        opp = am.analyze(res, pol, [_item()])
        self.assertTrue(opp.eligible)
        self.assertIn("mastering", am._adapter.seen[0][1])
        self.assertNotIn("no text overlays", am._adapter.seen[0][1])
        tg = OpenAITextGenerator(adapter=FakeAdapter("hello post"))
        tg._adapter.seen = []
        tg.generate("T", "S", pol["brand"], pol)
        self.assertIn("mastering tips", tg._adapter.seen[0][0])

    def test_i_absent_policy_keeps_legacy_prompts(self):
        from research.models import OpenAIResearchModel
        from g2.generators import OpenAITextGenerator
        rm = OpenAIResearchModel(adapter=FakeAdapter(_research_inner()))
        rm.research([_item()])
        rm.research([_item()], {})
        self.assertNotIn("Channel instructions",
                         rm._adapter.seen[0][0])
        self.assertEqual(rm._adapter.seen[0][0], rm._adapter.seen[1][0])
        tg = OpenAITextGenerator(adapter=FakeAdapter("hi"))
        tg.generate("T", "S", {}, None)
        tg.generate("T", "S", {}, {})
        self.assertEqual(tg._adapter.seen[0][0], tg._adapter.seen[1][0])

    def test_j_mock_parity_with_policy(self):
        from research.models import MockResearchModel
        from research.analysis import MockAnalysisModel
        from g2.generators import MockTextGenerator
        pol = _policy()
        item = _item()
        r1 = MockResearchModel().research([item])
        r2 = MockResearchModel().research([item], pol)
        self.assertEqual(r1.summary, r2.summary)
        self.assertEqual(len(r1.findings), len(r2.findings))
        a1 = MockAnalysisModel().analyze(r1, {"brand": {}, "language": {},
                                              "pillars": []}, [item])
        a2 = MockAnalysisModel().analyze(r2, {"brand": {}, "language": {},
                                              "pillars": []}, [item])
        self.assertEqual(a1.eligible, a2.eligible)
        t1 = MockTextGenerator().generate("T", "S", {"tone": "b"})
        t2 = MockTextGenerator().generate("T", "S", {"tone": "b"}, pol)
        self.assertEqual(t1, t2)


class PlanPipelineTest(unittest.TestCase):
    def test_k_plan_prompts_carry_skill(self):
        from research.generation import plan_generation
        pol = _policy()
        img = plan_generation(SimpleNamespace(
            eligible=True, topic="mastering",
            facts=["Juzzir launched X"], media_intent="image"),
            policy=pol)
        self.assertIn("no text overlays", img.image.prompt)
        self.assertNotIn("vertical 9:16", img.image.prompt)
        self.assertNotIn("Channel instructions", img.image.prompt)
        self.assertTrue(img.image.prompt.startswith(
            "mastering :: Juzzir launched X"))
        vid = plan_generation(SimpleNamespace(
            eligible=True, topic="mastering",
            facts=["Juzzir launched X"], media_intent="video"),
            policy=pol)
        self.assertIn("vertical 9:16", vid.video.prompt)
        self.assertIn("no text overlays", vid.video.prompt)
        self.assertNotIn("Channel instructions", vid.video.prompt)
        bare = plan_generation(SimpleNamespace(
            eligible=True, topic="mastering",
            facts=["Juzzir launched X"], media_intent="image"))
        self.assertEqual(bare.image.prompt,
                         "mastering :: Juzzir launched X")

    def test_l_pipeline_threads_policy(self):
        from g2.pipeline import build_package
        from g2.generators import MockImageGenerator, MockTextGenerator
        seen = {}

        class RecText(MockTextGenerator):
            def generate(self, title, summary, brand, policy=None):
                seen["policy"] = policy
                return super().generate(title, summary, brand, policy)

        pol = _policy()
        pkg = build_package(_item(), pol["brand"], pol["language"],
                            {"enabled": False}, "hide",
                            RecText(), MockImageGenerator(),
                            platform="facebook", policy=pol)
        self.assertIs(seen["policy"], pol)
        self.assertIn("Mastering", pkg.content)
        self.assertEqual(pkg.media[0]["kind"], "generator")
        self.assertIn("no text overlays", pkg.media[0]["prompt"])

    def test_m_full_chain_mock_end_to_end(self):
        from research.models import MockResearchModel
        from research.analysis import MockAnalysisModel
        from research.generation import (plan_generation,
                                         shape_item_for_g2)
        from g2.pipeline import build_package
        from g2.generators import MockTextGenerator
        pol = _policy()
        item = _item()
        res = MockResearchModel().research([item], pol)
        opp = MockAnalysisModel().analyze(
            res, {"brand": pol["brand"], "language": pol["language"],
                  "pillars": []}, [item])
        self.assertTrue(opp.eligible)
        plan = plan_generation(opp, [item], policy=pol)
        pkg = build_package(shape_item_for_g2(item, plan),
                            pol["brand"], pol["language"],
                            {"enabled": False}, "hide",
                            MockTextGenerator(), None,
                            platform="facebook", policy=pol)
        self.assertTrue(pkg.content)


class RoutingTest(unittest.TestCase):
    """Phase 14A §6: visual identity reaches image/video only;
    research/analysis/text prompts never carry visual data."""

    def test_visual_rules_not_in_research(self):
        from research.models import OpenAIResearchModel
        pol = _policy()
        rm = OpenAIResearchModel(adapter=FakeAdapter(_research_inner()))
        rm.research([_item()], pol)
        prompt, context = rm._adapter.seen[0]
        self.assertNotIn("no text overlays", prompt + context)
        self.assertNotIn("vertical 9:16", prompt + context)
        self.assertNotIn("Brand colors", prompt + context)

    def test_visual_rules_not_in_analysis(self):
        from research.models import OpenAIResearchModel
        from research.analysis import OpenAIAnalysisModel
        pol = _policy()
        rm = OpenAIResearchModel(adapter=FakeAdapter(_research_inner()))
        res = rm.research([_item()], pol)
        am = OpenAIAnalysisModel(adapter=FakeAdapter(_analysis_inner()))
        am.analyze(res, pol, [_item()])
        prompt, context = am._adapter.seen[0]
        self.assertNotIn("no text overlays", prompt + context)
        self.assertNotIn("vertical 9:16", prompt + context)
        self.assertNotIn("Brand colors", prompt + context)

    def test_visual_rules_not_in_text(self):
        from g2.generators import OpenAITextGenerator
        pol = _policy()
        tg = OpenAITextGenerator(adapter=FakeAdapter("hello post"))
        tg.generate("T", "S", pol["brand"], pol)
        prompt, _ = tg._adapter.seen[0]
        self.assertNotIn("no text overlays", prompt)
        self.assertNotIn("Brand colors", prompt)

    def test_video_rules_not_in_text_but_in_video(self):
        from g2.generators import OpenAITextGenerator
        from research.generation import plan_generation
        pol = _policy()
        tg = OpenAITextGenerator(adapter=FakeAdapter("hello post"))
        tg.generate("T", "S", pol["brand"], pol)
        self.assertNotIn("vertical 9:16", tg._adapter.seen[0][0])
        vid = plan_generation(SimpleNamespace(
            eligible=True, topic="T", facts=["f"],
            media_intent="video"), policy=pol)
        self.assertIn("vertical 9:16", vid.video.prompt)

    def test_visual_rules_reach_image_prompts(self):
        from g2.pipeline import build_package
        from g2.generators import MockImageGenerator, MockTextGenerator
        pol = _policy()
        pkg = build_package(_item(), pol["brand"], pol["language"],
                            {"enabled": False}, "hide",
                            MockTextGenerator(), MockImageGenerator(),
                            platform="facebook", policy=pol)
        self.assertIn("no text overlays", pkg.media[0]["prompt"])
        self.assertNotIn("vertical 9:16", pkg.media[0]["prompt"])
        self.assertNotIn("Channel instructions",
                         pkg.media[0]["prompt"])

    def test_visual_builder_splits_image_and_video(self):
        from channels.instructions import build_visual
        pol = _policy()
        img = build_visual(pol, video_rules=False)
        self.assertIn("no text overlays", img)
        self.assertNotIn("vertical 9:16", img)
        vid = build_visual(pol, video_rules=True)
        self.assertIn("no text overlays", vid)
        self.assertIn("vertical 9:16", vid)


if __name__ == "__main__":
    unittest.main()
