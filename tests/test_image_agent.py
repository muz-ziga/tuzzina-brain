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

    def test_engine_states_no_word_count_rule(self):
        # The prompt LENGTH is the Image Skill's rule (the active
        # document says "under 60 words"). The generic engine asks for
        # one prompt and no explanation, and imposes no limit of its
        # own that could contradict the Skill.
        captured = {}
        import llm.adapters as _adapters
        real = _adapters.complete_with_retry

        def _capture(adapter, system, context, **kw):
            captured["system"] = system
            return real(adapter, system, context, **kw)
        _adapters.complete_with_retry = _capture
        try:
            self._gen("A fader under one lamp, portrait 4:5.").generate_prompt(
                "t", BRAND, {})
        finally:
            _adapters.complete_with_retry = real
        self.assertNotIn("60-100 words", captured["system"])
        self.assertIn("Return one image prompt only, no explanation.",
                      captured["system"])

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

    def test_concept_step_receives_the_image_skill(self):
        # The concept is what picks the subject, so the Image Skill's own
        # text has to reach it. Brain routes the document; it never
        # restates a rule of its own.
        class Spy:
            def __init__(self):
                self.system = ""

            def complete(self, system, user, **kw):
                self.system = system
                return json.dumps({
                    "meaning": "the viewer hears the difference a master "
                               "makes to the same track",
                    "subject": "a mastering console with two meters",
                    "relationship": "the meters show the change",
                    "brief_constraints": [],
                    "art_direction": {"icon": "none", "slot": "",
                                      "palette": [], "text_align": "left"},
                })

        spy = Spy()
        G.OpenAIImagePromptGenerator(adapter=spy).generate_concept(
            self.PAYLOAD, {})
        from skills.loader import get_skill
        instructions = get_skill("image", None)["instructions"]
        first_line = instructions.strip().splitlines()[0]
        self.assertIn(first_line, spy.system)
        self.assertIn("The concept must already comply", spy.system)
        # The prompt writer still gets the full Skill on its own call.
        prompt_spy = Spy()
        gen = G.OpenAIImagePromptGenerator(adapter=prompt_spy)
        gen.generate_prompt("topic", BRAND, {}, self.PAYLOAD)
        self.assertIn(first_line, prompt_spy.system)

    def test_prompt_gate_ignores_numbers_outside_key_facts(self):
        # Step 3: the gate is scoped to the post's Key facts, so a digit
        # in the topic/angle/audience/media-intent (framing, not a
        # measurement the artwork could render) must NOT trip it, while a
        # figure the post states in Key facts still does.
        payload = {
            "topic": "11 ways to master your track",
            "angle": "hear 3 mixes before paying",
            "facts": ["the preview compares original and mastered"],
            "audience": "musicians", "media_intent": "image",
        }
        framing = ("MODERN DIGITAL GRAPHIC DESIGN. Show 11 framed cards "
                   "from the post. No text, no letters, no numbers.")
        clean = ("MODERN DIGITAL GRAPHIC DESIGN. A lighter band between two "
                 "darker regions. No text, no letters, no numbers.")
        # A topic/angle digit repeats in the prompt: no corrected call.
        gen, spy = self._gen([framing, clean])
        out = gen.generate_prompt("topic", BRAND, {}, payload, None)
        self.assertEqual(len(spy.systems), 1)
        self.assertEqual(out, framing.strip())
        # A Key-fact digit repeats in the prompt: one corrected call.
        payload["facts"] = ["streaming target around -14 LUFS"]
        leaky14 = ("MODERN DIGITAL GRAPHIC DESIGN. A scale marked -14 LUFS. "
                   "No text, no letters, no numbers.")
        gen2, spy2 = self._gen([leaky14, clean])
        out2 = gen2.generate_prompt("topic", BRAND, {}, payload, None)
        self.assertEqual(len(spy2.systems), 2)
        self.assertEqual(out2, clean.strip())

    def test_payload_gate_only_catches_numbers_the_post_supplied(self):
        # The gate is scoped on purpose: a blind "no digits anywhere"
        # rule was rejected by the owner. Only a figure that traces
        # back to the post payload is refused.
        payload = dict(self.PAYLOAD)
        payload["facts"] = ["Streaming targets sit roughly around "
                            "-14 to -11 LUFS."]
        numbers = set(G._bare_numbers(G._post_block(payload)))
        self.assertIn("14", numbers)
        self.assertIn("11", numbers)

        # Leaks when the prompt repeats a payload number.
        self.assertTrue(G._payload_leak(
            "a scale with the range around -14 to -11 LUFS", numbers))
        # Clean when it does not.
        self.assertEqual(G._payload_leak(
            "a scale where one band is wider and lighter", numbers), "")
        # A number the post never contained is NOT this gate's job.
        self.assertEqual(G._payload_leak(
            "a stack of 9 blocks, 3 across", numbers), "")
        # Hex colors and aspect ratios never count as payload figures.
        self.assertEqual(G._payload_leak(
            "gradient #FF5733, portrait 4:5", numbers), "")

    def test_prompt_gate_catches_digits_from_the_key_facts(self):
        # The concept gate cannot see this path: the post's own Key
        # facts carry the figure, so a digit-free concept still
        # produced "the streaming target range around -14 to -11 LUFS"
        # in the same sentence as "no numbers".
        leaky = ("MODERN DIGITAL GRAPHIC DESIGN. A vertical loudness "
                 "scale with the target range around -14 to -11 LUFS "
                 "highlighted. No text, no letters, no numbers.")
        clean = ("MODERN DIGITAL GRAPHIC DESIGN. A vertical loudness "
                 "scale where the safe band is a lighter, wider region "
                 "between two darker regions. No text, no letters, no "
                 "numbers, no logos.")
        payload = dict(self.PAYLOAD)
        payload["facts"] = ["Streaming targets sit roughly around "
                            "-14 to -11 LUFS."]
        concept = {"meaning": "loudness is felt", "subject": "a scale",
                   "relationship": "a band stands out",
                   "brief_constraints": [], "art_direction": {}}

        class Spy:
            def __init__(self):
                self.systems = []

            def complete(self, system, user, **kw):
                self.systems.append(system)
                return leaky if len(self.systems) == 1 else clean

        spy = Spy()
        out = G.OpenAIImagePromptGenerator(adapter=spy).generate_prompt(
            "Understanding Loudness Basics", BRAND, {}, payload, concept)
        # The corrected answer is what reached the caller.
        self.assertNotIn("LUFS", out)
        self.assertEqual(G._payload_leak(out, set(G._bare_numbers(
            G._post_block(payload)))), "")
        # Exactly one corrected call, and it names the offender without
        # prescribing any channel-specific device vocabulary of its own.
        self.assertEqual(len(spy.systems), 2)
        self.assertIn("which was refused", spy.systems[1])
        self.assertIn("Do not", spy.systems[1])
        # Ownership lock: no Skill vocabulary (bars/meters/spectra/...)
        # may leak into shared Brain code through the corrective message.
        for banned in ("spectrograms", "bars, meters", "panels or grids",
                       "stand a device in for the number"):
            self.assertNotIn(banned, spy.systems[1])

        # Hex colors are digits on the wire but colors to the engine.
        hexed = "A gradient background #FF5733 to #33FF57, no text."
        hx = Spy()
        hx.systems = []
        gen2 = G.OpenAIImagePromptGenerator(adapter=type(
            "H", (), {"complete": lambda self, system, user, **kw: hexed})())
        self.assertEqual(
            gen2.generate_prompt("t", BRAND, {}, self.PAYLOAD), hexed)

        # Both attempts dirty: fail closed, never publish a figure.
        class AlwaysDirty:
            def __init__(self):
                self.systems = []

            def complete(self, system, user, **kw):
                self.systems.append(system)
                return leaky
        dirty = AlwaysDirty()
        with self.assertRaises(Exception) as ctx:
            G.OpenAIImagePromptGenerator(adapter=dirty).generate_prompt(
                "Understanding Loudness Basics", BRAND, {}, payload, concept)
        self.assertIn("payload-numeric-leak", str(ctx.exception))
        self.assertEqual(len(dirty.systems), 2)

    def test_numeric_fragment_ignores_hex_colors(self):
        self.assertEqual(G._bare_numbers("portrait 4:5, centered"), [])
        self.assertEqual(G._bare_numbers("#FF5733 #33FF57"), [])
        self.assertEqual(sorted(G._bare_numbers("around -14 to -11")),
                         ["11", "14"])
        self.assertTrue(G._payload_leak(
            "#FF5733 with -14 LUFS", {"14"}))

    def test_concept_step_receives_the_recent_topic_history(self):
        # The novelty ledger already rides the run. The concept step is
        # where a subject gets chosen, so the history has to reach it as
        # DATA. What counts as repetition stays in the active Skills.
        class Spy:
            def __init__(self):
                self.system = ""

            def complete(self, system, user, **kw):
                self.system = system
                return json.dumps({
                    "meaning": "the viewer hears the difference a master "
                               "makes to the same track",
                    "subject": "a mastering console with two meters",
                    "relationship": "the meters show the change",
                    "brief_constraints": [],
                    "art_direction": {"icon": "none", "slot": "",
                                      "palette": [], "text_align": "left"},
                })

        policy = {"recent_topics": [
            {"topic": "Understanding Loudness Basics",
             "angle": "Understand Loudness Basics"},
            {"topic": "Juzzir's Mastering Personalities",
             "angle": "Explore different mastering styles"},
            {"topic": "   ", "angle": "ignored"},
            "not a mapping",
        ]}
        spy = Spy()
        G.OpenAIImagePromptGenerator(adapter=spy).generate_concept(
            self.PAYLOAD, policy)
        self.assertIn("Already covered in this campaign", spy.system)
        self.assertIn("Understanding Loudness Basics", spy.system)
        self.assertIn("Explore different mastering styles", spy.system)
        # Blank and malformed entries never reach the model.
        self.assertNotIn("ignored", spy.system)
        # Transport only: no rule of Brain's own about repetition.
        self.assertIn("the repetition policy is stated in the active "
                      "Skills", spy.system)

        # A run without history behaves exactly as before.
        clean = Spy()
        G.OpenAIImagePromptGenerator(adapter=clean).generate_concept(
            self.PAYLOAD, {})
        self.assertNotIn("Already covered in this campaign", clean.system)

    def test_prompt_step_receives_the_recent_topic_history(self):
        # The prompt writer is the last step that can still pick a
        # visual treatment, so the same ledger reaches it as DATA.
        class Spy:
            def __init__(self):
                self.system = ""

            def complete(self, system, user, **kw):
                self.system = system
                return "a prompt"

        policy = {"recent_topics": [
            {"topic": "Understanding Loudness Basics",
             "angle": "Understand Loudness Basics"},
            {"topic": "   ", "angle": "ignored"},
            "not a mapping",
        ]}
        spy = Spy()
        G.OpenAIImagePromptGenerator(adapter=spy).generate_prompt(
            "topic", BRAND, policy, self.PAYLOAD)
        self.assertIn("Already covered in this campaign", spy.system)
        self.assertIn("Understanding Loudness Basics", spy.system)
        self.assertNotIn("ignored", spy.system)
        # Transport only: no rule of Brain's own about repetition.
        self.assertIn("the repetition policy is stated in the active "
                      "Skills", spy.system)

        # A run without history behaves exactly as before.
        clean = Spy()
        G.OpenAIImagePromptGenerator(adapter=clean).generate_prompt(
            "topic", BRAND, {}, self.PAYLOAD)
        self.assertNotIn("Already covered in this campaign", clean.system)

    def test_image_prompt_carries_no_production_zone_prescription(self):
        # Production geometry (canvas, safe areas, typography) belongs to
        # the compositor, so the prompt never tells the model to split the
        # frame or to keep subjects out of overlay zones.
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
        self.assertNotIn("Reserved areas", system)
        self.assertNotIn("headline zone", system)
        self.assertNotIn("logo zone", system)
        self.assertNotIn("icon zone", system)
        self.assertEqual(G.AD_LAYOUT["width"], 896)
        self.assertEqual(G.AD_LAYOUT["height"], 1152)

    def test_slot_mechanism_has_no_colors_and_no_taste(self):
        # The mechanism only derives WHICH declared entry belongs to this
        # run. The list and every judgment about color live in the Image
        # Skill, never in this module.
        cycle = ["#111111", "#222222", "#333333", "#444444"]
        picked = [G.slot_value(cycle, "2026-09-24T%02d:00:00+00:00" % hour, 8)
                  for hour in range(0, 24, 3)]
        self.assertEqual(len(set(picked)), len(cycle))
        self.assertTrue(all(value in cycle for value in picked))
        next_day = [G.slot_value(cycle, "2026-09-25T%02d:00:00+00:00" % hour, 8)
                    for hour in range(0, 24, 3)]
        self.assertNotEqual(picked, next_day)
        # A declared value that is not a color literal is a structural
        # error, and the engine never substitutes a color of its own.
        with self.assertRaises(ValueError):
            G.slot_value(["#123456", "not-a-color"], "2026-09-24T09:00:00+00:00")
        with self.assertRaises(ValueError):
            G.slot_value([], "2026-09-24T09:00:00+00:00")

    def test_color_check_is_structural_only(self):
        self.assertTrue(G.is_color("#0B4FD8"))
        self.assertTrue(G.is_color("  #ffffff "))
        for value in ("#FFF", "red", "", "#12345", "#1234567", None):
            self.assertFalse(G.is_color(value), value)

    def test_engine_module_declares_no_palette(self):
        import pathlib
        source = pathlib.Path(G.__file__).read_text(encoding="utf-8")
        for banned in ("AD_PALETTE_CYCLE", "palette_is_strong",
                       "palette_for_slot"):
            self.assertNotIn(banned, source)


class BudgetParametersTest(unittest.TestCase):
    """Step 1: pin A.

    max_tokens=600 and temperature=0.3 are transport parameters that must
    reach the real model adapter, and Brain must not truncate the prompt
    on the application side. This does NOT assert every Skill constraint
    survives the 600-token ceiling: that is an output-length measurement,
    taken separately against the live Skill (it does not, fully).
    """

    def _use(self, tmp):
        import skills.loader as _loader
        old = os.environ.get("BRAIN_SKILLS_DIR")
        os.environ["BRAIN_SKILLS_DIR"] = tmp
        _loader.clear_cache()
        return old

    def _restore(self, old):
        import skills.loader as _loader
        if old is None:
            os.environ.pop("BRAIN_SKILLS_DIR", None)
        else:
            os.environ["BRAIN_SKILLS_DIR"] = old
        _loader.clear_cache()

    def test_generate_prompt_passes_budget_and_does_not_truncate(self):
        import tempfile
        import yaml
        import skills.loader as _loader
        tmp = tempfile.mkdtemp(prefix="tbra_budget_")
        old = self._use(tmp)
        try:
            with open(os.path.join(tmp, "image.yaml"), "w",
                      encoding="utf-8") as f:
                yaml.safe_dump({
                    "id": "image-x1", "name": "X", "type": "image",
                    "version": 1,
                    "instructions": "PLAIN STYLE. No people. Flat color.",
                }, f)
            _loader.clear_cache()

            class Spy:
                def __init__(self, raw):
                    self.raw = raw
                    self.kw = None
                    self.system = ""

                def complete(self, system, user, **kw):
                    self.system = system
                    self.kw = dict(kw)
                    return self.raw

            raw = ("PLAIN STYLE. A single ceramic mug on a flat teal ground, "
                   "centered, soft shadow, portrait 4:5.")
            spy = Spy(raw)
            out = G.OpenAIImagePromptGenerator(adapter=spy).generate_prompt(
                "topic", BRAND, {}, {"facts": ["a 250 ml cup"]})
            # A reached the adapter.
            self.assertEqual(spy.kw.get("temperature"), 0.3)
            self.assertEqual(spy.kw.get("max_tokens"), 600)
            # Brain applied no application-side truncation: the returned
            # prompt is the adapter output minus whitespace/scaffolding
            # handling only.
            self.assertEqual(out, raw.strip())
            # The Skill reached the real assembly path.
            self.assertIn("PLAIN STYLE", spy.system)
        finally:
            self._restore(old)


class TwoChannelSkillIsolationTest(unittest.TestCase):
    """Step 4: two distinct Image Skills must drive two different visual
    directions through the SAME shared prompt assembly, in both the
    concept and the final-prompt stages, with no cross-leak. The adapter
    derives its answer from the Skill it received (not from hardcoded
    output), which is what proves the Skill - not the test - drove the
    result.
    """

    ALPHA = "CHANNEL-ALPHA-STYLE. Warm photographic look, real people, shallow depth of field."
    BETA = "CHANNEL-BETA-STYLE. Flat vector look, no people ever, monochrome, hard edges."
    NAME_A = "chan-alpha"
    NAME_B = "chan-beta"

    def _use(self, tmp):
        import skills.loader as _loader
        old = os.environ.get("BRAIN_SKILLS_DIR")
        os.environ["BRAIN_SKILLS_DIR"] = tmp
        _loader.clear_cache()
        return old

    def _restore(self, old):
        import skills.loader as _loader
        if old is None:
            os.environ.pop("BRAIN_SKILLS_DIR", None)
        else:
            os.environ["BRAIN_SKILLS_DIR"] = old
        _loader.clear_cache()

    def _write(self, tmp, name, instructions):
        import yaml
        with open(os.path.join(tmp, "image.%s.yaml" % name), "w",
                  encoding="utf-8") as f:
            yaml.safe_dump({
                "id": "image-%s" % name, "name": name, "type": "image",
                "version": 1, "instructions": instructions,
            }, f)

    def test_channels_diverge_without_cross_leak(self):
        import tempfile
        import skills.loader as _loader
        tmp = tempfile.mkdtemp(prefix="tbra_2chan_")
        old = self._use(tmp)
        try:
            self._write(tmp, self.NAME_A, self.ALPHA)
            self._write(tmp, self.NAME_B, self.BETA)
            _loader.clear_cache()

            payload = {
                "topic": "Mastering preview",
                "angle": "compare the mixes before paying",
                "facts": ["the preview compares the original and mastered"],
                "audience": "musicians", "media_intent": "image",
            }

            class SkillEchoAdapter:
                def __init__(self):
                    self.systems = []

                def complete(self, system, user, **kw):
                    self.systems.append(system)
                    if self.ALPHA in system:
                        style = self.ALPHA
                    elif self.BETA in system:
                        style = self.BETA
                    else:
                        style = "UNKNOWN-STYLE"
                    if user.startswith("Post to illustrate"):
                        return json.dumps({
                            "meaning": "%s mastering visual concept" % style,
                            "subject": "%s mastering scene" % style,
                            "relationship": "the scene shows mastering",
                            "brief_constraints": [],
                            "art_direction": {"icon": "none", "slot": "",
                                              "palette": [], "text_align": "left"},
                        })
                    return "%s prompt, single subject, portrait 4:5." % style

            SkillEchoAdapter.ALPHA = self.ALPHA
            SkillEchoAdapter.BETA = self.BETA

            a = SkillEchoAdapter()
            genA = G.OpenAIImagePromptGenerator(adapter=a)
            conceptA = genA.generate_concept(
                payload, {"skills": {"image": self.NAME_A}})
            promptA = genA.generate_prompt(
                "Mastering preview", BRAND,
                {"skills": {"image": self.NAME_A}}, payload, conceptA)

            b = SkillEchoAdapter()
            genB = G.OpenAIImagePromptGenerator(adapter=b)
            conceptB = genB.generate_concept(
                payload, {"skills": {"image": self.NAME_B}})
            promptB = genB.generate_prompt(
                "Mastering preview", BRAND,
                {"skills": {"image": self.NAME_B}}, payload, conceptB)

            # Each channel's own style drove both stages.
            self.assertIn(self.ALPHA, conceptA["meaning"])
            self.assertIn(self.ALPHA, promptA)
            self.assertIn(self.BETA, conceptB["meaning"])
            self.assertIn(self.BETA, promptB)
            # No cross-leak: A never carries B's style and vice versa.
            self.assertNotIn(self.BETA, conceptA["meaning"])
            self.assertNotIn(self.BETA, promptA)
            self.assertNotIn(self.ALPHA, conceptB["meaning"])
            self.assertNotIn(self.ALPHA, promptB)
            # The Skill text actually reached the assembled system prompt,
            # so the routing is real (not a fallback default).
            self.assertIn(self.ALPHA, a.systems[0])
            self.assertIn(self.BETA, b.systems[0])
        finally:
            self._restore(old)


class StyleBlockAssemblyTest(unittest.TestCase):
    """The Skill owns the style block; Brain carries it as data.

    Measured cause: when the LLM wrote style AND scene together it
    diluted the style wording and the engine drifted to semi-real
    renders. With a fixed block appended literally, short scenes held
    the flat band. So the assembly is deterministic: the model writes
    the scene, and the Skill's block follows byte-for-byte.
    """

    BLOCK = ("professional editorial designer illustration, 2D flat vector "
             "artwork, no photorealism, no 3D, no gradients")

    def _use(self, tmp):
        import skills.loader as _loader
        old = os.environ.get("BRAIN_SKILLS_DIR")
        os.environ["BRAIN_SKILLS_DIR"] = tmp
        _loader.clear_cache()
        return old

    def _restore(self, old):
        import skills.loader as _loader
        if old is None:
            os.environ.pop("BRAIN_SKILLS_DIR", None)
        else:
            os.environ["BRAIN_SKILLS_DIR"] = old
        _loader.clear_cache()

    def _skill_dir(self, tmp, *, block):
        import yaml
        doc = {"id": "image-x1", "name": "X", "type": "image",
               "version": 1,
               "instructions": "SCENE-ONLY SKILL. Write the scene only."}
        if block is not None:
            doc["config"] = {"style_block": block}
        with open(os.path.join(tmp, "image.yaml"), "w",
                  encoding="utf-8") as f:
            yaml.safe_dump(doc, f, allow_unicode=True)

    class _Adapter:
        def __init__(self, raw):
            self.raw = raw
            self.system = ""

        def complete(self, system, user, **kw):
            self.system = system
            return self.raw

    def test_scene_plus_exact_block_in_that_order(self):
        import tempfile
        tmp = tempfile.mkdtemp(prefix="tbra_block_")
        old = self._use(tmp)
        try:
            self._skill_dir(tmp, block=self.BLOCK)
            scene = ("an engineer separating two tangled signal paths into "
                     "one clean path")
            out = G.OpenAIImagePromptGenerator(
                adapter=self._Adapter(scene)).generate_prompt(
                    "loudness", BRAND, {}, {})
            self.assertTrue(out.startswith(scene))
            self.assertIn(self.BLOCK, out)
            # Byte-identical, appended after the scene.
            self.assertEqual(out, scene + "\n\n" + self.BLOCK)
        finally:
            self._restore(old)

    def test_block_survives_a_model_that_echoes_style_wording(self):
        # Even if the model tries to append its own style words, the
        # block still arrives verbatim; Brain never rewrites it.
        import tempfile
        tmp = tempfile.mkdtemp(prefix="tbra_block_")
        old = self._use(tmp)
        try:
            self._skill_dir(tmp, block=self.BLOCK)
            scene = ("a wide flat studio scene with a character at a desk, "
                     "flat vector illustration, 2d, no 3d")
            out = G.OpenAIImagePromptGenerator(
                adapter=self._Adapter(scene)).generate_prompt(
                    "vinyl", BRAND, {}, {})
            self.assertTrue(out.endswith(self.BLOCK))
        finally:
            self._restore(old)

    def test_request_asks_for_the_scene_only(self):
        import tempfile
        tmp = tempfile.mkdtemp(prefix="tbra_block_")
        old = self._use(tmp)
        try:
            self._skill_dir(tmp, block=self.BLOCK)
            spy = self._Adapter("a scene")
            G.OpenAIImagePromptGenerator(adapter=spy).generate_prompt(
                "loudness", BRAND, {}, {})
            self.assertIn("ONLY the visual scene", spy.system)
            self.assertIn("Return the scene only", spy.system)
        finally:
            self._restore(old)

    def test_no_block_keeps_the_previous_prompt_contract(self):
        # A Skill that declares no block keeps the old behaviour: the
        # model returns the whole prompt and nothing is appended.
        import tempfile
        tmp = tempfile.mkdtemp(prefix="tbra_block_")
        old = self._use(tmp)
        try:
            self._skill_dir(tmp, block=None)
            raw = "PLAIN STYLE. A mug on a teal ground."
            out = G.OpenAIImagePromptGenerator(
                adapter=self._Adapter(raw)).generate_prompt(
                    "topic", BRAND, {}, {})
            self.assertEqual(out, raw.strip())
        finally:
            self._restore(old)

    def test_brain_hardcodes_no_visual_style_words(self):
        # Ownership guard: the block must be Skill data, never a Brain
        # default. Brain may name the concept of a block, but must not
        # ship a Juzzir visual vocabulary of its own.
        import inspect
        src = inspect.getsource(G)
        for banned in ("professional editorial designer illustration",
                       "editorial stock illustration",
                       "no volumetric lighting",
                       "balanced asymmetrical composition"):
            self.assertNotIn(banned, src)


if __name__ == "__main__":
    unittest.main()

