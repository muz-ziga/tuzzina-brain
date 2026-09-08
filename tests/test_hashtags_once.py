import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from adapters.facebook import FacebookAdapter
from adapters.instagram import InstagramAdapter
from contracts import SourceItem
from g2 import generators as G
from g2.pipeline import build_package


def _item():
    return SourceItem(source_id="s", source_type="website",
                      source_url="https://www.juzzir.com/a",
                      title="New Track Out", text="Listen now",
                      images=[])


def _cfg(**over):
    d = {"brand": {"tone": "t", "banned_words": [], "preferred_words": [],
                   "cta_style": "Go"},
         "language": {"default": "ar"},
         "hashtags": {"enabled": True,
                      "per_platform": {"facebook": {"max": 5},
                                       "instagram": {"max": 5}}},
         "links_policy": "hide"}
    d.update(over)
    return d


def _final_with_tags(platform):
    adapter = (FacebookAdapter() if platform == "facebook"
               else InstagramAdapter())
    cfg = _cfg()
    pkg = build_package(_item(), cfg["brand"], cfg["language"],
                        cfg["hashtags"], cfg["links_policy"],
                        G.MockTextGenerator(), G.MockImageGenerator(),
                        platform=platform, platform_settings={},
                        limits={"max_length": adapter.capabilities()["max_length"]},
                        link_fn=adapter.apply_link)
    out = adapter.shape_text(pkg.content, pkg.hashtags)
    return out, pkg.hashtags


class HashtagOnceTest(unittest.TestCase):
    def test_facebook_exactly_once(self):
        out, tags = _final_with_tags("facebook")
        self.assertTrue(tags)
        for tag in tags:
            self.assertEqual(out.count(tag), 1, tag)

    def test_instagram_exactly_once(self):
        out, tags = _final_with_tags("instagram")
        self.assertTrue(tags)
        for tag in tags:
            self.assertEqual(out.count(tag), 1, tag)

    def test_content_without_tags_stays_clean(self):
        cfg = _cfg(hashtags={"enabled": False})
        adapter = FacebookAdapter()
        pkg = build_package(_item(), cfg["brand"], cfg["language"],
                            cfg["hashtags"], cfg["links_policy"],
                            G.MockTextGenerator(), G.MockImageGenerator(),
                            platform="facebook", platform_settings={},
                            link_fn=adapter.apply_link)
        self.assertEqual(pkg.hashtags, [])
        out = adapter.shape_text(pkg.content, pkg.hashtags)
        self.assertNotIn("#", out)
        self.assertIn("Listen now", out)

    def test_empty_tags_add_nothing(self):
        adapter = InstagramAdapter()
        out = adapter.shape_text("plain body", [])
        self.assertEqual(out, "plain body")

    def test_preferred_max_still_work(self):
        cfg = _cfg(hashtags={"enabled": True,
                             "per_platform": {"facebook": {
                                 "max": 2, "preferred": ["juzzir"]}}})
        adapter = FacebookAdapter()
        pkg = build_package(_item(), cfg["brand"], cfg["language"],
                            cfg["hashtags"], cfg["links_policy"],
                            G.MockTextGenerator(), G.MockImageGenerator(),
                            platform="facebook", platform_settings={},
                            link_fn=adapter.apply_link)
        self.assertIn("#juzzir", pkg.hashtags)
        self.assertLessEqual(len(pkg.hashtags), 2)
        out = adapter.shape_text(pkg.content, pkg.hashtags)
        self.assertEqual(out.count("#juzzir"), 1)

    def test_package_content_has_no_tags(self):
        # Core contract: pkg.content is plain text, tags live in pkg.hashtags
        cfg = _cfg()
        pkg = build_package(_item(), cfg["brand"], cfg["language"],
                            cfg["hashtags"], cfg["links_policy"],
                            G.MockTextGenerator(), G.MockImageGenerator(),
                            platform="facebook", platform_settings={})
        self.assertNotIn("#", pkg.content)
        self.assertTrue(pkg.hashtags)


if __name__ == "__main__":
    unittest.main()
