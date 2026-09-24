import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from contracts import SourceItem
from g2 import generators as G
from g2.pipeline import build_package


def item(**kw):
    d = dict(source_id="s1", source_type="website",
             source_url="https://demo.test/a", title="Big Launch Day",
             text="We launch the new platform today with fast tools.",
             images=[], links=[], platform="website")
    d.update(kw)
    return SourceItem(**d)


BRAND = {"tone": "bold", "audience": "founders", "name": "Juzzir"}


class _AgentStub:
    def __init__(self, prompt="AGENT-PROMPT", raise_on=None):
        self.prompt = prompt
        self.raise_on = raise_on
        self.calls = []

    def generate_prompt(self, topic, brand, policy=None):
        self.calls.append((topic, brand))
        if self.raise_on:
            raise self.raise_on
        return self.prompt


class MockPromptTest(unittest.TestCase):
    def test_mock_prompt_is_non_empty(self):
        p = G.MockImagePromptGenerator().generate_prompt("topic", BRAND, {})
        self.assertIn("topic", p)
        self.assertTrue(p.strip())


class PromptScaffoldingTest(unittest.TestCase):
    class _Adapter:
        def __init__(self, raw):
            self.raw = raw

        def complete(self, system, user, **kw):
            return self.raw

    def _gen(self, raw):
        return G.OpenAIImagePromptGenerator(adapter=self._Adapter(raw))

    def test_icon_template_scaffolding_stripped(self):
        # Live failure: the model echoed the Skill's icon-channel
        # template (category / icon / icon:<name>) around the real
        # prompt. Only the visual prompt may reach the generator.
        out = self._gen(
            "category\nicon\n\nA single translucent fader lever, "
            "centered left on a dark gradient, minimalist, portrait 4:5.\n\n"
            "icon:play_compare\n").generate_prompt("t", BRAND, {})
        self.assertEqual(
            out,
            "A single translucent fader lever, centered left on a dark "
            "gradient, minimalist, portrait 4:5.")

    def test_prompt_mentioning_icon_kept(self):
        out = self._gen(
            "A small icon of a play button floats beside a fader, "
            "minimalist, portrait 4:5.").generate_prompt("t", BRAND, {})
        self.assertIn("icon of a play button", out)

    def test_post_payload_and_brief_reach_the_model(self):
        class Spy:
            def __init__(self):
                self.system = ""

            def complete(self, system, user, **kw):
                self.system = system
                return "A quiet workshop bench, warm key light, portrait."

        adapter = Spy()
        gen = G.OpenAIImagePromptGenerator(adapter=adapter)
        policy = {
            "visual": {"brief": {
                "palette": ["deep teal", "warm sand"],
                "treatment": ["editorial photography"],
                "contrast": ["high key with deep shadows"],
                "subject": ["one human-scale detail"],
                "character": "calm, craft-focused",
            }},
        }
        out = gen.generate_prompt(
            "Try-before-you-pay mastering preview", BRAND, policy,
            {"topic": "Try-before-you-pay mastering preview",
             "angle": "compare a mix before paying",
             "facts": ["preview is A/B against the original"],
             "audience": "mix engineers", "media_intent": "image"})
        self.assertTrue(out)
        for needle in ("Topic: Try-before-you-pay mastering preview",
                       "Angle: compare a mix before paying",
                       "Key facts: preview is A/B against the original",
                       "Audience: mix engineers",
                       "Media intent: image",
                       "Palette: deep teal, warm sand.",
                       "Treatment: editorial photography.",
                       "Contrast: high key with deep shadows.",
                       "Subject and composition: one human-scale detail.",
                       "Visual character: calm, craft-focused."):
            self.assertIn(needle, adapter.system)

    def test_no_post_payload_still_builds_a_prompt(self):
        class Spy:
            def __init__(self):
                self.system = ""

            def complete(self, system, user, **kw):
                self.system = system
                return "A quiet bench, warm key light, portrait."

        adapter = Spy()
        G.OpenAIImagePromptGenerator(adapter=adapter).generate_prompt(
            "Plain topic", BRAND, {})
        self.assertNotIn("Post to illustrate:", adapter.system)

    def test_labelled_icon_channel_scaffolding_stripped(self):
        # Live failure: the model emitted the Skill's channel header
        # with values (ICON SELECTION: / Category: x / Icon: y) plus
        # its own [icon:...] tag. None may reach the generator. A
        # later run bolded the same lines (**Category:** x), so the
        # format must not slip through either.
        for header in (
                "ICON SELECTION:\nCategory: waveform/audio\nIcon: waveform-pulse",
                "**Category:** waveform/audio  \n**Icon:** waveform_pulse"):
            out = self._gen(
                header + "\n\n"
                "A weathered reel-to-reel recorder on an oak table, warm "
                "spotlight from the upper left, vertical portrait.\n"
                "[icon:waveform-pulse]").generate_prompt("t", BRAND, {})
            self.assertEqual(
                out,
                "A weathered reel-to-reel recorder on an oak table, warm "
                "spotlight from the upper left, vertical portrait.")


class PipelineAgentTest(unittest.TestCase):
    def _pkg(self, policy):
        return build_package(item(), BRAND, {}, {"enabled": False},
                             "hide", G.MockTextGenerator(),
                             G.MockImageGenerator(), policy=policy)

    def test_agent_prompt_used_when_configured(self):
        agent = _AgentStub()
        pkg = self._pkg({"image_prompt_gen": agent})
        self.assertEqual(pkg.media[0]["kind"], "generator")
        self.assertEqual(pkg.media[0]["prompt"], "AGENT-PROMPT")
        self.assertEqual(len(agent.calls), 1)

    def test_deterministic_prompt_when_agent_absent(self):
        pkg = self._pkg({})
        prompt = pkg.media[0]["prompt"]
        self.assertIn("Big Launch Day", prompt)
        self.assertNotEqual(prompt, "AGENT-PROMPT")

    def test_agent_failure_propagates(self):
        agent = _AgentStub(raise_on=RuntimeError("boom"))
        with self.assertRaises(RuntimeError):
            self._pkg({"image_prompt_gen": agent})


class BuildImagePromptGenTest(unittest.TestCase):
    KEYS = [
        "BRAIN_IMAGE_PROMPT_ADAPTER", "BRAIN_IMAGE_PROMPT_PROTOCOL",
        "BRAIN_IMAGE_PROMPT_KEY", "BRAIN_IMAGE_PROMPT_MODEL",
        "BRAIN_IMAGE_PROMPT_BASE", "BRAIN_IMAGE_PROMPT_SESSION_HEADER",
        "BRAIN_IMAGE_PROMPT_HEADERS", "OPENAI_API_KEY",
    ]

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in self.KEYS}
        for k in self.KEYS:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _build(self):
        from run_cycle import _build_image_prompt_gen
        return _build_image_prompt_gen("--openai", session_id="run-1")

    def test_none_without_any_config(self):
        self.assertIsNone(self._build())

    def test_mock_mode_returns_mock(self):
        from run_cycle import _build_image_prompt_gen
        gen = _build_image_prompt_gen("--mock")
        self.assertIsInstance(gen, G.MockImagePromptGenerator)

    def test_partial_config_fails_closed(self):
        os.environ["BRAIN_IMAGE_PROMPT_ADAPTER"] = "openai"
        with self.assertRaises(ValueError):
            self._build()

    def test_builds_when_configured(self):
        os.environ["BRAIN_IMAGE_PROMPT_ADAPTER"] = "openai"
        os.environ["BRAIN_IMAGE_PROMPT_KEY"] = "sk-agent"
        os.environ["BRAIN_IMAGE_PROMPT_MODEL"] = "m-agent"
        os.environ["BRAIN_IMAGE_PROMPT_HEADERS"] = '{"X-Tag": "a"}'
        gen = self._build()
        self.assertIsInstance(gen, G.OpenAIImagePromptGenerator)
        self.assertTrue(hasattr(gen, "generate_prompt"))


class ConceptGroundingTest(unittest.TestCase):
    PAYLOAD = {
        "topic": "Try-before-you-pay mastering preview",
        "angle": "Hear and compare different mastering styles on your "
                 "own track before committing to pay",
        "audience": "people interested in music production and mastering",
        "media_intent": "image",
        "facts": [
            "Juzzir lets you upload a track and hear different mastering "
            "styles before paying.",
            "Juzzir's preview step lets you compare the original mix "
            "against the mastered version side by side before deciding.",
        ],
    }

    CLIMBER = (
        '{"meaning": "A single, focused effort can overcome a large '
        'challenge through persistence and determination.", "subject": '
        '"A lone climber ascending a steep rock face, captured mid-climb '
        'with visible effort and determination.", "relationship": "The '
        'climber represents persistent effort against the rock of '
        'obstacles, triumph over adversity.", "brief_constraints": [], '
        '"art_direction": {"icon": "none", "slot": "", "palette": [], '
        '"text_align": "left"}}')

    GROUNDED = (
        '{"meaning": "The viewer can compare the original mix against '
        'the mastered version before paying for a track.", "subject": '
        '"Two audio renderings of the same track side by side, the '
        'untreated mix and the mastered version.", "relationship": "The '
        'side-by-side comparison lets the listener judge the mastered '
        'track before committing to pay.", "brief_constraints": ["single '
        'subject, quiet lower third"], "art_direction": {"icon": "use", '
        '"slot": "top-right", "palette": ["amber"], "text_align": '
        '"left"}}')

    def _gen(self, outcomes):
        class Adapter:
            def __init__(self, script):
                self.script = list(script)
                self.systems = []

            def complete(self, system, user, **kw):
                self.systems.append(system)
                return self.script.pop(0)

        adapter = Adapter(outcomes)
        return G.OpenAIImagePromptGenerator(adapter=adapter), adapter

    def test_climber_adversity_concept_is_rejected(self):
        # Live failure: a structurally valid stock metaphor for a
        # mastering-comparison post. Both attempts are refused and no
        # concept is returned.
        gen, _ = self._gen([self.CLIMBER, self.CLIMBER])
        with self.assertRaises(Exception):
            gen.generate_concept(self.PAYLOAD, {})

    def test_grounded_concept_is_accepted(self):
        gen, _ = self._gen([self.GROUNDED])
        out = gen.generate_concept(self.PAYLOAD, {})
        self.assertEqual(
            out["subject"],
            "Two audio renderings of the same track side by side, the "
            "untreated mix and the mastered version.")
        self.assertEqual(
            out["brief_constraints"], ["single subject, quiet lower third"])

    def test_structural_fields_still_required(self):
        missing_subject = self.GROUNDED.replace(
            '"subject": "Two audio renderings of the same track side by '
            'side, the untreated mix and the mastered version.",', '')
        gen, _ = self._gen([missing_subject, missing_subject])
        with self.assertRaises(Exception):
            gen.generate_concept(self.PAYLOAD, {})

    def test_compare_morphology_is_grounded(self):
        # Morphological variants of the same claim must not be treated
        # as different concepts. The payload carries "compare" and the
        # concept uses "comparison" - same stem, grounded.
        payload = dict(self.PAYLOAD)
        payload["facts"] = ["compare the original mix"]
        concept = json.dumps({
            "meaning": "the viewer needs to see a comparison of the original and mastered versions",
            "subject": "a comparison view of two waveforms",
            "relationship": "the comparison shows the difference before paying",
            "brief_constraints": [],
            "art_direction": {"icon": "none", "slot": "", "palette": [], "text_align": "left"},
        })
        gen, _ = self._gen([concept])
        out = gen.generate_concept(payload, {})
        self.assertIn("comparison", out["subject"])

    def test_no_payload_vocabulary_refuses_the_concept(self):
        # Production truth: the stage used to receive no post payload
        # at all, and the concept was invented from nothing. With no
        # claim vocabulary there is nothing to ground in, so the
        # concept must be refused instead of accepted.
        gen, _ = self._gen([self.GROUNDED, self.GROUNDED])
        with self.assertRaises(Exception):
            gen.generate_concept({"topic": ""}, {})

    def test_visual_brief_and_instructions_survive(self):
        gen, adapter = self._gen([self.GROUNDED])
        gen.generate_concept(self.PAYLOAD, {
            "visual": {"brief": {"treatment": ["editorial photography"],
                                 "palette": ["deep teal", "warm sand"]}},
        })
        system = adapter.systems[0]
        self.assertIn("Treatment: editorial photography.", system)
        self.assertIn("Palette: deep teal, warm sand.", system)
        self.assertIn("generic motivational", system)
        self.assertIn("own communication goal", system)


    def test_art_direction_decides_icon_slot_palette_and_alignment(self):
        raw = json.dumps({
            "meaning": "the viewer can compare the original mix against the "
                       "mastered version before paying",
            "subject": "two audio renderings of the same track side by side",
            "relationship": "the comparison lets the listener judge the "
                            "mastered track before paying",
            "brief_constraints": ["quiet lower third"],
            "art_direction": {"icon": "use", "slot": "top-left",
                              "palette": ["amber", "deep teal"],
                              "text_align": "center"},
        })
        gen, _ = self._gen([raw])
        out = gen.generate_concept(self.PAYLOAD, {})
        self.assertEqual(out["art_direction"]["icon"], "use")
        self.assertEqual(out["art_direction"]["slot"], "top-left")
        self.assertEqual(out["art_direction"]["palette"],
                         ["amber", "deep teal"])
        self.assertEqual(out["art_direction"]["text_align"], "center")

    def test_no_icon_is_a_valid_decision(self):
        raw = json.dumps({
            "meaning": "the viewer should hear the mastered version of "
                       "their own track before paying",
            "subject": "a single audio waveform resolving into a finished "
                       "master",
            "relationship": "the waveform becoming the master shows the "
                            "result the listener will get",
            "brief_constraints": [],
            "art_direction": {"icon": "none", "slot": "",
                              "palette": [], "text_align": "left"},
        })
        gen, _ = self._gen([raw])
        out = gen.generate_concept(self.PAYLOAD, {})
        self.assertEqual(out["art_direction"]["icon"], "none")
        self.assertEqual(out["art_direction"]["slot"], "")

    def test_unknown_icon_slot_is_refused(self):
        raw = json.dumps({
            "meaning": "the viewer can compare the original mix against the "
                       "mastered version before paying",
            "subject": "two audio renderings side by side",
            "relationship": "the comparison happens before paying",
            "brief_constraints": [],
            "art_direction": {"icon": "use", "slot": "bottom-right",
                              "palette": [], "text_align": "left"},
        })
        gen, _ = self._gen([raw, raw])
        with self.assertRaises(Exception):
            gen.generate_concept(self.PAYLOAD, {})

    def test_zone_contract_reaches_the_image_model(self):
        # Single source of zones: the numbers rendered into the prompt
        # are the numbers the compositor will place layers into.
        class Spy:
            def __init__(self):
                self.system = ""

            def complete(self, system, user, **kw):
                self.system = system
                return "A bench, warm key light, portrait."

        adapter = Spy()
        G.OpenAIImagePromptGenerator(adapter=adapter).generate_prompt(
            "Plain topic", BRAND, {})
        system = adapter.system
        self.assertIn("Reserved areas", system)
        self.assertIn("x 72 to 824", system)
        self.assertIn("y 520 to 820", system)
        self.assertIn("y 0 to 507", system)
        self.assertIn("top-left", system)
        self.assertIn("top-right", system)
        self.assertEqual(G.AD_LAYOUT["width"], 896)
        self.assertEqual(G.AD_LAYOUT["height"], 1152)


if __name__ == "__main__":
    unittest.main()
