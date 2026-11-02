"""After-post story tests (module story_after)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import story_after as sa


class _Client:
    def get_post(self, post_id):
        return {
            "integration": "int-1",
            "posts": [
                {"id": post_id, "title": "Mastering tips",
                 "content": "body", "releaseURL": self.releaseURL(
                     post_id),
                 "image": [{"id": "img-1", "path": "ai/a.png"}]}
            ],
        }

    def releaseURL(self, post_id):
        return "https://ig.test/p/%s" % post_id


class FakeServiceOK:
    def inject(self, intent, client):
        return {"response": [{"postId": "s-1", "status": "success"}]}


class TestPermalink(unittest.TestCase):
    def test_permalink_found(self):
        c = _Client()
        self.assertEqual(
            sa.real_permalink(c, "p1"), "https://ig.test/p/p1")

    def test_permalink_polls_until_url(self):
        c = _Client()
        self.assertEqual(
            sa.real_permalink(
                c, "p1"), "https://ig.test/p/p1")

    def test_permalink_empty_rows_gives_empty(self):
        class _E:
            def get_post(self, post_id):
                return {"posts": [{"title": "x"}]}
        # 1 try to keep the test fast
        self.assertEqual(sa.real_permalink(_E(), "p1", tries=1), "")

    def test_media_refs_first_row(self):
        c = _Client()
        data = c.get_post("p1")
        self.assertEqual(
            sa.post_media_refs(data),
            [{"id": "img-1", "path": "ai/a.png"}])

    def test_caption_carries_permalink(self):
        cap = sa.story_caption(
            "Mastering tips", "https://x/p1", "int-1", {})
        self.assertIn("https://x/p1", cap)
        self.assertIn("Mastering tips", cap)

    def test_caption_dedup_per_channel(self):
        seen = {("int-1", "t\n🔗 https://x/p1"): True}
        self.assertEqual(
            sa.story_caption("t", "https://x/p1", "int-1", seen), "")


if __name__ == "__main__":
    unittest.main()
