import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from channels.profile import (Brand, ChannelProfile, HashtagPolicy,
                              PlatformSettings, _MISSING, resolve)
from channels.loader import load_channel_profile


def ch(**kw):
    """Build a ChannelProfile with sentinel 'unset' for fields not
    explicitly passed. The resolver relies on sentinel-vs-set to
    distinguish channel overrides from project defaults."""
    base = dict(
        name="x",
        platform=_MISSING,
        language=_MISSING,
        brand=Brand(),
        hashtags=HashtagPolicy(),
        links_policy=_MISSING,
        platform_settings=PlatformSettings(
            platform="facebook", payload={"__type": "facebook"}),
        extras={},
    )
    base.update(kw)
    return ChannelProfile(**base)


class ChannelTest(unittest.TestCase):
    def test_brand_override_wins(self):
        proj = {"brand": {"name": "P", "tone": "calm", "audience": "devs"}}
        c = ch(brand=Brand(tone="loud"))
        r = resolve(proj, c)
        self.assertEqual(r["brand"]["tone"], "loud")          # channel wins
        self.assertEqual(r["brand"]["audience"], "devs")      # project default
        self.assertEqual(r["brand"]["name"], "P")             # project default

    def test_brand_empty_string_is_an_override(self):
        proj = {"brand": {"name": "P", "description": "proj desc"}}
        c = ch(brand=Brand(description=""))   # explicit empty override
        self.assertEqual(resolve(proj, c)["brand"]["description"], "")

    def test_banned_words_override(self):
        proj = {"brand": {"banned_words": ["x", "y"]}}
        c = ch(brand=Brand(banned_words=["z"]))
        self.assertEqual(resolve(proj, c)["brand"]["banned_words"], ["z"])
        c2 = ch(brand=Brand(banned_words=[]))   # [] is a real override
        self.assertEqual(resolve(proj, c2)["brand"]["banned_words"], [])

    def test_hashtag_policy_override(self):
        proj = {"hashtags": {"enabled": True,
                             "per_platform": {"facebook": {"max": 5,
                                                           "preferred": ["a"]}}}}
        c = ch(hashtags=HashtagPolicy(max=2, preferred=["b"]))
        r = resolve(proj, c)
        self.assertEqual(r["hashtags"]["per_platform"]["facebook"]["max"], 2)
        self.assertEqual(r["hashtags"]["per_platform"]["facebook"]["preferred"],
                         ["b"])

    def test_language_fallback(self):
        proj = {"language": {"default": "ar"}}
        self.assertEqual(resolve(proj, ch())["language"]["default"], "ar")
        self.assertEqual(resolve(proj, ch(language="en"))
                         ["language"]["default"], "en")

    def test_links_policy_fallback(self):
        proj = {"links_policy": "cta"}
        self.assertEqual(resolve(proj, ch())["links_policy"], "cta")
        self.assertEqual(resolve(proj, ch(links_policy="hide"))
                         ["links_policy"], "hide")

    def test_platform_settings_pass_through(self):
        proj = {}
        c = ch(platform_settings=PlatformSettings(
            platform="facebook",
            payload={"__type": "facebook", "post_type": "post"}))
        r = resolve(proj, c)
        self.assertEqual(r["platform_settings"]["__type"], "facebook")
        self.assertEqual(r["platform_settings"]["post_type"], "post")

    def test_platform_propagates(self):
        self.assertEqual(resolve({}, ch())["platform"], "facebook")
        r = resolve({}, ch(platform="facebook"))
        self.assertEqual(r["platform"], "facebook")

    def test_schedule_propagates_from_project(self):
        proj = {"schedule": {"timezone": "UTC", "days": [], "times": []}}
        self.assertEqual(resolve(proj, ch())["schedule"], proj["schedule"])


class LoaderTest(unittest.TestCase):
    def _w(self, text):
        f = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
        f.write(text)
        f.close()
        return f.name

    def test_minimal(self):
        p = self._w("""
name: X
platform: facebook
""")
        c = load_channel_profile(p)
        self.assertEqual(c.name, "X")
        self.assertEqual(c.platform, "facebook")
        # 'language' and 'links_policy' are NOT set in YAML -> sentinel
        from channels.profile import _MISSING
        self.assertIs(c.language, _MISSING)
        self.assertIs(c.links_policy, _MISSING)
        self.assertEqual(c.platform_settings.payload["__type"], "facebook")

    def test_full(self):
        p = self._w("""
name: Juzzir FB
platform: facebook
language: ar
brand:
  tone: "informative"
  banned_words: ["spam"]
  preferred_words: ["juzzir"]
  cta_style: "Try free"
hashtags:
  enabled: true
  max: 3
  preferred: ["juzzir"]
links_policy: hide
platform_settings:
  __type: facebook
  post_type: post
""")
        c = load_channel_profile(p)
        self.assertEqual(c.brand.tone, "informative")
        self.assertEqual(c.brand.banned_words, ["spam"])
        self.assertEqual(c.hashtags.max, 3)
        self.assertEqual(c.hashtags.preferred, ["juzzir"])
        self.assertEqual(c.platform_settings.payload["post_type"], "post")

    def test_unknown_keys_preserved(self):
        p = self._w("""
name: X
platform: facebook
content_pillars: ["how-to", "behind-the-scenes"]
competitor_set: ["competitor-a", "competitor-b"]
image_policy: {min_resolution: 1080, format: jpg}
""")
        c = load_channel_profile(p)
        self.assertIn("content_pillars", c.extras)
        self.assertIn("competitor_set", c.extras)
        self.assertIn("image_policy", c.extras)

    def test_platform_type_mismatch_rejected(self):
        p = self._w("""
name: X
platform: facebook
platform_settings:
  __type: tiktok
""")
        with self.assertRaises(ValueError):
            load_channel_profile(p)

    def test_missing_name(self):
        p = self._w("platform: facebook\n")
        with self.assertRaises(ValueError):
            load_channel_profile(p)

    def test_invalid_platform(self):
        p = self._w("name: X\nplatform: tiktok\n")
        with self.assertRaises(ValueError):
            load_channel_profile(p)


if __name__ == "__main__":
    unittest.main()
