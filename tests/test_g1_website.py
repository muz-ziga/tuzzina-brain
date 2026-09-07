import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import g1.website as W
from g1.website import WebsiteAdapter

LISTING = """<html><head><title>Demo Blog</title>
<meta name="description" content="A demo blog">
<link rel="icon" href="/fav.ico"></head><body>
<a href="/p/one-fantastic-article-about-tech">One fantastic article about tech trends today</a>
<a href="/p/second-great-story-on-science-daily">Second great story on science daily news</a>
<a href="/tag/x">x</a>
<img src="/i/a.jpg" alt="a">
</body></html>"""

ARTICLE = """<html><head><title>Article One Title Here</title>
<meta name="article:published_time" content="2026-01-02T10:00:00Z">
</head><body><p>First paragraph of the article body text here.</p>
<p>Second paragraph with more details and context.</p>
<img src="/i/b.png"></body></html>"""


def fake_fetch(url):
    if url.rstrip("/").endswith("/p/one-fantastic-article-about-tech"):
        return ARTICLE, url
    if url.rstrip("/").endswith("/p/second-great-story-on-science-daily"):
        return ARTICLE, url
    return LISTING, url


class G1Test(unittest.TestCase):
    def setUp(self):
        self._orig = W._fetch
        W._fetch = fake_fetch

    def tearDown(self):
        W._fetch = self._orig

    def test_extract_n_items(self):
        res = WebsiteAdapter().extract("https://demo.test", 2)
        self.assertEqual(len(res.items), 2)
        self.assertFalse(res.meta["partial"])
        self.assertEqual(res.meta["requested"], 2)

    def test_partial_result(self):
        res = WebsiteAdapter().extract("https://demo.test", 10)
        self.assertEqual(len(res.items), 2)
        self.assertTrue(res.meta["partial"])
        self.assertIn("reason", res.meta)

    def test_item_fields(self):
        res = WebsiteAdapter().extract("https://demo.test", 1)
        it = res.items[0]
        self.assertTrue(it.title)
        self.assertTrue(it.text)
        self.assertTrue(it.images[0].startswith("https://demo.test/i/"))
        self.assertEqual(it.published_at, "2026-01-02T10:00:00Z")

    def test_identity(self):
        res = WebsiteAdapter().extract("https://demo.test", 1)
        self.assertEqual(res.identity.name, "Demo Blog")
        self.assertIn("demo blog", res.identity.description.lower())
        self.assertTrue(res.identity.logo_url.endswith("/fav.ico"))

    def test_fetch_failure(self):
        W._fetch = lambda u: ("", "")
        res = WebsiteAdapter().extract("https://demo.test", 3)
        self.assertEqual(res.items, [])
        self.assertTrue(res.meta["partial"])
        self.assertEqual(res.meta["reason"], "fetch-failed-or-not-html")

    def test_duplicate_filter(self):
        same_link = "/p/one-fantastic-article-about-tech"
        html = LISTING.replace("/p/second-great-story-on-science-daily",
                               same_link)
        W._fetch = lambda u: (html, u)
        res = WebsiteAdapter().extract("https://demo.test", 5)
        self.assertEqual(len(res.items), 1)
        self.assertTrue(res.meta["partial"])

    def test_single_page(self):
        W._fetch = lambda u: (ARTICLE, u)
        res = WebsiteAdapter().extract("https://demo.test/only", 3)
        self.assertEqual(len(res.items), 1)
        self.assertTrue(res.meta["partial"])


if __name__ == "__main__":
    unittest.main()
