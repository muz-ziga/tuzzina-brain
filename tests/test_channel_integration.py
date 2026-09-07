import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from channels.profile import (Brand, ChannelProfile, HashtagPolicy,
                              PlatformSettings, resolve)
from channels.loader import load_channel_profile
from g2 import generators as G
from g2.pipeline import build_package
from contracts import SourceItem


def _project():
    return {
        "brand": {"name": "Juzzir", "tone": "informative",
                  "audience": "general Arabic audience",
                  "banned_words": [], "preferred_words": ["juzzir"],
                  "cta_style": "Learn more"},
        "language": {"default": "ar"},
        "hashtags": {"enabled": True,
                     "per_platform": {"facebook": {"max": 5}}},
        "links_policy": "hide",
        "sources": [],
        "schedule": {"timezone": "Europe/Madrid", "days": [], "times": []},
    }


def _item():
    return SourceItem(source_id="s1", source_type="website",
                      source_url="https://www.juzzir.com/x",
                      title="Juzzir — AI Audio Mastering",
                      text="One Mix. Multiple Personalities. Upload your track.",
                      images=[])


class IntegrationTest(unittest.TestCase):
    def test_resolved_payload_matches_facebook_dto(self):
        ch = ChannelProfile(
            name="Juzzir Facebook", platform="facebook", language="ar",
            brand=Brand(tone="informative, slightly playful",
                        audience="music producers, Arabic creators",
                        cta_style="Try free"),
            hashtags=HashtagPolicy(enabled=True, max=3,
                                    preferred=["juzzir", "mastering"]),
            links_policy="hide",
            platform_settings=PlatformSettings(
                platform="facebook",
                payload={"__type": "facebook", "post_type": "post"}),
        )
        cfg = resolve(_project(), ch)
        pkg = build_package(_item(), cfg["brand"], cfg["language"],
                            cfg["hashtags"], cfg["links_policy"],
                            G.MockTextGenerator(), G.MockImageGenerator(),
                            platform=cfg["platform"],
                            platform_settings=cfg["platform_settings"])
        # Tuzzina Facebook DTO accepts these
        self.assertEqual(pkg.platform, "facebook")
        self.assertEqual(pkg.settings["__type"], "facebook")
        self.assertEqual(pkg.settings["post_type"], "post")
        # Channel hashtag policy applied
        self.assertEqual(len(pkg.hashtags), 3)
        self.assertIn("#juzzir", pkg.hashtags)
        self.assertIn("#mastering", pkg.hashtags)
        # Brand channel-overridden tone reached generator
        self.assertIn("informative, slightly playful", pkg.content)
        # Links policy hides source URL
        self.assertNotIn("juzzir.com/x", pkg.content)
        # Media fallback to mock generator (no extracted image)
        self.assertEqual(pkg.media[0]["kind"], "generator")

    def test_project_defaults_still_work_without_channel_overrides(self):
        ch = ChannelProfile(
            name="Juzzir Facebook", platform="facebook", language="",
            brand=Brand(), hashtags=HashtagPolicy(),
            links_policy="",
            platform_settings=PlatformSettings(platform="facebook",
                                                 payload={"__type": "facebook"}),
        )
        cfg = resolve(_project(), ch)
        pkg = build_package(_item(), cfg["brand"], cfg["language"],
                            cfg["hashtags"], cfg["links_policy"],
                            G.MockTextGenerator(), G.MockImageGenerator(),
                            platform=cfg["platform"],
                            platform_settings=cfg["platform_settings"])
        # Project's preferred words used because channel preferred_words is empty
        self.assertIn("#juzzir", pkg.hashtags)
        # Hashtags capped at 5 (project's per_platform.facebook.max)
        self.assertLessEqual(len(pkg.hashtags), 5)
        # Platform settings forwarded
        self.assertEqual(pkg.settings["__type"], "facebook")

    def test_yaml_loader_integration(self):
        import tempfile
        yaml = """
name: Juzzir Facebook
platform: facebook
language: ar
brand:
  tone: "loud"
  preferred_words: ["juzzir"]
  cta_style: "Try free"
hashtags:
  max: 2
  preferred: ["juzzir"]
links_policy: hide
platform_settings:
  __type: facebook
  post_type: post
"""
        f = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
        f.write(yaml)
        f.close()
        ch = load_channel_profile(f.name)
        cfg = resolve(_project(), ch)
        self.assertEqual(cfg["brand"]["tone"], "loud")  # channel wins
        self.assertEqual(cfg["brand"]["audience"],
                         "general Arabic audience")     # project default
        self.assertEqual(cfg["hashtags"]["per_platform"]["facebook"]["max"], 2)
        self.assertEqual(cfg["hashtags"]["per_platform"]["facebook"]
                         ["preferred"], ["juzzir"])
        self.assertEqual(cfg["platform_settings"]["post_type"], "post")


if __name__ == "__main__":
    unittest.main()
