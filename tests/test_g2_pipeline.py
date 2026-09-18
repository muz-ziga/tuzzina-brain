import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from contracts import SourceItem
from g2 import generators as G
from g2.pipeline import (apply_banned, apply_link_policy, build_package,
                         make_hashtags, normalize)


def item(**kw):
    d = dict(source_id="s1", source_type="website",
             source_url="https://demo.test/a", title="Big Launch Day",
             text="We launch the new platform today with fast tools.",
             images=["https://demo.test/i/a.jpg"], links=[],
             platform="website")
    d.update(kw)
    return SourceItem(**d)


BRAND = {"tone": "bold", "audience": "founders", "banned_words": ["spam"],
         "preferred_words": ["launch"], "cta_style": "Try it now"}


class G2Test(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(normalize("  a\n\n b  "), "a b")

    def test_banned_words(self):
        text, hit = apply_banned("no spam here spam", ["spam"])
        self.assertEqual(hit, ["spam"])
        self.assertNotIn("spam", text)

    def test_hashtag_rules(self):
        tags = make_hashtags("Big Launch", "fast tools", ["launch"], 3)
        self.assertIn("#launch", tags)
        self.assertLessEqual(len(tags), 3)
        self.assertTrue(all(t.startswith("#") for t in tags))

    def test_hashtag_zero(self):
        self.assertEqual(make_hashtags("t", "s", [], 0), [])

    def test_link_policies(self):
        self.assertIn("https://demo.test/a",
                      apply_link_policy("hi", "https://demo.test/a",
                                        "attach", ""))
        cta = apply_link_policy("hi", "https://demo.test/a", "cta",
                                "Try it now")
        self.assertIn("Try it now", cta)
        self.assertNotIn("https://", cta)
        self.assertEqual(apply_link_policy("hi", "https://demo.test/a",
                                           "hide", ""), "hi")

    def test_package_schema(self):
        pkg = build_package(item(), BRAND, {}, {"enabled": True,
                            "per_platform": {"facebook": {"max": 5}}},
                            "hide", G.MockTextGenerator(),
                            G.MockImageGenerator())
        self.assertTrue(pkg.content)
        self.assertEqual(pkg.platform, "facebook")
        self.assertEqual(pkg.settings["__type"], "facebook")
        self.assertTrue(pkg.media)
        self.assertTrue(pkg.hashtags)

    def test_package_uses_extracted_image(self):
        pkg = build_package(item(), BRAND, {}, {"enabled": False},
                            "hide", G.MockTextGenerator(),
                            G.MockImageGenerator())
        self.assertEqual(pkg.media[0]["kind"], "url")

    def test_package_generator_fallback(self):
        pkg = build_package(item(images=[]), BRAND, {},
                            {"enabled": False}, "hide",
                            G.MockTextGenerator(), G.MockImageGenerator())
        self.assertEqual(pkg.media[0]["kind"], "generator")

    def test_mock_png_valid(self):
        blob, name = G.MockImageGenerator().generate_png("x")
        self.assertTrue(blob.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertTrue(name.endswith(".png"))

    def test_openai_text_needs_key(self):
        with self.assertRaises(RuntimeError):
            G.OpenAITextGenerator("")

    def test_empty_platform_preferred_falls_back_to_brand(self):
        # Regression: an explicitly empty per-platform preferred
        # list must not shadow brand preferred_words (production
        # posted 5 generic tags and no #juzzir because of this).
        hs = {"enabled": True, "max": 3, "per_platform": {
            "facebook": {"max": 3, "preferred": []}}}
        brand = dict(BRAND)
        brand["preferred_words"] = ["juzzir"]
        pkg = build_package(item(platform="facebook"), brand, {},
                            hs, "hide", G.MockTextGenerator(),
                            G.MockImageGenerator())
        self.assertEqual(pkg.hashtags[0], "#juzzir")
        self.assertLessEqual(len(pkg.hashtags), 3)

    def test_missing_platform_uses_strategy_level(self):
        hs = {"enabled": True, "max": 3}
        brand = dict(BRAND)
        brand["preferred_words"] = ["juzzir"]
        pkg = build_package(item(platform="rss"), brand, {}, hs,
                            "hide", G.MockTextGenerator(),
                            G.MockImageGenerator())
        self.assertEqual(pkg.hashtags[0], "#juzzir")
        self.assertLessEqual(len(pkg.hashtags), 3)

    def test_explicit_platform_preferred_wins(self):
        hs = {"enabled": True, "max": 5, "per_platform": {
            "facebook": {"max": 5, "preferred": ["mix"]}}}
        brand = dict(BRAND)
        brand["preferred_words"] = ["juzzir"]
        pkg = build_package(item(platform="facebook"), brand, {},
                            hs, "hide", G.MockTextGenerator(),
                            G.MockImageGenerator())
        self.assertEqual(pkg.hashtags[0], "#mix")

    def test_destination_platform_wins_for_tags(self):
        # Source platform (rss) must not select hashtag rules;
        # the destination integration identifier does.
        hs = {"enabled": True, "max": 3, "per_platform": {
            "facebook": {"max": 3, "preferred": []}}}
        brand = dict(BRAND)
        brand["preferred_words"] = ["juzzir"]
        policy = {"channel_meta": {"identifier": "facebook"}}
        pkg = build_package(item(platform="rss"), brand, {}, hs,
                            "hide", G.MockTextGenerator(),
                            G.MockImageGenerator(), platform="rss",
                            policy=policy)
        self.assertEqual(pkg.platform, "facebook")
        self.assertEqual(pkg.hashtags[0], "#juzzir")
        self.assertLessEqual(len(pkg.hashtags), 3)

    def test_no_channel_meta_keeps_caller_platform(self):
        hs = {"enabled": True, "max": 3}
        pkg = build_package(item(platform="rss"), BRAND, {}, hs,
                            "hide", G.MockTextGenerator(),
                            G.MockImageGenerator(), platform="rss",
                            policy={})
        self.assertEqual(pkg.platform, "rss")

    def test_local_image_engine_removed(self):
        # STEP 2: no local OpenAI image execution may remain anywhere
        # on the production path. The delegated marker carries no
        # credentials and emits no bytes.
        self.assertFalse(hasattr(G, "OpenAIImageGenerator"))
        self.assertTrue(hasattr(G, "TuzzinaImageGenerator"))
        with self.assertRaises(TypeError):
            G.TuzzinaImageGenerator("some-key")


class FixedGen:
    """Deterministic text source emitting caller-chosen copy, so
    CTA/URL assembly is observable without any model."""

    def __init__(self, text):
        self._text = text

    def generate(self, title, summary, brand, policy=None):
        return self._text


class CtaMechanicsTest(unittest.TestCase):
    # Pins the exact publisher assembly behind the Stream Deck
    # failure: a cta_style carrying brand+URL is appended to
    # EVERY post by the pipeline (product-blind). An empty
    # cta_style appends nothing while still letting a relevant
    # model-written URL survive first-occurrence.

    def _pkg(self, text, links_policy="cta",
             cta_style="Try it free \u2014 https://www.juzzir.com/"):
        brand = {"tone": "t", "banned_words": [],
                 "preferred_words": [], "cta_style": cta_style}
        hs = {"enabled": True,
              "per_platform": {"facebook": {"max": 0}}}
        return build_package(item(), brand, {}, hs, links_policy,
                             FixedGen(text), G.MockImageGenerator())

    def test_cta_style_with_url_appended_blindly(self):
        pkg = self._pkg("Useful workflow tip")
        self.assertIn("https://www.juzzir.com/", pkg.content)
        self.assertIn("Try it free", pkg.content)

    def test_empty_cta_appends_nothing(self):
        pkg = self._pkg("Useful workflow tip", cta_style="")
        self.assertNotIn("https://", pkg.content)
        self.assertNotIn("Try it free", pkg.content)
        self.assertIn("Useful workflow tip", pkg.content)

    def test_relevant_model_url_survives_empty_cta(self):
        pkg = self._pkg("See https://www.juzzir.com/ for details",
                        cta_style="")
        self.assertIn("https://www.juzzir.com/", pkg.content)

    def test_hide_drops_model_urls(self):
        pkg = self._pkg("See https://www.juzzir.com/ for details",
                        links_policy="hide", cta_style="")
        self.assertNotIn("https://", pkg.content)


if __name__ == "__main__":
    unittest.main()
