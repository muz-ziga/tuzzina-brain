import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from adapters import get_adapter
from adapters.base import UnsupportedPlatform
from adapters.facebook import FacebookAdapter
from adapters.instagram import InstagramAdapter


class FacebookCapabilitiesTest(unittest.TestCase):
    def test_caps(self):
        c = FacebookAdapter().capabilities()
        self.assertEqual(c["max_length"], 63206)
        self.assertEqual(c["post_types"], ["post", "story"])
        self.assertTrue(c["media"]["text_only_allowed"])
        self.assertTrue(c["media"]["photos"])
        self.assertTrue(c["media"]["video_mp4_to_reel"])
        self.assertEqual(c["hashtags"], "inline")
        self.assertEqual(c["mentions"], "inline")
        self.assertEqual(c["links"], "attach_allowed")
        self.assertEqual(c["media_delivery"], "url")

    def test_text_shaping(self):
        a = FacebookAdapter()
        self.assertIn("#x", a.shape_text("hi", ["#x"]))
        self.assertEqual(len(a.shape_text("y" * 70000, [])), 63206)

    def test_link_policies(self):
        a = FacebookAdapter()
        self.assertIn("http://u", a.apply_link("hi", "http://u", "attach", ""))
        self.assertIn("Try", a.apply_link("hi", "http://u", "cta", "Try"))
        self.assertNotIn("http", a.apply_link("hi", "http://u", "hide", ""))

    def test_media_passthrough(self):
        a = FacebookAdapter()
        self.assertEqual(a.shape_media([]), [])
        self.assertEqual(len(a.shape_media([{"kind": "url"}])), 1)

    def test_default_settings(self):
        self.assertEqual(FacebookAdapter().default_settings(),
                         {"__type": "facebook", "post_type": "post"})


class InstagramCapabilitiesTest(unittest.TestCase):
    def test_caps(self):
        c = InstagramAdapter().capabilities()
        self.assertEqual(c["max_length"], 2200)
        self.assertEqual(c["post_types"], ["post", "story"])
        self.assertTrue(c["post_type_required"])
        self.assertTrue(c["media"]["required"])
        self.assertEqual(c["media"]["carousel_min"], 2)
        self.assertEqual(c["media"]["carousel_max"], 10)
        self.assertTrue(c["media"]["story_single_picture"])
        self.assertEqual(c["hashtags"], "inline")
        self.assertEqual(c["mentions"], "inline")
        self.assertEqual(c["links"], "unsupported")
        self.assertEqual(c["media_delivery"], "url")

    def test_text_shaping(self):
        a = InstagramAdapter()
        self.assertIn("#x", a.shape_text("hi", ["#x"]))
        self.assertEqual(len(a.shape_text("y" * 5000, [])), 2200)

    def test_media_required(self):
        with self.assertRaises(UnsupportedPlatform):
            InstagramAdapter().shape_media([])

    def test_carousel_cap(self):
        a = InstagramAdapter()
        self.assertEqual(len(a.shape_media([{"k": i} for i in range(12)])),
                         10)

    def test_link_policy(self):
        a = InstagramAdapter()
        # attach ignored: URL must NOT appear
        self.assertNotIn("http://u",
                         a.apply_link("hi", "http://u", "attach", ""))
        # cta appends text without URL
        out = a.apply_link("hi", "http://u", "cta", "Try")
        self.assertIn("Try", out)
        self.assertNotIn("http://u", out)

    def test_default_settings(self):
        self.assertEqual(InstagramAdapter().default_settings(),
                         {"__type": "instagram", "post_type": "post"})


class AdapterSelectionTest(unittest.TestCase):
    def test_from_tuzzina_identifier(self):
        self.assertIsInstance(get_adapter("facebook"), FacebookAdapter)
        self.assertIsInstance(get_adapter("instagram"), InstagramAdapter)
        self.assertIsInstance(get_adapter("Facebook"), FacebookAdapter)

    def test_unsupported_fails_cleanly(self):
        for ident in ("tiktok", "youtube", "", "unknown"):
            with self.assertRaises(UnsupportedPlatform):
                get_adapter(ident)

    def test_no_valid_platforms_list(self):
        import adapters
        self.assertFalse(hasattr(adapters, "VALID_PLATFORMS"))
        import adapters.base as b
        self.assertFalse(hasattr(b, "VALID_PLATFORMS"))


if __name__ == "__main__":
    unittest.main()
