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

    def test_local_image_engine_removed(self):
        # STEP 2: no local OpenAI image execution may remain anywhere
        # on the production path. The delegated marker carries no
        # credentials and emits no bytes.
        self.assertFalse(hasattr(G, "OpenAIImageGenerator"))
        self.assertTrue(hasattr(G, "TuzzinaImageGenerator"))
        with self.assertRaises(TypeError):
            G.TuzzinaImageGenerator("some-key")


if __name__ == "__main__":
    unittest.main()
