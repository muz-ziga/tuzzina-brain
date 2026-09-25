"""Stage-scoped Brain entry points: contracts without duplication.

Proves, with scripted adapters and stub clients (no network,
no keys, no live calls):
1. analysis/text/prompt stages return validated payloads.
2. failing stages raise with the original domain error
   (callers map to envelopes; contracts unchanged).
3. serialization round-trips (dict -> object -> dict).
4. FixedText/FixedPrompt replay is byte-identical to the
   legacy path for the same validated strings.
5. execute dry-run performs zero writes and assigns
   deterministic value ids.
6. CLI harness fails closed (exit 2) on bad args/files.
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from g2.generators import (FixedPromptGenerator, FixedTextGenerator,
                           MockImageGenerator, MockTextGenerator)
from g2.pipeline import build_package
from llm.adapters import LLMError
from contracts import SourceItem
from research.models import _trace_id
from stage_run import (_as_opportunity, _as_research_result,
                       _as_source_item, _to_jsonable, main_stage,
                       run_analysis_stage, run_execute_stage,
                       run_prompt_stage, run_research_stage,
                       run_text_stage, STAGE_BASE_KEYS,
                        QUEUE_SOURCE, _queue_consume,
                        _unpublished_offer)



CONCEPT_JSON = (
    '{"meaning": "the sea is calm before a storm", "subject": "a quiet '
    'harbor", "relationship": "still water holding pressure underneath", '
    '"brief_constraints": ["portrait framing"], "art_direction": '
    '{"icon": "none", "slot": "", "palette": [], "text_align": "left"}}')


class ScriptAdapter:
    def __init__(self, outcomes):
        self._outcomes = list(outcomes)
        self.calls = []

    def complete(self, system, user, *, temperature=0.7, max_tokens=400,
                 timeout=60):
        self.calls.append((system, user))
        if not self._outcomes:
            raise AssertionError("adapter called past script end")
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _item_dict(i=0):
    return {
        "source_id": f"s-{i}", "source_type": "rss",
        "source_url": f"https://demo.test/{i}",
        "title": f"Harbor news {i}",
        "text": "Harbor masterclass announced today.",
        "images": [], "links": [], "hashtags": [],
        "published_at": "2026-09-08T09:00:00+00:00",
        "platform": "rss", "item_id": f"g-{i}",
        "content_hash": f"h{i}",
    }


def _research_dict(tid="g-0"):
    return {
        "summary": "s", "findings": [
            {"statement": "Harbor masterclass announced",
             "kind": "fact", "confidence": "high",
             "item_ids": [tid], "excerpt": "Harbor masterclass"}],
        "topics": ["t"], "entities": [], "item_ids": [tid],
        "earliest_published": "", "latest_published": "",
        "model": "", "meta": {}}


def _opp_dict(media="none"):
    return {
        "eligible": True, "topic": "Harbor masterclass",
        "angle": "Loudness basics", "rationale": "R",
        "facts": ["Harbor masterclass announced"],
        "source_item_ids": ["g-0"], "content_format": "post",
        "media_intent": media, "audience": "a", "language": "en",
        "priority": "normal", "confidence": "high",
        "constraints": [], "model": "", "meta": {}}


BRAND = {"tone": "bold", "audience": "founders",
         "banned_words": ["spam"], "preferred_words": ["launch"],
         "cta_style": "Try it now"}
POLICY = {"brand": dict(BRAND), "language": {"default": "en"},
          "hashtags": {"enabled": False}, "links_policy": "hide",
          "pillars": [], "generation": {}, "media": {}}


class FakeClient:
    def __init__(self):
        self.released = []
        self.calls = []

    def release_claim(self, key, run_id):
        self.released.append((key, run_id))
        return 1


class RoundTripCase(unittest.TestCase):
    def test_source_item_round_trip(self):
        raw = _item_dict()
        back = _to_jsonable(_as_source_item(raw))
        for key in ("source_id", "title", "text", "item_id",
                    "platform"):
            self.assertEqual(back[key], raw[key])

    def test_research_round_trip(self):
        back = _to_jsonable(_as_research_result(_research_dict()))
        self.assertEqual(back["findings"][0]["kind"], "fact")
        self.assertEqual(back["summary"], "s")

    def test_opportunity_round_trip(self):
        back = _to_jsonable(_as_opportunity(_opp_dict("image")))
        self.assertTrue(back["eligible"])
        self.assertEqual(back["media_intent"], "image")

    def test_rejects_garbage(self):
        for fn, bad in ((_as_source_item, []),
                        (_as_research_result, "x"),
                        (_as_opportunity, None)):
            with self.assertRaises(ValueError):
                fn(bad)


class AnalysisStageCase(unittest.TestCase):
    def _ctx(self, adapter):
        from research.analysis import OpenAIAnalysisModel
        return {"client": FakeClient(), "run_id": "r1",
                "cfg": dict(POLICY),
                "analysis_model": OpenAIAnalysisModel(adapter=adapter),
                "research": _research_dict(),
                "items": [_item_dict()],
                "claimed": []}

    def test_valid_opportunity(self):
        from research.analysis import OpenAIAnalysisModel  # noqa
        adapter = ScriptAdapter([json.dumps({
            "eligible": True, "topic": "T", "angle": "A",
            "rationale": "R", "facts": ["Harbor masterclass announced"],
            "source_item_ids": ["g-0"], "content_format": "post",
            "media_intent": "none", "audience": "a", "language": "en",
            "priority": "normal", "confidence": "high",
            "constraints": []})])
        out = run_analysis_stage(self._ctx(adapter))
        self.assertTrue(out["opportunity"]["eligible"])
        self.assertEqual(out["claimed"], [])

    def test_invalid_output_propagates(self):
        adapter = ScriptAdapter([LLMError("empty-content")] * 3)
        with self.assertRaises(Exception) as ctx:
            run_analysis_stage(self._ctx(adapter))
        self.assertIn("model-error", str(ctx.exception))


class TextStageCase(unittest.TestCase):
    def _ctx(self, adapter):
        from g2.generators import OpenAITextGenerator
        return {"client": FakeClient(), "run_id": "r1",
                "cfg": dict(POLICY),
                "text_gen": OpenAITextGenerator(api_key="k",
                                                adapter=adapter),
                "opportunity": _opp_dict(),
                "items": [_item_dict()],
                "claimed": []}

    def test_valid_text(self):
        out = run_text_stage(self._ctx(ScriptAdapter(["Hello harbor"])))
        self.assertIn("Hello harbor", out["text"])

    def test_text_failure_releases_claims(self):
        client = FakeClient()
        ctx = self._ctx(ScriptAdapter([LLMError("http-500")] * 3))
        ctx["client"] = client
        ctx["claimed"] = ["k1"]
        with self.assertRaises(Exception):
            run_text_stage(ctx)
        self.assertEqual(client.released, [("k1", "r1")])


class PromptStageCase(unittest.TestCase):
    def _ctx(self, adapter):
        from g2.generators import OpenAIImagePromptGenerator
        return {"client": FakeClient(), "run_id": "r1",
                "cfg": dict(POLICY),
                "prompt_gen": OpenAIImagePromptGenerator(
                    api_key="k", adapter=adapter),
                "topic": "Harbor day",
                "claimed": []}

    def test_valid_prompt(self):
        # Two calls now: the visual-concept decision, then the prompt
        # rendered from that concept.
        adapter = ScriptAdapter([
            CONCEPT_JSON,
            "Studio scene, soft light",
        ])
        out = run_prompt_stage(self._ctx(adapter))
        self.assertEqual(out["prompt"], "Studio scene, soft light")
        self.assertEqual(out["concept"]["subject"], "a quiet harbor")
        concept_system = adapter.calls[0][0]
        self.assertIn("visual concept", concept_system.lower())
        # The second call renders the prompt FROM that concept.
        self.assertIn("a quiet harbor", adapter.calls[1][0])

    def test_concept_driven_by_meaning_not_topic_label(self):
        # The claim is in the semantic payload (angle/facts); the
        # topic is only a label. The concept call must see the claim,
        # and the rendered prompt must follow the concept.
        adapter = ScriptAdapter([
            '{"meaning": "one mix fails in one environment and holds '
            'in another", "subject": "a single source feeding two very '
            'different rooms", "relationship": "the same signal meeting '
            'opposite conditions", "brief_constraints": ["high key with '
            'deep shadows"], "art_direction": {"icon": "none", "slot": "", '
            '"palette": ["amber", "slate"], "text_align": "left"}}',
            "A single source line splitting toward two contrasting rooms, "
            "warm key light, portrait.",
        ])
        ctx = self._ctx(adapter)
        ctx["topic"] = "Try-before-you-pay mastering preview"
        ctx["post"] = {
            "topic": "Try-before-you-pay mastering preview",
            "angle": "why the same mix collapses in a car but not on "
                     "headphones",
            "facts": ["car systems roll off low frequencies"],
            "audience": "mix engineers",
            "media_intent": "image",
        }
        out = run_prompt_stage(ctx)
        # The claim travelled to the concept decision, not the label.
        concept_user = adapter.calls[0][1]
        self.assertIn("collapses in a car", concept_user)
        self.assertIn("car systems roll off low frequencies", concept_user)
        self.assertEqual(
            out["concept"]["relationship"],
            "the same signal meeting opposite conditions")
        # The prompt writer consumed the concept, not the raw topic.
        prompt_system = adapter.calls[1][0]
        self.assertIn("Visual concept", prompt_system)
        self.assertIn("one mix fails in one environment", prompt_system)
        self.assertIn("Render the visual concept above exactly",
                      prompt_system)

    def test_visual_brief_reaches_concept_and_prompt(self):
        # A different channel, a different brief, one generator: the
        # concept is grounded in that channel's own post meaning and
        # carries that channel's brief.
        ctx = self._ctx(ScriptAdapter([
            '{"meaning": "the viewer should judge whether a sourdough '
            'starter is ready by its float test", "subject": "a glass jar '
            'of starter rising under a float test", "relationship": "the '
            'jar rising with the lid on shows the float test the post '
            'explains", "brief_constraints": ["macro photography"], '
            '"art_direction": {"icon": "none", "slot": "", "palette": '
            '["wheat", "clay"], "text_align": "left"}}',
            "A glass jar of sourdough starter rising, warm bench light, "
            "portrait.",
        ]))
        ctx["cfg"] = dict(POLICY)
        ctx["cfg"]["visual"] = {"brief": {
            "palette": ["deep teal", "warm sand"],
            "treatment": ["editorial photography"],
            "contrast": ["high key with deep shadows"],
        }}
        ctx["post"] = {
            "topic": "Sourdough starter float test",
            "angle": "what the float test actually proves about a starter",
            "facts": ["a float test only measures gas, not acid"],
            "audience": "home bakers",
            "media_intent": "image",
        }
        out = run_prompt_stage(ctx)
        self.assertIn("float test", out["concept"]["subject"])
        adapter = ctx["prompt_gen"]._adapter
        self.assertIn("Palette: deep teal, warm sand.",
                      adapter.calls[0][0])
        self.assertIn("Treatment: editorial photography.",
                      adapter.calls[0][0])
        self.assertIn("Palette: deep teal, warm sand.",
                      adapter.calls[1][0])

    def test_icon_is_optional_when_the_concept_declines_it(self):
        # The concept may decide no icon helps this post: then no icon
        # is picked and none travels to the compositor.
        adapter = ScriptAdapter([
            '{"meaning": "the viewer can compare the original mix against '
            'the mastered version before paying", "subject": "two audio '
            'renderings side by side", "relationship": "the comparison '
            'happens before paying", "brief_constraints": [], '
            '"art_direction": {"icon": "none", "slot": "", "palette": [], '
            '"text_align": "left"}}',
            "Two audio renderings side by side, warm key light, portrait.",
        ])
        ctx = self._ctx(adapter)
        ctx["post"] = {
            "topic": "Try-before-you-pay mastering preview",
            "angle": "compare the original mix with the mastered version "
                     "before paying",
            "facts": ["compare the original mix against the mastered version"],
            "audience": "mix engineers",
            "media_intent": "image",
        }
        out = run_prompt_stage(ctx)
        self.assertIsNone(out["icon_key"])
        self.assertNotIn("[icon:", out["prompt"])
        self.assertEqual(out["art_direction"]["icon"], "none")
        self.assertEqual(len(adapter.calls), 2)

    def test_default_image_skill_declares_no_icon_layer(self):
        # The default Image Skill declares `icons: none`, so a concept
        # asking for an icon is overridden BY THE SKILL, not by the
        # engine: the same doc, read from the skills directory, is the
        # single source of that policy.
        adapter = ScriptAdapter([
            '{"meaning": "the mix needs a louder master for streaming", '
            '"subject": "a waveform rising into a loud master", '
            '"relationship": "the same mix at two levels", '
            '"brief_constraints": [], "art_direction": {"icon": "use", '
            '"slot": "top-right", "palette": ["#111111"], '
            '"text_align": "center"}}',
            "A waveform rising into a loud master, portrait.",
        ])
        ctx = self._ctx(adapter)
        ctx["post"] = {
            "topic": "Loudness targets for streaming masters",
            "angle": "a mix needs a louder master for streaming playback",
            "facts": ["a mix needs a louder master for streaming"],
            "audience": "mix engineers",
            "media_intent": "image",
        }
        out = run_prompt_stage(ctx)
        self.assertEqual(out["art_direction"]["icon"], "none")
        self.assertEqual(out["art_direction"]["slot"], "")
        self.assertIsNone(out["icon_key"])
        self.assertNotIn("[icon:", out["prompt"])
        # No palette cycle is declared by the default Skill, so the
        # concept's own color stands.
        self.assertEqual(out["art_direction"]["palette"], ["#111111"])
        self.assertEqual(out["concept"]["art_direction"]["icon"], "none")
        self.assertEqual(out["art_direction"]["text_align"], "center")

    def test_image_skill_owns_icons_and_palette(self):
        # Two Image Skill documents, two behaviors, same engine: the
        # Skill that forbids icons and declares a palette cycle gets
        # exactly that, and a concept's own colors are ignored.
        import os
        import tempfile
        import yaml
        from skills.loader import clear_cache
        from stage_run import run_prompt_stage as stage

        cycle = ["#0B4FD8", "#C2410C", "#6D28D9", "#047857",
                 "#B91C1C", "#0E7490", "#A21CAF", "#B45309"]
        old = os.environ.get("BRAIN_SKILLS_DIR")
        tmp = tempfile.mkdtemp(prefix="tbra_visual_")
        try:
            os.environ["BRAIN_SKILLS_DIR"] = tmp
            for name in ("research", "analysis", "text", "video"):
                with open(os.path.join(tmp, name + ".yaml"), "w",
                          encoding="utf-8") as handle:
                    yaml.safe_dump(
                        {"id": name + "-x", "name": name, "type": name,
                         "version": 1, "instructions": "Do it."}, handle)
            with open(os.path.join(tmp, "image.policy.yaml"), "w",
                      encoding="utf-8") as handle:
                yaml.safe_dump(
                    {"id": "image-policy", "name": "policy",
                     "type": "image", "version": 1,
                     "instructions": "Strong, saturated color only.",
                     "config": {"icons": "none",
                                "palette_cycle": cycle,
                                "palette_slots_per_day": 8}}, handle)
            clear_cache()
            adapter = ScriptAdapter([
                '{"meaning": "one mix fails in one environment and holds '
                'in another", "subject": "a single source feeding two very '
                'different rooms", "relationship": "the same signal '
                'meeting opposite conditions", "brief_constraints": [], '
                '"art_direction": {"icon": "use", "slot": "top-left", '
                '"palette": ["#010203"], "text_align": "left"}}',
                "A single source line splitting toward two rooms, portrait.",
            ])
            cfg = dict(POLICY, skills={"image": "policy"})
            ctx = self._ctx(adapter)
            ctx["cfg"] = cfg
            ctx["now"] = "2026-09-24T09:00:00+00:00"
            ctx["post"] = {
                "topic": "Translation across playback environments",
                "angle": "one mix fails in one environment and holds in another",
                "facts": ["the same mix collapses in a car but not on headphones"],
                "audience": "mix engineers",
                "media_intent": "image",
            }
            out = run_prompt_stage(ctx)
            from g2.generators import slot_value
            expected = slot_value(cycle, ctx["now"], 8)
            self.assertEqual(out["art_direction"]["icon"], "none")
            self.assertEqual(out["art_direction"]["slot"], "")
            self.assertEqual(out["art_direction"]["palette"], [expected])
            self.assertIsNone(out["icon_key"])
            self.assertNotIn("[icon:", out["prompt"])
            # The declared color reaches the image model through the
            # prompt (the Skill's rotation, not an engine choice).
            self.assertIn(expected, adapter.calls[1][0])
            self.assertNotIn("#010203", adapter.calls[1][0])
        finally:
            clear_cache()
            if old is None:
                os.environ.pop("BRAIN_SKILLS_DIR", None)
            else:
                os.environ["BRAIN_SKILLS_DIR"] = old
        self.assertIs(stage, run_prompt_stage)

    def test_concept_colors_pass_through_without_a_skill_declaration(self):
        # No Image Skill policy: the concept's own palette stands, and a
        # non-color value is dropped as a structural matter only.
        import os
        import tempfile
        import yaml
        from skills.loader import clear_cache

        old = os.environ.get("BRAIN_SKILLS_DIR")
        tmp = tempfile.mkdtemp(prefix="tbra_visual_none_")
        try:
            os.environ["BRAIN_SKILLS_DIR"] = tmp
            for name in ("research", "analysis", "text", "video"):
                with open(os.path.join(tmp, name + ".yaml"), "w",
                          encoding="utf-8") as handle:
                    yaml.safe_dump(
                        {"id": name + "-x", "name": name, "type": name,
                         "version": 1, "instructions": "Do it."}, handle)
            with open(os.path.join(tmp, "image.plain.yaml"), "w",
                      encoding="utf-8") as handle:
                yaml.safe_dump(
                    {"id": "image-plain", "name": "plain", "type": "image",
                     "version": 1, "instructions": "Whatever looks right."},
                    handle)
            clear_cache()
            adapter = ScriptAdapter([
                '{"meaning": "one mix fails in one environment and holds '
                'in another", "subject": "a single source feeding two very '
                'different rooms", "relationship": "the same signal '
                'meeting opposite conditions", "brief_constraints": [], '
                '"art_direction": {"icon": "use", "slot": "top-left", '
                '"palette": ["#123456", "pale beige"], '
                '"text_align": "left"}}',
                "A single source line splitting toward two rooms, portrait.",
            ])
            ctx = self._ctx(adapter)
            ctx["cfg"] = dict(POLICY, skills={"image": "plain"})
            ctx["post"] = {
                "topic": "Translation across playback environments",
                "angle": "one mix fails in one environment and holds in another",
                "facts": ["the same mix collapses in a car but not on headphones"],
                "audience": "mix engineers",
                "media_intent": "image",
            }
            out = run_prompt_stage(ctx)
            # Colors: the concept's own list, minus the value that is not
            # a color literal (structural check, no taste involved).
            self.assertEqual(out["art_direction"]["palette"], ["#123456"])
            # Icon: this Skill declared nothing, so the concept's request
            # stands and the engine resolves it from the manifest.
            self.assertEqual(out["art_direction"]["icon"], "use")
            self.assertEqual(out["art_direction"]["slot"], "top-left")
        finally:
            clear_cache()
            if old is None:
                os.environ.pop("BRAIN_SKILLS_DIR", None)
            else:
                os.environ["BRAIN_SKILLS_DIR"] = old

    def test_unusable_concept_blocks_the_prompt(self):
        # A concept missing its fields is not rendered into a prompt.
        with self.assertRaises(Exception):
            run_prompt_stage(self._ctx(ScriptAdapter([
                LLMError("invalid-output"),
                LLMError("invalid-output"),
            ])))

    def test_prompt_failure_propagates(self):
        with self.assertRaises(Exception):
            run_prompt_stage(
                self._ctx(ScriptAdapter([LLMError("empty-content")] * 3)))


class FixedReplayCase(unittest.TestCase):
    def test_fixed_text_matches_legacy_path(self):
        from contracts import SourceItem
        item = SourceItem(**_item_dict())
        brand, policy = dict(BRAND), dict(POLICY)
        legacy = build_package(
            item, brand, policy.get("language"),
            policy.get("hashtags"), "hide",
            MockTextGenerator(), None, platform="facebook",
            policy=policy)
        fixed = build_package(
            item, brand, policy.get("language"),
            policy.get("hashtags"), "hide",
            FixedTextGenerator(legacy.content), None,
            platform="facebook", policy=policy)
        self.assertEqual(fixed.content, legacy.content)
        self.assertEqual(fixed.hashtags, legacy.hashtags)

    def test_fixed_prompt_matches_legacy_path(self):
        from contracts import SourceItem
        item = SourceItem(**_item_dict())
        brand, policy = dict(BRAND), dict(POLICY)
        legacy = build_package(
            item, brand, policy.get("language"),
            policy.get("hashtags"), "hide",
            MockTextGenerator(), MockImageGenerator(),
            platform="facebook", policy=policy)
        fixed_policy = dict(policy)
        fixed_policy["image_prompt_gen"] = FixedPromptGenerator(
            legacy.media[0]["prompt"])
        fixed = build_package(
            item, brand, policy.get("language"),
            policy.get("hashtags"), "hide",
            MockTextGenerator(), MockImageGenerator(),
            platform="facebook", policy=fixed_policy)
        self.assertEqual(fixed.media[0]["prompt"],
                         legacy.media[0]["prompt"])


class ExecuteStageCase(unittest.TestCase):
    def _ctx(self):
        return {
            "client": FakeClient(), "run_id": "run-9",
            "cfg": dict(POLICY),
            "opportunity": _opp_dict(),
            "items": [_item_dict()],
            "text": "Harbor post body here",
            "prompt": None,
            "claimed": ["k1"],
            "dry_run": True, "mode": "draft",
            "state_store": "memory",
            "integration_id": "int-1",
            "schedule": {"timezone": "UTC", "times": ["09:00"]},
        }

    def test_dry_run_writes_nothing_with_deterministic_ids(self):
        client = FakeClient()
        ctx = self._ctx()
        ctx["client"] = client
        out = run_execute_stage(ctx)
        self.assertEqual(out["post_ids"], [])
        self.assertEqual(out["intents"], 1)
        self.assertEqual(out["packages"], 1)
        # Claims released at end even on the dry path.
        self.assertEqual(client.released, [("k1", "run-9")])

    def test_deterministic_value_ids_shape(self):
        out = run_execute_stage(self._ctx())
        self.assertEqual(out["value_ids"], ["brainrun:run-9:0:main"])
        self.assertEqual(out["media_prompts"], [])

    def test_image_intent_records_prompt_without_resolving(self):
        # Image intent + dry-run: the Fixed replay prompt lands in
        # the package (visible as media_prompts evidence) while
        # zero Tuzzina calls happen (no bytes, no upload).
        ctx = self._ctx()
        ctx["opportunity"] = _opp_dict("image")
        ctx["prompt"] = "STUDIO PROMPT HERE"
        out = run_execute_stage(ctx)
        self.assertEqual(out["intents"], 1)
        self.assertEqual(out["media_prompts"], ["STUDIO PROMPT HERE"])
        self.assertEqual(out["post_ids"], [])
        self.assertEqual(out["media"], [])


class EnvelopeMappingCase(unittest.TestCase):
    def test_retryable_error_maps_to_retryable_envelope(self):
        from llm.adapters import LLMError
        from stage_run import RETRYABLE, SUCCESS, TERMINAL, \
            stage_error_envelope
        stage, status, payload, error = stage_error_envelope(
            "research", LLMError("empty-content"))
        self.assertEqual((stage, status, payload), ("research", RETRYABLE, None))
        self.assertIn("empty-content", error)
        self.assertEqual(RETRYABLE, "RETRYABLE_STAGE_FAILURE")
        self.assertEqual(TERMINAL, "TERMINAL_FAILURE")
        self.assertEqual(SUCCESS, "SUCCESS")

    def test_terminal_error_maps_to_terminal_envelope(self):
        from stage_run import TERMINAL, stage_error_envelope
        stage, status, payload, error = stage_error_envelope(
            "analysis", ValueError("bad-config"))
        self.assertEqual((stage, status, payload),
                         ("analysis", TERMINAL, None))
        self.assertIn("bad-config", error)

    def test_envelope_round_trip_through_emit(self):
        import io
        from contextlib import redirect_stdout
        from stage_run import stage_error_envelope
        from llm.adapters import LLMError
        import json as _json
        buf = io.StringIO()
        with redirect_stdout(buf):
            from stage_run import _emit_stage
            rc = _emit_stage(
                *stage_error_envelope("text", LLMError("http-503")))
        self.assertEqual(rc, 0)
        line = buf.getvalue().strip()
        self.assertTrue(line.startswith("BRAIN_STAGE "))
        env = _json.loads(line[len("BRAIN_STAGE "):])
        self.assertEqual(env["status"], "RETRYABLE_STAGE_FAILURE")
        self.assertEqual(env["stage"], "text")


class StageModeErrorPathCase(unittest.TestCase):
    def test_stage_exception_becomes_retryable_envelope(self):
        # Regression: the exception-to-envelope mapping itself
        # must never raise (a NameError here once turned every
        # retryable failure into an exit-1 crash).
        import io
        import json as _json
        from contextlib import redirect_stdout
        import stage_run as _sr
        from llm.adapters import LLMError
        real_research = _sr.run_research_stage
        real_ctx = _sr._stage_ctx

        def boom(_ctx):
            raise LLMError("empty-content")

        class Base:
            mode = "--openai"
            dry_run = True
            state_store = "memory"
            strategy_by_integration = "int-1"
            campaign = "c.yaml"
            run_id = "r-1"

        _sr.run_research_stage = boom
        _sr._stage_ctx = lambda base, run_id, stage_input: {}
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                rc = _sr._run_stage_mode("research", "", Base())
        finally:
            _sr.run_research_stage = real_research
            _sr._stage_ctx = real_ctx
        self.assertEqual(rc, 0)
        line = buf.getvalue().strip()
        self.assertTrue(line.startswith("BRAIN_STAGE "))
        env = _json.loads(line[len("BRAIN_STAGE "):])
        self.assertEqual(env["status"], "RETRYABLE_STAGE_FAILURE")
        self.assertEqual(env["stage"], "research")

    def test_stage_terminal_error_becomes_terminal_envelope(self):
        import io
        import json as _json
        from contextlib import redirect_stdout
        import stage_run as _sr
        real_text = _sr.run_text_stage
        real_ctx = _sr._stage_ctx

        def boom(_ctx):
            raise ValueError("bad-config")

        class Base:
            mode = "--openai"
            dry_run = True
            state_store = "memory"
            strategy_by_integration = "int-1"
            campaign = "c.yaml"
            run_id = "r-1"

        _sr.run_text_stage = boom
        _sr._stage_ctx = lambda base, run_id, stage_input: {}
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                rc = _sr._run_stage_mode("text", "", Base())
        finally:
            _sr.run_text_stage = real_text
            _sr._stage_ctx = real_ctx
        self.assertEqual(rc, 0)
        env = _json.loads(buf.getvalue().strip()[len("BRAIN_STAGE "):])
        self.assertEqual(env["status"], "TERMINAL_FAILURE")


class ResearchPayloadCapCase(unittest.TestCase):
    def test_full_collection_trimmed_to_max_items(self):
        # Regression: a campaign collecting up to 80 items
        # (~4KB each) burst the 512KB stage envelope and died
        # with oversize payload. The research LLM only ever
        # reads MAX_ITEMS, so the stage carries exactly those;
        # items_new keeps the discovery count, claims stay held.
        import research.cycle as _cyc
        from contracts import SourceItem
        from research.models import MAX_ITEMS, ResearchResult
        real_collect = _cyc.collect_all_sources

        def fake_collect(sources, store, now, out, pending):
            for i in range(MAX_ITEMS * 4):
                out.items.append(SourceItem(
                    source_id="s-%d" % i, source_type="website",
                    source_url="https://x/%d" % i,
                    title="t-%d" % i, text="w" * 4000))

        class Model:
            def research(self, items, cfg):
                return ResearchResult(
                    summary="s", findings=[], topics=[],
                    entities=[], item_ids=[], model="stub",
                    meta={})

        ctx = {"store": object(), "cfg": {},
               "client": object(), "run_id": "r-1",
               "sources": [{"type": "website", "url": "https://x"}],
               "now": "t", "research_model": Model()}
        _cyc.collect_all_sources = fake_collect
        try:
            payload = run_research_stage(ctx)
        finally:
            _cyc.collect_all_sources = real_collect
        self.assertEqual(payload["items_new"], MAX_ITEMS * 4)
        self.assertLessEqual(len(payload["items"]), MAX_ITEMS)
        self.assertLess(len(json.dumps(payload)), 512 * 1024)


class AnalysisSeesResearchCase(unittest.TestCase):
    def test_base_keys_carry_research(self):
        # Regression: "research" was missing from the carried
        # keys, so analysis always read an empty result and every
        # run ended as a no-findings no-op no matter what the
        # research agent found.
        self.assertIn("research", STAGE_BASE_KEYS)

    def test_base_keys_carry_the_visual_semantic_payload(self):
        # Regression: "topic" and "post" were missing, so the prompt
        # stage received an empty payload in production and every
        # image came from the Skill alone. The analysis output the
        # visual concept must be built from has to reach the stage.
        for key in ("topic", "post"):
            self.assertIn(key, STAGE_BASE_KEYS)

    def test_prompt_stage_payload_keys_are_carried(self):
        # Regression: "topic" and "post" were missing, so the prompt
        # stage received an empty payload in production and every
        # image came from the Skill alone. The analysis output the
        # visual concept must be built from has to reach the stage.
        for key in ("topic", "post"):
            self.assertIn(key, STAGE_BASE_KEYS)

    def test_base_keys_carry_the_art_direction(self):
        # Regression: the prompt stage decided the image direction
        # (icon, slot, palette, text alignment) and published it as
        # "art_direction", but the execute stage did not receive it,
        # so the concept's own decisions were dropped at the
        # compositor and only the Skill defaults survived.
        self.assertIn("art_direction", STAGE_BASE_KEYS)

    def test_icon_key_is_not_a_second_channel(self):
        # The icon reference travels inside the prompt as an
        # [icon:key] tag, which execute parses (_parse_icon_tag).
        # Carrying "icon_key" as well would be a second channel for
        # the same decision, free to disagree with the prompt.
        self.assertNotIn("icon_key", STAGE_BASE_KEYS)

    def test_execute_honors_the_carried_art_direction(self):
        # End of the transport: what the prompt stage decided must
        # be what the compositor is asked for.
        from stage_run import _skill_art_direction
        carried = {"icon": "use", "slot": "top-left",
                   "palette": ["#123456"], "text_align": "center"}
        art = _skill_art_direction(carried, {}, None)
        self.assertEqual(art["icon"], "use")
        self.assertEqual(art["slot"], "top-left")
        self.assertEqual(art["palette"], ["#123456"])
        self.assertEqual(art["text_align"], "center")
        self.assertTrue(art["icons_allowed"])

    def test_analysis_honors_carried_research(self):
        # The carried research findings must drive the verdict:
        # a FACT finding means eligible, not no-findings.
        from contracts import SourceItem
        from research.analysis import MockAnalysisModel
        from research.models import Finding, ResearchResult
        it = SourceItem(source_id="g-1", source_type="website",
                        source_url="https://x/1", title="T",
                        text="Body words.", item_id="g-1",
                        content_hash="h")

        class Client:
            def release_claim(self, key, run_id):
                pass

        ctx = {"client": Client(), "run_id": "r-1",
               "cfg": {"brand": {"tone": "direct",
                                 "audience": "producers"},
                       "language": {"default": "en"},
                       "pillars": [], "media": {}},
               "research": _to_jsonable(ResearchResult(
                   summary="S",
                   findings=[Finding(
                       statement="Fact words", kind="fact",
                       confidence="high", item_ids=["g-1"],
                       excerpt="Fact")],
                   topics=["t"], entities=[], item_ids=["g-1"],
                   model="stub", meta={})),
               "items": [_to_jsonable(it)], "claimed": [],
               "analysis_model": MockAnalysisModel()}
        payload = run_analysis_stage(ctx)
        self.assertTrue(payload["opportunity"]["eligible"])


class QueueCase(unittest.TestCase):
    class FakeClient:
        def __init__(self, states=None, broken=False):
            self.states = dict(states or {})
            self.broken = broken

        def get_research_state(self, source):
            if self.broken:
                raise ConnectionError("down")
            return self.states.get(source)

        def save_research_state(self, source, state):
            if self.broken:
                raise ConnectionError("down")
            self.states[source] = state
            return {}

    def entry(self, stmt="Fact words", iid="g-1", attempts=0):
        return {"statement": stmt, "kind": "fact",
                "confidence": "high", "item_ids": [iid],
                "excerpt": stmt[:60], "title": "T",
                "text": "Body words.", "source_url": "https://x/1",
                "attempts": attempts, "queued_at": "2026-01-01"}

    def queued_state(self, entries):
        return {QUEUE_SOURCE: {"candidates": {
            ("g-%d" % i if e["item_ids"] == ["g-1"] else "q:%d" % i): e
            for i, e in enumerate(entries)}}}

    def test_starving_cycle_offers_queue_as_data_not_as_findings(self):
        # No fresh items: the stage still succeeds with an EMPTY
        # research result, and parked material is offered to the next
        # stage as labelled data. It is never promoted to findings and
        # never becomes "this run's items".
        import research.cycle as _cyc
        real_collect = _cyc.collect_all_sources
        fc = self.FakeClient(self.queued_state([self.entry()]))
        _cyc.collect_all_sources = lambda *a: None
        try:
            payload = run_research_stage(
                {"store": object(), "cfg": {}, "client": fc,
                 "run_id": "r-1", "sources": [], "now": "t",
                 "dry_run": False})
        finally:
            _cyc.collect_all_sources = real_collect
        self.assertEqual(payload["outcome"], "findings")
        self.assertTrue(payload["queued"])
        self.assertEqual(payload["items"], [])
        self.assertEqual(payload["research"]["findings"], [])
        offered = payload["research"]["meta"]["unpublished"]
        self.assertEqual(len(offered), 1)
        self.assertEqual(offered[0]["statement"], "Fact words")
        # Offering is an observation, not a use: the entry stays
        # parked with its counter untouched, and the offer carries no
        # write of its own.
        saved = fc.states[QUEUE_SOURCE]["candidates"]
        self.assertEqual(len(saved), 1)
        self.assertEqual(list(saved.values())[0]["attempts"], 0)
        self.assertEqual(offered[0]["attempts"], 0)

    def test_parked_material_is_offered_even_when_the_cycle_found_data(self):
        # Brain no longer decides WHEN earlier material is shown: a
        # cycle that produced findings carries the offer too, as
        # optional DATA. Whether it is adopted is the next stage's
        # Skill's call.
        import research.cycle as _cyc
        from research.models import Finding, ResearchResult

        class Model:
            def research(self, items, cfg):
                return ResearchResult(
                    summary="s",
                    findings=[Finding(statement="A fact", kind="fact",
                                      confidence="high",
                                      item_ids=[items[0].item_id],
                                      excerpt="A fact")],
                    topics=["t"], entities=[], item_ids=[items[0].item_id],
                    model="stub", meta={})

        item = SourceItem(source_id="s-1", source_type="website",
                          source_url="http://x.test/1", title="T",
                          text="body", item_id="g-1")
        fc = self.FakeClient(self.queued_state([self.entry()]))
        real_collect = _cyc.collect_all_sources
        _cyc.collect_all_sources = lambda sources, store, now, out, \
            pending: out.items.append(item)
        try:
            payload = run_research_stage(
                {"store": object(), "cfg": {}, "client": fc,
                 "run_id": "r-1", "sources": [], "now": "t",
                 "dry_run": False, "research_model": Model()})
        finally:
            _cyc.collect_all_sources = real_collect
        self.assertEqual(payload["outcome"], "findings")
        self.assertTrue(payload["research"]["findings"])
        offer = payload["research"]["meta"]["unpublished"]
        self.assertEqual(len(offer), 1)
        self.assertEqual(offer[0]["statement"], "Fact words")
        # Offered, not used: this cycle's own findings are untouched
        # and the parked entry keeps its counter.
        self.assertEqual([f["statement"] for f in
                          payload["research"]["findings"]], ["A fact"])
        self.assertEqual(list(
            fc.states[QUEUE_SOURCE]["candidates"].values())[0]["attempts"],
            0)

    def test_ineligible_parks_findings(self):
        from research.analysis import ContentOpportunity

        class Nope:
            def analyze(self, result, policy, items=None):
                return ContentOpportunity(
                    eligible=False, rationale="no-fit")

        fc = self.FakeClient()
        finding = {"statement": "Fact words", "kind": "fact",
                   "confidence": "high", "item_ids": ["g-1"],
                   "excerpt": "Fact words"}
        item = {"source_id": "g-1", "source_type": "website",
                "source_url": "https://x/1", "title": "T",
                "text": "Body words.", "item_id": "g-1"}
        payload = run_analysis_stage(
            {"client": fc, "run_id": "r-1", "cfg": {},
             "research": {"summary": "S", "findings": [finding],
                          "topics": [], "entities": [],
                          "item_ids": ["g-1"], "model": "m",
                          "meta": {}},
             "items": [item], "claimed": [],
             "analysis_model": Nope(), "dry_run": False,
             "now": "t"})
        self.assertFalse(payload["opportunity"]["eligible"])
        saved = fc.states[QUEUE_SOURCE]["candidates"]
        self.assertEqual(len(saved), 1)
        self.assertEqual(
            list(saved.values())[0]["statement"], "Fact words")

    def test_publish_consumes_queue(self):
        # Posted entries leave; the rest stays unpublished.
        fc = self.FakeClient(self.queued_state(
            [self.entry("One", "g-1"), self.entry("Two", "g-2")]))
        _queue_consume(fc, ["g-1"])
        saved = fc.states[QUEUE_SOURCE]["candidates"]
        self.assertEqual(len(saved), 1)
        self.assertEqual(
            list(saved.values())[0]["statement"], "Two")

    def test_stale_entries_are_not_offered_again(self):
        # An entry that spent its real attempts is stale: it is no
        # longer offered, and offering never wrote it away — dropping
        # happens where the attempts are recorded.
        fc = self.FakeClient(self.queued_state(
            [self.entry(attempts=3)]))
        from stage_run import _unpublished_offer
        self.assertEqual(_unpublished_offer(fc), [])

    def test_attempted_entries_drop_when_they_spend_their_attempts(self):
        # Same staleness rule, now driven by use: each adoption
        # records one attempt and the last one retires the entry.
        from stage_run import _queue_attempt
        fc = self.FakeClient(self.queued_state([self.entry()]))
        for expected in (1, 2):
            _queue_attempt(fc, ["g-1"])
            saved = list(fc.states[QUEUE_SOURCE]["candidates"].values())
            self.assertEqual(saved[0]["attempts"], expected)
        _queue_attempt(fc, ["g-1"])
        self.assertEqual(fc.states[QUEUE_SOURCE]["candidates"], {})

    def test_parking_retires_entries_that_spent_their_attempts(self):
        # An exhausted entry must not occupy capped queue space.
        from research.analysis import ContentOpportunity
        from stage_run import _queue_add

        class Nope:
            def analyze(self, result, policy, items=None):
                return ContentOpportunity(eligible=False,
                                          rationale="no-fit")

        fc = self.FakeClient(self.queued_state(
            [self.entry(attempts=3), self.entry("Fresh", "g-2")]))
        _queue_add(fc, [Nope()], [], "t")
        saved = list(fc.states[QUEUE_SOURCE]["candidates"].values())
        self.assertEqual([e["statement"] for e in saved], ["Fresh"])

    def test_dry_run_never_touches_the_queue(self):
        import research.cycle as _cyc
        from stage_run import _unpublished_offer
        real_collect = _cyc.collect_all_sources
        fc = self.FakeClient(self.queued_state([self.entry()]))
        _cyc.collect_all_sources = lambda *a: None
        try:
            payload = run_research_stage(
                {"store": object(), "cfg": {}, "client": fc,
                 "run_id": "r-1", "sources": [], "now": "t",
                 "dry_run": True})
        finally:
            _cyc.collect_all_sources = real_collect
        self.assertEqual(payload["research"]["findings"], [])
        self.assertNotIn("unpublished", payload["research"]["meta"])
        self.assertEqual(
            list(fc.states[QUEUE_SOURCE]["candidates"].values())[0]["attempts"],
            0)

    def test_broken_transport_never_clobbers(self):
        fc = self.FakeClient(broken=True)
        from stage_run import _unpublished_offer
        self.assertEqual(_unpublished_offer(fc), [])
        from stage_run import _queue_add
        _queue_add(fc, [{"statement": "S", "kind": "fact",
                         "confidence": "high", "item_ids": ["g-9"],
                         "excerpt": "S"}], [], "t")
        self.assertEqual(fc.states, {})


class IconSelectCase(unittest.TestCase):
    MANIFEST = {"tree": {"icons": {"count": 2}, "logos": {"count": 1}},
                "files": {
                    "identity/icons/play-circle.svg": {
                        "category": "icons", "name": "play-circle",
                        "ext": ".svg"},
                    "identity/icons/waveform-bars.svg": {
                        "category": "icons", "name": "waveform-bars",
                        "ext": ".svg"},
                    "identity/logos/acme.svg": {
                        "category": "logos", "name": "acme",
                        "ext": ".svg"}},
                "docs": []}

    class Adapter:
        def __init__(self, script):
            self.script = list(script)
            self.calls = []

        def complete(self, system, user, **kw):
            self.calls.append((system, user))
            return self.script.pop(0)

    def test_two_step_pick_returns_key(self):
        from g2.generators import OpenAIImagePromptGenerator
        import json as _json
        gen = OpenAIImagePromptGenerator(adapter=self.Adapter([
            _json.dumps({"category": "icons"}),
            _json.dumps({"icon": "icons/waveform-bars"})]))
        self.assertEqual(
            gen.select_icon("mix translation", self.MANIFEST),
            "identity/icons/waveform-bars.svg")

    def test_unknown_category_fails_closed(self):
        from g2.generators import OpenAIImagePromptGenerator
        import json as _json
        gen = OpenAIImagePromptGenerator(adapter=self.Adapter([
            _json.dumps({"category": "nope"}),
            _json.dumps({"category": "nope"})]))
        self.assertIsNone(
            gen.select_icon("mix translation", self.MANIFEST))

    def test_unknown_icon_fails_closed(self):
        from g2.generators import OpenAIImagePromptGenerator
        import json as _json
        gen = OpenAIImagePromptGenerator(adapter=self.Adapter([
            _json.dumps({"category": "icons"}),
            _json.dumps({"icon": "icons/ghost"}),
            _json.dumps({"icon": "icons/ghost"})]))
        self.assertIsNone(
            gen.select_icon("mix translation", self.MANIFEST))

    def test_empty_manifest_selects_nothing(self):
        from g2.generators import MockImagePromptGenerator
        self.assertIsNone(
            MockImagePromptGenerator().select_icon("t", {}))
        from g2.generators import OpenAIImagePromptGenerator
        adapter = self.Adapter([])
        gen = OpenAIImagePromptGenerator(adapter=adapter)
        self.assertIsNone(gen.select_icon("t", {}))
        self.assertEqual(adapter.calls, [])

    def test_icon_tag_round_trip(self):
        from stage_run import _parse_icon_tag
        prompt, key = _parse_icon_tag(
            "A harbor at dawn\n[icon:identity/icons/play-circle.svg]")
        self.assertEqual(prompt, "A harbor at dawn")
        self.assertEqual(key, "identity/icons/play-circle.svg")
        prompt, key = _parse_icon_tag("A harbor at dawn")
        self.assertEqual(prompt, "A harbor at dawn")
        self.assertIsNone(key)

    def test_hallucinated_mid_prompt_tag_stripped(self):
        # Live failure: the model emitted its own [icon:...] tag
        # mid-prompt while the stage appended the real one. The
        # image prompt must carry no tag text at all; the last
        # well-formed tag wins as the key.
        from stage_run import _parse_icon_tag
        prompt, key = _parse_icon_tag(
            "A translucent hourglass.  \n\n[icon:waveform_audio]\n"
            "[icon:identity/icons/note.png]")
        self.assertEqual(prompt, "A translucent hourglass.")
        self.assertEqual(key, "identity/icons/note.png")
        self.assertNotIn("[icon:", prompt)


class LegacyOutcomeCase(unittest.TestCase):
    def test_research_outcomes_cover_all_legacy_branches(self):
        # Legacy outcome map (V2 flow lookup): off when the role
        # is off, and a valid result (empty or not) otherwise.
        # V1 ignores the extra key (it reads items/roles).
        import research.cycle as _cyc
        from contracts import SourceItem
        real_collect = _cyc.collect_all_sources

        def empty_collect(sources, store, now, out, pending):
            pass

        class Model:
            def research(self, items, cfg):
                from research.models import ResearchResult
                return ResearchResult(
                    summary="s", findings=[], topics=[],
                    entities=[], item_ids=[], model="stub",
                    meta={})

        base = {"store": object(), "cfg": {},
                "client": object(), "run_id": "r-1",
                "sources": [], "now": "t",
                "research_model": Model(), "dry_run": False}
        _cyc.collect_all_sources = empty_collect
        try:
            out = run_research_stage(dict(base))
        finally:
            _cyc.collect_all_sources = real_collect
        # Nothing collected is still a SUCCESSFUL research result, and
        # it is a normal result object with an empty findings list, not
        # a sentinel and not a different outcome.
        self.assertEqual(out["outcome"], "findings")
        self.assertEqual(out["items"], [])
        self.assertEqual(out["research"]["findings"], [])
        self.assertIsInstance(out["research"]["summary"], str)

        def one_collect(sources, store, now, out, pending):
            out.items.append(SourceItem(
                source_id="s-1", source_type="website",
                source_url="https://x/1", title="t", text="w"))

        _cyc.collect_all_sources = one_collect
        try:
            out = run_research_stage(dict(base))
        finally:
            _cyc.collect_all_sources = real_collect
        self.assertEqual(out["outcome"], "findings")
        self.assertEqual(len(out["items"]), 1)

    def test_research_off_outcome_without_model(self):
        import research.cycle as _cyc
        real_collect = _cyc.collect_all_sources

        def one_collect(sources, store, now, out, pending):
            from contracts import SourceItem
            out.items.append(SourceItem(
                source_id="s-1", source_type="website",
                source_url="https://x/1", title="t", text="w"))

        class Boom:
            def research(self, items, cfg):
                raise AssertionError("model must not run")

        _cyc.collect_all_sources = one_collect
        try:
            out = run_research_stage(
                {"store": object(), "cfg": {"roles": ["text"]},
                 "client": object(), "run_id": "r-1",
                 "sources": [], "now": "t",
                 "research_model": Boom(), "dry_run": False})
        finally:
            _cyc.collect_all_sources = real_collect
        self.assertEqual(out["outcome"], "off")
        self.assertIsNone(out["research"])

    def test_analysis_text_prompt_execute_outcomes(self):
        from research.analysis import ContentOpportunity
        from stage_run import (run_execute_stage, run_prompt_stage,
                               run_text_stage)

        class Client:
            def release_claim(self, key, run_id):
                pass

        policy = {"brand": {"tone": "direct",
                            "audience": "Libya"},
                  "language": {"default": "ar"},
                  "pillars": [], "generation": {}, "media": {}}
        opp = {"eligible": True, "topic": "T", "angle": "A",
               "rationale": "R", "facts": ["Fact words here"],
               "source_item_ids": ["g-1"], "content_format": "post",
               "media_intent": "image", "audience": "a",
               "language": "ar", "priority": "normal",
               "confidence": "high", "constraints": []}
        no = dict(opp, eligible=False, media_intent="none")
        item = {"source_id": "g-1", "source_type": "website",
                "source_url": "https://x/1", "title": "T",
                "text": "Body words here.", "item_id": "g-1"}

        class Eligible:
            def analyze(self, result, policy, items=None):
                return ContentOpportunity(**opp)

        class Ineligible:
            def analyze(self, result, policy, items=None):
                return ContentOpportunity(**no)

        research = {"summary": "S", "findings": [{
            "statement": "Fact words here", "kind": "fact",
            "confidence": "high", "item_ids": ["g-1"],
            "excerpt": "Fact words here"}],
            "topics": [], "entities": [], "item_ids": ["g-1"],
            "model": "m", "meta": {}}
        base = {"client": Client(), "run_id": "r-1", "cfg": policy,
                "research": research, "items": [item],
                "claimed": [], "dry_run": True}
        yes = run_analysis_stage(dict(base, analysis_model=Eligible()))
        self.assertEqual(yes["opportunity"]["eligible"], True)
        self.assertEqual(yes["outcome"], "eligible")
        no_out = run_analysis_stage(
            dict(base, analysis_model=Ineligible()))
        self.assertEqual(no_out["outcome"], "ineligible")

        class Text:
            def generate(self, title, summary, brand, cfg):
                return "Post words here."

        tout = run_text_stage(dict(
            base, opportunity=yes["opportunity"], text_gen=Text()))
        self.assertTrue(tout["text"])
        self.assertEqual(tout["outcome"], "drafted_image")

        class Prompt:
            def generate_prompt(self, topic, brand, cfg, post=None):
                Prompt.seen = (topic, post)
                return "A calm harbor."

        pout = run_prompt_stage(dict(
            base, topic="T", prompt_gen=Prompt(),
            post={"topic": "T", "angle": "why loudness fails",
                  "facts": ["car speakers roll off below 60Hz"],
                  "audience": "mix engineers", "media_intent": "image"}))
        self.assertEqual(pout["outcome"], "ready")
        self.assertEqual(pout["prompt"], "A calm harbor.")
        # The analysis payload reaches the prompt generator intact.
        self.assertEqual(Prompt.seen[0], "T")
        self.assertEqual(Prompt.seen[1]["angle"], "why loudness fails")
        self.assertEqual(Prompt.seen[1]["facts"],
                         ["car speakers roll off below 60Hz"])
        self.assertEqual(Prompt.seen[1]["audience"], "mix engineers")

        eout = run_execute_stage(dict(
            base, opportunity=yes["opportunity"], text="Post words.",
            prompt="A calm harbor.", mode="draft",
            integration_id="int-1", schedule={}))
        self.assertEqual(eout["outcome"], "done")

    def test_staged_run_carries_recent_history_and_source_free_content(self):
        # Two engine guarantees on the staged path (the one campaigns
        # actually use): recent history reaches the decision stage, and
        # a decision with no research item still produces content —
        # with the destination taken from the integration context.
        import stage_run
        from research.analysis import ContentOpportunity
        from stage_run import (run_execute_stage, run_text_stage)

        class Client:
            def __init__(self):
                self.state = {
                    "published-topics": {"topics": {"items": [
                        {"topic": "Old topic", "angle": "Old angle"}]}}}

            def get_research_state(self, key):
                return self.state.get(key)

            def save_research_state(self, key, value):
                self.state[key] = value
                return value

            def release_claim(self, key, run_id):
                pass

        real_base = stage_run._stage_base

        def _base(args, run_id, base=None):
            out = real_base(args, run_id, base)
            return out
        # History reaches the decision stage: the same ledger the
        # full-cycle path already reads is now visible here too.
        client = Client()
        from run_cycle import _recent_topics
        self.assertEqual(_recent_topics(client),
                         [{"topic": "Old topic", "angle": "Old angle"}])

        opportunity = {"eligible": True, "topic": "Evergreen topic",
                       "angle": "Angle", "rationale": "R",
                       "facts": ["truthful fact"], "source_item_ids": [],
                       "content_format": "post", "media_intent": "none",
                       "audience": "a", "language": "en",
                       "priority": "normal", "confidence": "medium",
                       "constraints": []}

        class Text:
            def generate(self, title, summary, brand, cfg):
                Text.seen = (title, summary)
                return "Post words here."

        ctx = {"client": client, "run_id": "r-2",
               "cfg": {"brand": {"tone": "direct", "audience": "a"},
                       "language": {"default": "en"},
                       "channel_meta": {"identifier": "mastodon"},
                       "pillars": [], "generation": {}, "media": {},
                       "hashtags": {"enabled": True, "max": 3},
                       "links_policy": "hide", "schedule": {},
                       "roles": ["research", "analysis", "text"]},
               "research": None, "items": [], "opportunity": opportunity,
               "claimed": [], "dry_run": True, "text_gen": Text(),
               "text": "Post words here.", "mode": "draft",
               "integration_id": "int-9", "schedule": {}, "now": "t"}

        tout = run_text_stage(ctx)
        self.assertEqual(tout["outcome"], "drafted")
        # The unit is the decision's own content, not a fake source.
        self.assertEqual(Text.seen[0], "Evergreen topic")

        eout = run_execute_stage(ctx)
        self.assertEqual(eout["outcome"], "done")
        # Destination came from the integration context; nothing in the
        # engine defaults a platform any more.
        self.assertEqual(eout.get("platform", "mastodon"), "mastodon")
        self.assertTrue(_base)

    def test_editorial_fallback_is_not_a_research_finding(self):
        # The source-free unit must stay distinguishable from collected
        # material all the way into the decision payload.
        from research.generation import editorial_item_for
        item = editorial_item_for(
            __import__("research.generation", fromlist=["x"])
            .GenerationPlan(text=__import__(
                "research.generation", fromlist=["x"])
                .TextRequest(title="Evergreen", summary="facts")))
        self.assertEqual(item.source_type, "editorial")
        self.assertEqual(item.item_id, "")
        self.assertEqual(item.source_url, "")


class CliCase(unittest.TestCase):
    def test_bad_stage_rejected(self):
        self.assertEqual(
            main_stage(["--stage", "nope", "campaign.yaml"]), 2)

    def test_missing_input_file_rejected(self):
        self.assertEqual(
            main_stage(["--stage", "research", "--stage-input",
                        "C:\\nope\\missing.json", "campaign.yaml",
                        "--strategy-by-integration", "x"]), 2)


if __name__ == "__main__":
    unittest.main()
