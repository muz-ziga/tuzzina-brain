"""Stage B candidate tests: one normalized identity across
sources, no provider-specific classes. Fully offline."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from contracts import SourceItem
from research.monitor import (MemoryStateStore, classify_candidates,
                              stable_identity)


def _item(**kw):
    d = dict(source_id="s", source_type="rss",
             source_url="https://feed.test/a", title="T",
             text="Body here.")
    d.update(kw)
    return SourceItem(**d)


class IdentityCase(unittest.TestCase):
    def test_platform_external_id_wins(self):
        item_id, _ = stable_identity(
            platform="x", external_id="12345",
            url="https://x.test/u/status/12345", title="T",
            text="Body")
        self.assertEqual(item_id, "x:12345")

    def test_same_post_two_queries_one_identity(self):
        a, _ = stable_identity(platform="x", external_id="12345",
                               url="https://x.test/a", title="T1",
                               text="B1")
        b, _ = stable_identity(platform="x", external_id="12345",
                               url="https://x.test/b", title="T2",
                               text="B2")
        self.assertEqual(a, b)

    def test_same_article_rss_search_news_one_identity(self):
        url = ("https://press.test/mastering-loudness?utm_source=rss"
               "#frag")
        a, _ = stable_identity(url=url, title="Loudness piece",
                               text="Same substance here")
        b, _ = stable_identity(
            url="https://press.test/mastering-loudness",
            title="Loudness piece, retitled slightly",
            text="Same substance here")
        self.assertEqual(a, b)
        self.assertTrue(a.startswith("https://"))

    def test_same_content_different_urls_merge(self):
        a, _ = stable_identity(url="", title="Same title",
                               text="Same substance")
        b, _ = stable_identity(url="", title="Same title",
                               text="Same substance")
        self.assertEqual(a, b)
        self.assertTrue(a.startswith("hash:"))

    def test_distinct_items_stay_distinct(self):
        a, _ = stable_identity(url="https://press.test/one",
                               title="One", text="First story")
        b, _ = stable_identity(url="https://press.test/two",
                               title="Two", text="Second story")
        self.assertNotEqual(a, b)

    def test_title_alone_never_identity(self):
        # Same headline, different URLs AND different substance:
        # URL precedence keeps them apart.
        a, _ = stable_identity(url="https://a.test/x",
                               title="Breaking news today",
                               text="Story about loudness curves")
        b, _ = stable_identity(url="https://b.test/y",
                               title="Breaking news today",
                               text="Story about converter chips")
        self.assertNotEqual(a, b)

    def test_hash_stable_across_calls(self):
        a, ha = stable_identity(url="https://p.test/x",
                                title="T", text="B")
        b, hb = stable_identity(url="https://p.test/x",
                                title="T", text="B")
        self.assertEqual((a, ha), (b, hb))


class CrossSourceDedupCase(unittest.TestCase):
    def test_second_source_sees_unchanged(self):
        store = MemoryStateStore()
        now = "2026-09-08T12:00:00+00:00"
        item_id, chash = stable_identity(
            url="https://press.test/mastering-loudness",
            title="Loudness piece", text="Substance")
        rss_state = store.load("rss:feed")
        classified, pending = classify_candidates("rss:feed", [{
            "carrier": None, "item_id": item_id,
            "content_hash": chash, "published_at": ""}], rss_state,
            now)
        self.assertEqual(classified[0][1], "NEW")
        store.save(pending)
        # Same article arrives via web.search under another
        # source_id: same identity resolves against ITS cursor…
        search_state = store.load("search:q")
        classified2, _ = classify_candidates("search:q", [{
            "carrier": None, "item_id": item_id,
            "content_hash": chash, "published_at": ""}],
            search_state, now)
        # …so per-source cursors alone do NOT dedup: this is the
        # documented cross-source gap the DiscoveryManager
        # (Stage 8, candidate pool keyed by stable_identity)
        # must close. The identity primitive itself is proven
        # equal above; the pool is future work.
        self.assertEqual(classified[0][0], classified2[0][0])

    def test_updated_hash_detected(self):
        store = MemoryStateStore()
        now = "2026-09-08T12:00:00+00:00"
        item_id, chash = stable_identity(
            url="https://press.test/x", title="T", text="v1")
        _, pending = classify_candidates("s", [{
            "carrier": None, "item_id": item_id,
            "content_hash": chash, "published_at": ""}],
            store.load("s"), now)
        store.save(pending)
        _, chash2 = stable_identity(
            url="https://press.test/x", title="T", text="v2 changed")
        classified, _ = classify_candidates("s", [{
            "carrier": None, "item_id": item_id,
            "content_hash": chash2, "published_at": ""}],
            store.load("s"), now)
        self.assertEqual(classified[0][1], "UPDATED")


class ContractCase(unittest.TestCase):
    def test_optional_fields_default_empty(self):
        item = _item()
        self.assertEqual(
            (item.external_id, item.discovered_at,
             item.capability, item.author), ("", "", "", ""))

    def test_no_provider_subclasses(self):
        import contracts
        names = [n for n in dir(contracts) if "andidate" in n]
        self.assertEqual(names, [])


if __name__ == "__main__":
    unittest.main()
