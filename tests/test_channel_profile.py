import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from channels.profile import (Brand, ChannelProfile, HashtagPolicy,
                              _MISSING, resolve)
from channels.loader import load_channel_profile


def ch(**kw):
    base = dict(
        integration_id="integ-xyz",
        brand=Brand(),
        hashtags=HashtagPolicy(),
        links_policy=_MISSING,
        provider_overrides={},
        extras={},
    )
    base.update(kw)
    return ChannelProfile(**base)


class ChannelTest(unittest.TestCase):
    def test_integration_id_required(self):
        self.assertEqual(ch().integration_id, "integ-xyz")

    def test_brand_override_wins(self):
        proj = {"brand": {"name": "P", "tone": "calm", "audience": "devs"}}
        c = ch(brand=Brand(tone="loud"))
        r = resolve(proj, c)
        self.assertEqual(r["brand"]["tone"], "loud")
        self.assertEqual(r["brand"]["audience"], "devs")
        self.assertEqual(r["brand"]["name"], "P")

    def test_brand_empty_string_is_an_override(self):
        proj = {"brand": {"name": "P", "description": "proj desc"}}
        c = ch(brand=Brand(description=""))
        self.assertEqual(resolve(proj, c)["brand"]["description"], "")

    def test_banned_words_override(self):
        proj = {"brand": {"banned_words": ["x", "y"]}}
        c = ch(brand=Brand(banned_words=["z"]))
        self.assertEqual(resolve(proj, c)["brand"]["banned_words"], ["z"])
        c2 = ch(brand=Brand(banned_words=[]))
        self.assertEqual(resolve(proj, c2)["brand"]["banned_words"], [])

    def test_hashtag_policy_override_uses_channel_platform_key(self):
        proj = {"hashtags": {"enabled": True,
                             "per_platform": {"facebook": {"max": 5,
                                                           "preferred": ["a"]}}}}
        c = ch(hashtags=HashtagPolicy(max=2, preferred=["b"]))
        meta = {"identifier": "facebook"}
        r = resolve(proj, c, channel_meta=meta)
        self.assertEqual(r["hashtags"]["per_platform"]["facebook"]["max"], 2)
        self.assertEqual(r["hashtags"]["per_platform"]["facebook"]["preferred"],
                         ["b"])

    def test_hashtag_policy_falls_back_to_project_default_platform(self):
        # Project-level policy uses the same platform key as the
        # channel's integration identifier.
        proj = {"hashtags": {"enabled": True,
                             "per_platform": {"facebook": {"max": 9}}}}
        c = ch()
        meta = {"identifier": "facebook"}
        r = resolve(proj, c, channel_meta=meta)
        self.assertEqual(r["hashtags"]["per_platform"]["facebook"]["max"], 9)

    def test_links_policy_fallback(self):
        proj = {"links_policy": "cta"}
        self.assertEqual(resolve(proj, ch())["links_policy"], "cta")
        self.assertEqual(resolve(proj, ch(links_policy="hide"))
                         ["links_policy"], "hide")

    def test_provider_overrides_pass_through(self):
        proj = {}
        c = ch(provider_overrides={"post_type": "post"})
        self.assertEqual(resolve(proj, c)["provider_overrides"],
                         {"post_type": "post"})

    def test_extras_preserved(self):
        proj = {}
        c = ch(extras={"content_pillars": ["a"], "competitor_set": ["b"]})
        r = resolve(proj, c)
        self.assertEqual(r["extras"]["content_pillars"], ["a"])
        self.assertEqual(r["extras"]["competitor_set"], ["b"])

    def test_integration_id_flows_through(self):
        proj = {}
        r = resolve(proj, ch(integration_id="integ-42"))
        self.assertEqual(r["integration_id"], "integ-42")

    def test_channel_meta_flows_through(self):
        meta = {"integration_id": "i1-from-tuzzina", "name": "Juzzir",
                "identifier": "facebook", "picture": "x"}
        r = resolve({}, ch(integration_id="i1-from-brain"),
                    channel_meta=meta)
        # The resolved integration_id comes from the brain YAML
        # (channel strategy claims it); the meta surfaces separately.
        self.assertEqual(r["integration_id"], "i1-from-brain")
        self.assertEqual(r["channel_meta"], meta)

    def test_no_channel_meta_no_crash(self):
        # When channel_meta is absent (e.g. tests, dry-runs), the
        # brain must still work — we fall back to project defaults.
        proj = {"language": {"default": "ar"}}
        r = resolve(proj, ch())
        self.assertEqual(r["language"]["default"], "ar")
        self.assertEqual(r["channel_meta"], {})


class LoaderTest(unittest.TestCase):
    def _w(self, text):
        f = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
        f.write(text)
        f.close()
        return f.name

    def test_minimal(self):
        p = self._w("integration_id: integ-abc\n")
        c = load_channel_profile(p)
        self.assertEqual(c.integration_id, "integ-abc")
        self.assertIs(c.links_policy, _MISSING)

    def test_full(self):
        p = self._w("""
integration_id: integ-xyz
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
provider_overrides:
  post_type: post
""")
        c = load_channel_profile(p)
        self.assertEqual(c.brand.tone, "informative")
        self.assertEqual(c.brand.banned_words, ["spam"])
        self.assertEqual(c.hashtags.max, 3)
        self.assertEqual(c.hashtags.preferred, ["juzzir"])
        self.assertEqual(c.links_policy, "hide")
        self.assertEqual(c.provider_overrides, {"post_type": "post"})

    def test_unknown_keys_preserved(self):
        p = self._w("""
integration_id: integ-1
content_pillars: ["how-to", "behind-the-scenes"]
competitor_set: ["competitor-a"]
image_policy: {min_resolution: 1080, format: jpg}
""")
        c = load_channel_profile(p)
        self.assertIn("content_pillars", c.extras)
        self.assertIn("competitor_set", c.extras)
        self.assertIn("image_policy", c.extras)

    def test_provider_overrides_must_be_mapping(self):
        p = self._w("""
integration_id: integ-1
provider_overrides: "not a mapping"
""")
        with self.assertRaises(ValueError):
            load_channel_profile(p)

    def test_missing_integration_id(self):
        p = self._w("brand: {tone: loud}\n")
        with self.assertRaises(ValueError):
            load_channel_profile(p)


if __name__ == "__main__":
    unittest.main()
