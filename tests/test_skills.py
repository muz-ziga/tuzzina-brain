"""Skills tests: loader contract, editorial hygiene, stage wiring.

All offline: skill fixtures live in a temp dir (BRAIN_SKILLS_DIR),
transport is faked, no keys, no network.
"""
import json
import os
import sys
import tempfile
import unittest
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from skills.editorial import clean_model_text
from skills.loader import (SkillError, clear_cache, get_skill,
                           list_skills)


def _write(dirpath, name, doc):
    import yaml
    with open(os.path.join(dirpath, name), "w",
              encoding="utf-8") as f:
        yaml.safe_dump(doc, f)


def _doc(skill_type="text", **over):
    doc = {"id": "%s-x1" % skill_type, "name": "X",
           "type": skill_type, "version": 3,
           "instructions": "Do the thing well.",
           "config": {"max_chars": 600}}
    doc.update(over)
    return doc


class LoaderCase(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get("BRAIN_SKILLS_DIR")
        self.tmp = tempfile.mkdtemp()
        os.environ["BRAIN_SKILLS_DIR"] = self.tmp
        clear_cache()

    def tearDown(self):
        clear_cache()
        if self._old is None:
            os.environ.pop("BRAIN_SKILLS_DIR", None)
        else:
            os.environ["BRAIN_SKILLS_DIR"] = self._old

    def test_load_valid(self):
        _write(self.tmp, "text.yaml", _doc())
        skill = get_skill("text")
        self.assertEqual(
            (skill["id"], skill["version"], skill["enabled"]),
            ("text-x1", 3, True))
        self.assertIn("Do the thing", skill["instructions"])

    def test_unknown_type_fails_closed(self):
        with self.assertRaises(SkillError):
            get_skill("podcast")

    def test_missing_file_fails_closed(self):
        with self.assertRaises(SkillError):
            get_skill("text")

    def test_disabled_fails_closed(self):
        _write(self.tmp, "text.yaml", _doc(enabled=False))
        with self.assertRaises(SkillError):
            get_skill("text")

    def test_invalid_documents_fail_closed(self):
        bad_docs = [
            {"name": "X", "type": "text", "version": 1,
             "instructions": "I"},  # missing id
            dict(_doc(), version=0),
            dict(_doc(), version="1"),
            dict(_doc(), instructions="  "),
            dict(_doc(), enabled="yes"),
            dict(_doc(), config=[]),
            dict(_doc(), type="video"),  # stem mismatch
            dict(_doc(), mystery="drop"),  # unknown key
        ]
        for i, doc in enumerate(bad_docs):
            _write(self.tmp, "text.yaml", doc)
            clear_cache()
            with self.assertRaises(SkillError, msg="doc %d" % i):
                get_skill("text")

    def test_list_reports_status(self):
        _write(self.tmp, "text.yaml", _doc())
        _write(self.tmp, "image.yaml", _doc("image", enabled=False))
        rows = {r["type"]: r for r in list_skills()}
        self.assertEqual(rows["text"]["status"], "ok")
        self.assertEqual(rows["image"]["status"], "disabled")
        self.assertTrue(rows["research"]["status"].startswith(
            "missing-skill"))
        self.assertTrue(rows["video"]["status"].startswith(
            "missing-skill"))
        self.assertTrue(rows["analysis"]["status"].startswith(
            "missing-skill"))

    def test_shipped_skills_load(self):
        # Deployment guard: the real skill files parse and carry
        # distinct ids/versions per type.
        os.environ.pop("BRAIN_SKILLS_DIR", None)
        clear_cache()
        seen = set()
        for skill_type in ("research", "analysis", "text", "image",
                           "video"):
            skill = get_skill(skill_type)
            self.assertTrue(skill["instructions"])
            self.assertGreaterEqual(skill["version"], 1)
            seen.add(skill["id"])
        self.assertEqual(len(seen), 5)


class EditorialCase(unittest.TestCase):
    def test_observed_double_cta(self):
        # The exact production failure shape: model echoes the
        # policy CTA line, the publisher appends it again.
        model = ("Stereo problems? Check phase.\n\n"
                 "Try it free \u2014 https://www.juzzir.com/")
        out = clean_model_text(
            model, links_policy="cta",
            policy_urls=("https://www.juzzir.com/",),
            policy_phrases=("Try it free \u2014 ",))
        self.assertEqual(out, "Stereo problems? Check phase.")
        self.assertEqual(out.count("juzzir.com"), 0)

    def test_markdown_flattened(self):
        out = clean_model_text(
            "See [the guide](https://a.test/g) and ![pic](https://a.test/p)",
            links_policy="hide")
        self.assertNotIn("[", out)
        self.assertNotIn("a.test", out)
        self.assertIn("the guide", out)

    def test_hide_drops_urls(self):
        out = clean_model_text("Read https://a.test/x now",
                               links_policy="hide")
        self.assertNotIn("a.test", out)
        self.assertIn("Read", out)

    def test_attach_keeps_first_copy_only(self):
        out = clean_model_text(
            "See https://a.test/x and again https://a.test/x end",
            links_policy="attach", policy_urls=("https://art.test/p",))
        self.assertEqual(out.count("a.test/x"), 1)

    def test_hashtags_stripped(self):
        out = clean_model_text("Great mix! #mastering #Mix",
                               links_policy="hide")
        self.assertNotIn("#", out)
        self.assertIn("Great mix!", out)

    def test_duplicate_lines_collapse(self):
        out = clean_model_text("Line one\nLine one\n\nLine two",
                               links_policy="hide")
        self.assertEqual(out, "Line one\n\nLine two")

    def test_clean_text_untouched(self):
        text = "Short helpful post.\n\nSecond paragraph here."
        self.assertEqual(
            clean_model_text(text, links_policy="hide"), text)

    def test_empty_safe(self):
        self.assertEqual(clean_model_text("", links_policy="hide"), "")
        self.assertEqual(clean_model_text("   "), "")


class ScriptConsistencyCase(unittest.TestCase):
    """Third-script noise in Arabic output fails closed (detection
    only — deleting words would mangle sentences)."""

    def test_clean_arabic_passes(self):
        from skills.editorial import assert_script, script_consistent
        text = "تأكد من توافق المزيج قبل المعالجة. Juzzir يساعد."
        self.assertTrue(script_consistent(text, "ar"))
        assert_script(text, "ar")

    def test_chinese_in_arabic_fails(self):
        from skills.editorial import assert_script, script_consistent
        text = "تأكد من الميكروفون مع الم的作品 قبل المعالجة."
        self.assertFalse(script_consistent(text, "ar"))
        with self.assertRaises(ValueError):
            assert_script(text, "ar")

    def test_cyrillic_in_arabic_fails(self):
        from skills.editorial import script_consistent
        self.assertFalse(script_consistent("مرحبا мир", "ar"))

    def test_non_arabic_langs_pass(self):
        from skills.editorial import script_consistent
        self.assertTrue(script_consistent("Hello 的作品", "en"))
        self.assertTrue(script_consistent("", "ar"))
        self.assertTrue(script_consistent("anything", ""))

    def test_pipeline_gates_mixed_script(self):
        from g2.generators import TextGenerator
        from g2.pipeline import build_package
        from contracts import SourceItem

        class Dirty(TextGenerator):
            def generate(self, title, summary, brand, policy=None):
                return "نص عربي مع 的作品 مختلطة."

        item = SourceItem(
            source_id="s", source_type="rss", source_url="u",
            title="T", text="Body words.", item_id="s",
            content_hash="h")
        with self.assertRaises(ValueError):
            build_package(
                item, {"tone": "b"}, {"default": "ar"},
                {"enabled": False}, "hide", Dirty(), None,
                platform="facebook", policy={"brand": {}})

    def test_pipeline_passes_clean_arabic(self):
        from g2.generators import TextGenerator
        from g2.pipeline import build_package
        from contracts import SourceItem

        class Clean(TextGenerator):
            def generate(self, title, summary, brand, policy=None):
                return "نص عربي نظيف ومفيد."

        item = SourceItem(
            source_id="s", source_type="rss", source_url="u",
            title="T", text="Body words.", item_id="s",
            content_hash="h")
        pkg = build_package(
            item, {"tone": "b"}, {"default": "ar"},
            {"enabled": False}, "hide", Clean(), None,
            platform="facebook", policy={"brand": {}})
        self.assertIn("نص عربي", pkg.content)


class AppendOnceCase(unittest.TestCase):
    """Link/CTA assembly happens in exactly one stage even
    though pipeline and injection both route through adapter
    link handling: the second application is a no-op."""

    def test_first_application_appends(self):
        from skills.editorial import append_once
        self.assertEqual(
            append_once("Body text", "Try it free \u2014 https://a.test/"),
            "Body text\n\nTry it free \u2014 https://a.test/")

    def test_second_application_is_noop(self):
        from skills.editorial import append_once
        once = append_once("Body text",
                           "Try it free \u2014 https://a.test/")
        self.assertEqual(append_once(once, "Try it free \u2014 "
                                     "https://a.test/"), once)

    def test_empty_addition_ignored(self):
        from skills.editorial import append_once
        self.assertEqual(append_once("Body", "  "), "Body")
        self.assertEqual(append_once("", "CTA"), "CTA")

    def test_pipeline_then_adapter_applies_once(self):
        from adapters.facebook import FacebookAdapter
        from g2.pipeline import apply_link_policy
        cta = "Try it free \u2014 https://www.juzzir.com/"
        after_pipeline = apply_link_policy(
            "Value here", "https://art.test/p", "cta", cta)
        final = FacebookAdapter().apply_link(
            after_pipeline, "", "cta", cta)
        self.assertEqual(final.count("juzzir.com"), 1)
        self.assertTrue(final.startswith("Value here"))

    def test_attach_pipeline_then_adapter_applies_once(self):
        from adapters.facebook import FacebookAdapter
        from g2.pipeline import apply_link_policy
        url = "https://art.test/piece"
        after_pipeline = apply_link_policy(
            "Value here", url, "attach", "Learn more")
        final = FacebookAdapter().apply_link(
            after_pipeline, url, "attach", "Learn more")
        self.assertEqual(final.count(url), 1)


class WiringCase(unittest.TestCase):
    def setUp(self):
        self._calls = []
        self._orig = urllib.request.urlopen
        calls = self._calls

        def _fake(req, timeout=None):
            import json as _json
            calls.append({"url": req.full_url,
                          "body": _json.loads(req.data.decode())})
            payload = _json.dumps(
                {"choices": [{"message": {"content": "ok"}}]})

            class R:
                def read(self):
                    return payload.encode()

                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

            return R()

        urllib.request.urlopen = _fake

    def tearDown(self):
        urllib.request.urlopen = self._orig

    def test_text_prompt_carries_skill_and_strategy(self):
        from g2.generators import OpenAITextGenerator
        pol = {"brand": {"tone": "bold"},
               "content": {"content_pillars": ["tips"]},
               "generation": {"prompt_style": "punchy"},
               "language": {"default": "en"}}
        OpenAITextGenerator(adapter=__import__(
            "llm.adapters", fromlist=["OpenAIAdapter"]).OpenAIAdapter(
                "k", "m")).generate("T", "S", pol["brand"], pol)
        sys_prompt = self._calls[0]["body"]["messages"][0]["content"]
        self.assertIn("will appear TWICE", sys_prompt)
        self.assertIn("Channel instructions:", sys_prompt)
        self.assertIn("punchy", sys_prompt)
        self.assertNotIn("Write one Arabic social post", sys_prompt)

    def test_pipeline_editorial_cleans_leaks(self):
        from g2.generators import TextGenerator
        from g2.pipeline import build_package
        from contracts import SourceItem

        class Dirty(TextGenerator):
            def generate(self, title, summary, brand, policy=None):
                return ("Value here https://dup.test/x and "
                        "https://dup.test/x #taggo\n"
                        "Try it free \u2014 https://www.juzzir.com/")

        item = SourceItem(
            source_id="s", source_type="rss", source_url="u",
            title="T", text="Body words.", item_id="s",
            content_hash="h")
        pkg = build_package(
            item, {"tone": "b", "cta_style": "Try it free \u2014 "
                   "https://www.juzzir.com/"}, {"default": "en"},
            {"enabled": False}, "cta", Dirty(), None,
            platform="facebook", policy={"brand": {}})
        # The publisher appends the CTA exactly once downstream;
        # the model's echoed copy is gone.
        self.assertEqual(pkg.content.count("juzzir.com"), 1)
        self.assertNotIn("#taggo", pkg.content)
        self.assertEqual(pkg.content.count("dup.test/x"), 1)
        self.assertIn("Value here", pkg.content)

    def test_research_prompt_carries_skill(self):
        import json as _json

        captured = {}

        class Cap:
            model = "m"

            def complete(self, system, user, **kw):
                captured["sys"] = system
                return _json.dumps({
                    "summary": "S", "topics": ["t"], "entities": [],
                    "findings": [{"statement": "Fact words here",
                                  "kind": "fact", "confidence": "high",
                                  "item_ids": ["g-1"],
                                  "excerpt": "Fact"}]})

        from research.models import OpenAIResearchModel
        from contracts import SourceItem
        it = SourceItem(source_id="g-1", source_type="rss",
                        source_url="u", title="T", text="Body words.",
                        item_id="g-1", content_hash="h")
        OpenAIResearchModel(adapter=Cap()).research([it])
        self.assertIn("thin pages", captured["sys"])


class AssignmentCase(unittest.TestCase):
    """One unified Skill system: stages resolve a named skill per
    type (or the global default). No embedded instruction strings:
    assignments are names, content lives only in skill files."""

    def setUp(self):
        self._old = os.environ.get("BRAIN_SKILLS_DIR")
        self.tmp = tempfile.mkdtemp()
        os.environ["BRAIN_SKILLS_DIR"] = self.tmp
        clear_cache()
        for skill_type in ("research", "analysis", "text", "image",
                           "video"):
            _write(self.tmp, skill_type + ".yaml",
                   _doc(skill_type))
        _write(self.tmp, "text.juzzir.yaml",
               _doc("text", id="text-juzzir-v1", name="Juzzir Text",
                    instructions="JUZZIR-TEXT-MARKER post."))
        _write(self.tmp, "research.juzzir.yaml",
               _doc("research", id="research-juzzir-v1",
                    name="Juzzir Research",
                    instructions="JUZZIR-RESEARCH-MARKER research."))
        _write(self.tmp, "research.politics.yaml",
               _doc("research", id="research-politics-v1",
                    name="Politics Research",
                    instructions="POLITICS-RESEARCH-MARKER research."))
        _write(self.tmp, "analysis.juzzir.yaml",
               _doc("analysis", id="analysis-juzzir-v1",
                    name="Juzzir Analysis",
                    instructions="JUZZIR-ANALYSIS-MARKER analysis."))

    def tearDown(self):
        clear_cache()
        if self._old is None:
            os.environ.pop("BRAIN_SKILLS_DIR", None)
        else:
            os.environ["BRAIN_SKILLS_DIR"] = self._old

    def test_each_stage_resolves_assigned_name(self):
        skill = get_skill("text", "juzzir")
        self.assertEqual(skill["id"], "text-juzzir-v1")
        self.assertEqual(skill["source"], "assigned")
        self.assertIn("JUZZIR-TEXT-MARKER", skill["instructions"])
        skill = get_skill("research", "politics")
        self.assertEqual(skill["id"], "research-politics-v1")
        self.assertEqual(skill["source"], "assigned")

    def test_empty_assignment_uses_default(self):
        for empty in (None, "", "   "):
            skill = get_skill("text", empty if empty != "   " else "")
            self.assertEqual(skill["id"], "text-x1")
            self.assertEqual(skill["source"], "default")

    def test_missing_slot_falls_back_global(self):
        skill = get_skill("research", None)
        self.assertEqual(skill["source"], "default")
        self.assertEqual(skill["id"], "research-x1")
        skill = get_skill("image", None)
        self.assertEqual(skill["source"], "default")

    def test_assignments_are_isolated_by_name(self):
        skill_a = get_skill("research", "juzzir")
        skill_b = get_skill("research", "politics")
        self.assertIn("JUZZIR-RESEARCH-MARKER",
                      skill_a["instructions"])
        self.assertNotIn("POLITICS-RESEARCH-MARKER",
                         skill_a["instructions"])
        self.assertIn("POLITICS-RESEARCH-MARKER",
                      skill_b["instructions"])
        self.assertNotIn("JUZZIR-RESEARCH-MARKER",
                         skill_b["instructions"])

    def test_malformed_assignments_fail_closed(self):
        # The old embedded-content path is gone: instruction
        # strings are not valid assignments.
        bad = ["CAMPAIGN-A-SKILL-PROOF research directive",
               "Do the thing well. " * 500,
               "../text", "text.yaml", ".hidden", "UPPER",
               "has space", "x" * 65, "a/b", 7, ["juzzir"],
               {"text": "juzzir"}]
        for value in bad:
            with self.assertRaises(SkillError,
                                   msg="assignment %r" % (value,)):
                get_skill("text", value)

    def test_missing_named_file_fails_closed(self):
        with self.assertRaises(SkillError):
            get_skill("text", "nope")

    def test_disabled_named_skill_fails_closed(self):
        _write(self.tmp, "text.off.yaml",
               _doc("text", id="text-off-v1", enabled=False))
        clear_cache()
        with self.assertRaises(SkillError):
            get_skill("text", "off")

    def test_named_type_mismatch_fails_closed(self):
        _write(self.tmp, "text.sneaky.yaml",
               _doc("video", id="sneaky-v1"))
        clear_cache()
        with self.assertRaises(SkillError):
            get_skill("text", "sneaky")

    def test_list_includes_named_skills(self):
        rows = [(r["type"], r["ref"]) for r in list_skills()]
        self.assertIn(("text", ""), rows)
        self.assertIn(("text", "juzzir"), rows)
        self.assertIn(("research", "politics"), rows)
        by_ref = {(r["type"], r["ref"]): r for r in list_skills()}
        self.assertEqual(by_ref[("text", "juzzir")]["id"],
                         "text-juzzir-v1")
        self.assertEqual(by_ref[("text", "")]["id"], "text-x1")

    def test_stage_prompts_carry_assigned_markers(self):
        import json as _json
        from research.analysis import OpenAIAnalysisModel
        from research.models import OpenAIResearchModel
        from g2.generators import OpenAITextGenerator
        from contracts import SourceItem

        captured = {}

        class Cap:
            model = "m"

            def complete(self, system, user, **kw):
                captured.setdefault("prompts", []).append(
                    system + "\n" + user)
                if "opportunity" in system:
                    return _json.dumps({
                        "eligible": True, "topic": "T", "angle": "A",
                        "rationale": "R", "facts": ["Fact words here"],
                        "source_item_ids": ["g-1"],
                        "content_format": "post",
                        "media_intent": "none", "audience": "a",
                        "language": "en", "priority": "normal",
                        "confidence": "high", "constraints": []})
                return _json.dumps({
                    "summary": "S", "topics": ["t"], "entities": [],
                    "findings": [{"statement": "Fact words here",
                                  "kind": "fact", "confidence": "high",
                                  "item_ids": ["g-1"],
                                  "excerpt": "Fact"}]})

        policy = {"brand": {}, "language": {}, "pillars": [],
                  "generation": {}, "media": {},
                  "skills": {"research": "juzzir",
                             "analysis": "juzzir",
                             "text": "juzzir"}}
        it = SourceItem(source_id="g-1", source_type="rss",
                        source_url="u", title="T", text="Body words.",
                        item_id="g-1", content_hash="h")
        research = OpenAIResearchModel(adapter=Cap()).research(
            [it], policy)
        out = OpenAIAnalysisModel(adapter=Cap()).analyze(
            research, policy, [it])
        self.assertTrue(out.eligible)
        OpenAITextGenerator(adapter=Cap()).generate(
            "T", "S", {}, policy)
        blob = "\n".join(captured["prompts"])
        self.assertIn("JUZZIR-RESEARCH-MARKER", blob)
        self.assertIn("JUZZIR-ANALYSIS-MARKER", blob)
        self.assertIn("JUZZIR-TEXT-MARKER", blob)
        self.assertNotIn("POLITICS-RESEARCH-MARKER", blob)

if __name__ == "__main__":
    unittest.main()
