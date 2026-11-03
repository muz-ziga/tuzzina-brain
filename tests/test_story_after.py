"""After-post story tests (module story_after).

Fakes mirror the REAL public list shape (id/content/releaseURL +
nested integration object; NO title/image keys) so the tests prove
the exact contract the endpoint honors: permalink+title from the
row, media from the one-shot input (execute stage refs).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import story_after as sa
from story_after import (
    clean_media_refs,
    fetch_post_rows,
    inject_story,
    post_media_refs,
    row_target,
    row_title,
    story_caption,
)


def _row(pid, iid, url, content):
    return {"id": pid, "content": content, "releaseURL": url,
            "integration": {"id": iid, "providerIdentifier": "x"}}


class _ListClient:
    """Fake public list API: rows shaped exactly like the real
    endpoint select (no title/image/flat-integrationId keys)."""

    def __init__(self, rows):
        self._rows = rows

    def list_recent_posts(self, since_iso, until_iso):
        return {"posts": list(self._rows)}


class _InjectClient(_ListClient):
    def __init__(self, rows):
        super().__init__(rows)
        self.calls = []

    def get_integration(self, iid):
        return {"id": iid, "name": "X", "identifier": "facebook",
                "picture": None}

    def campaign_content_exists(self, run_id, content, integration_id="",
                                value_id=""):
        return {"exists": False, "sameValue": False}

    def release_claim(self, key, run_id):
        pass

    def create_post(self, posts, publish_at, post_type=None):
        self.calls.append(posts)
        return [{"postId": "s-%d" % len(self.calls), "status": "success"}]


REFS = [{"id": "img-1", "path": "ai/a.png"}]
ROWS = [
    _row("brainrun:r:0:main", "int-fb", "https://fb.test/p/1",
         "Hook line here.\nBody text."),
    _row("brainrun:r:0:int-ig:main", "int-ig", "https://ig.test/p/1",
         "Second hook.\nMore body."),
]


class RowShapeCase(unittest.TestCase):
    def test_target_from_nested_integration(self):
        self.assertEqual(row_target(ROWS[0]), "int-fb")

    def test_title_falls_back_to_content_hook(self):
        self.assertEqual(row_title(ROWS[0]), "Hook line here.")

    def test_media_refs_cleaned(self):
        self.assertEqual(clean_media_refs(REFS), REFS)
        self.assertEqual(clean_media_refs("nope"), [])
        self.assertEqual(clean_media_refs([{"id": "x"}]), [])

    def test_fetch_matches_by_id_with_permalink(self):
        rows = fetch_post_rows(
            _ListClient(ROWS), ["brainrun:r:0:main"], tries=1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["releaseURL"], "https://fb.test/p/1")

    def test_fetch_skips_rows_without_permalink(self):
        rows = fetch_post_rows(
            _ListClient([_row("p1", "int-fb", "", "body")]),
            ["p1"], tries=1)
        self.assertEqual(rows, [])


class InjectFromRealRowsCase(unittest.TestCase):
    def test_publishes_one_story_per_channel_with_link(self):
        client = _InjectClient(ROWS)
        out = inject_story(client, {}, ["brainrun:r:0:main",
                                    "brainrun:r:0:int-ig:main"],
                             [], media_refs=REFS, poll_tries=1)
        self.assertEqual(len(client.calls), 2)
        kinds = [(c[0].get("settings") or {}).get("post_type")
                 for c in client.calls]
        self.assertEqual(kinds, ["story", "story"])
        bodies = [c[0]["value"][0]["content"] for c in client.calls]
        self.assertIn("https://fb.test/p/1", bodies[0])
        self.assertIn("Hook line here.", bodies[0])
        self.assertIn("https://ig.test/p/1", bodies[1])
        imgs = [c[0]["value"][0]["image"] for c in client.calls]
        self.assertEqual(imgs[0], REFS)
        self.assertEqual(imgs[1], REFS)
        self.assertEqual(out, ["s-1", "s-2"])

    def test_no_refs_and_no_row_image_publishes_nothing(self):
        # Documents the public-endpoint ceiling: without execute
        # refs there is no media to reuse, so nothing goes out.
        client = _InjectClient(ROWS)
        out = inject_story(client, {}, ["brainrun:r:0:main"], [],
                             media_refs=None, poll_tries=1)
        self.assertEqual(out, [])
        self.assertEqual(client.calls, [])

    def test_targets_filter_respected(self):
        client = _InjectClient(ROWS)
        out = inject_story(client, {}, ["brainrun:r:0:main",
                                    "brainrun:r:0:int-ig:main"],
                             ["int-ig"], media_refs=REFS, poll_tries=1)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(out, ["s-1"])

    def test_channel_failure_never_fails(self):
        class _Fails(_InjectClient):
            def create_post(self, posts, publish_at, post_type=None):
                if posts[0]["integration"]["id"] == "int-fb":
                    raise RuntimeError("refused")
                return super().create_post(posts, publish_at, post_type)

        client = _Fails(ROWS)
        out = inject_story(client, {}, ["brainrun:r:0:main",
                                    "brainrun:r:0:int-ig:main"],
                             [], media_refs=REFS, poll_tries=1)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(out, ["s-1"])


class CaptionCase(unittest.TestCase):
    def test_caption_carries_permalink(self):
        cap = story_caption(
            "Mastering tips", "https://x/p1", "int-1", {})
        self.assertIn("https://x/p1", cap)
        self.assertIn("Mastering tips", cap)

    def test_caption_dedup_per_channel(self):
        seen = {("int-1", "t\n🔗 https://x/p1"): True}
        self.assertEqual(
            story_caption("t", "https://x/p1", "int-1", seen), "")


if __name__ == "__main__":
    unittest.main()
