import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from adapters import get_adapter
from adapters.base import UnsupportedPlatform
from adapters.facebook import FacebookAdapter
from adapters.instagram import InstagramAdapter


class FacebookTranslatorTest(unittest.TestCase):
    def test_no_capability_registry(self):
        # STEP 4: adapters are translators, not provider-truth stores.
        a = FacebookAdapter()
        self.assertFalse(hasattr(a, "capabilities"))
        for stale in ("max_length", "maxLength", "post_types",
                      "media_delivery", "story_needs_attachment"):
            self.assertFalse(hasattr(a, stale),
                             f"stale provider truth leaked: {stale}")

    def test_text_assembly_unbounded(self):
        # No length constant lives here; Tuzzina validates length.
        a = FacebookAdapter()
        self.assertIn("#x", a.shape_text("hi", ["#x"]))
        self.assertEqual(len(a.shape_text("y" * 70000, [])), 70000)

    def test_link_policies(self):
        a = FacebookAdapter()
        self.assertIn("http://u", a.apply_link("hi", "http://u", "attach", ""))
        self.assertIn("Try", a.apply_link("hi", "http://u", "cta", "Try"))
        self.assertNotIn("http", a.apply_link("hi", "http://u", "hide", ""))

    def test_media_passthrough(self):
        a = FacebookAdapter()
        self.assertEqual(a.shape_media([]), [])
        self.assertEqual(len(a.shape_media([{"kind": "url"}])), 1)

    def test_link_setting(self):
        a = FacebookAdapter()
        self.assertEqual(a.link_setting("attach", "http://u"),
                         {"url": "http://u"})
        self.assertEqual(a.link_setting("hide", "http://u"), {})
        self.assertEqual(a.link_setting("attach", ""), {})

    def test_default_settings(self):
        self.assertEqual(FacebookAdapter().default_settings(),
                         {"__type": "facebook", "post_type": "post"})


class InstagramTranslatorTest(unittest.TestCase):
    def test_no_capability_registry(self):
        a = InstagramAdapter()
        self.assertFalse(hasattr(a, "capabilities"))
        for stale in ("max_length", "maxLength", "post_types",
                      "post_type_required", "carousel_max",
                      "media_delivery", "caption_single_media_only"):
            self.assertFalse(hasattr(a, stale),
                             f"stale provider truth leaked: {stale}")

    def test_text_assembly_unbounded(self):
        a = InstagramAdapter()
        self.assertIn("#x", a.shape_text("hi", ["#x"]))
        self.assertEqual(len(a.shape_text("y" * 5000, [])), 5000)

    def test_link_setting(self):
        a = InstagramAdapter()
        # Instagram has no link field; attach is meaningless here.
        self.assertEqual(a.link_setting("attach", "http://u"), {})
        self.assertEqual(a.link_setting("cta", "http://u"), {})

    def test_media_passthrough(self):
        # Media validity is owned by Tuzzina: refs pass through,
        # including empty (Tuzzina refuses authoritatively).
        a = InstagramAdapter()
        self.assertEqual(a.shape_media([]), [])
        self.assertEqual(len(a.shape_media([{"k": i} for i in range(12)])),
                         12)

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
