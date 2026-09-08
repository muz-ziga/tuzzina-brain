"""R1 tests: RSS/Atom collector + monitoring state. All transport
is faked (no network, no DNS); time is an explicit string. The
12 required fixture cases plus lifecycle/commit/security proofs."""
import os
import socket
import sys
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from g1 import rss as R
from research.monitor import (CollectionResult, MemoryStateStore, NEW,
                              UNCHANGED, UPDATED, collect)

RSS3 = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<title>Spike Feed</title><link>http://feed.test/</link>
<item><title>Alpha post</title><link>http://feed.test/a?utm_source=x</link>
<guid>g-a-1</guid><pubDate>Mon, 08 Sep 2026 09:00:00 +0000</pubDate>
<description><![CDATA[<p>Alpha body text here.</p>]]></description></item>
<item><title>Beta post</title><link>http://feed.test/b</link>
<guid>g-b-2</guid><pubDate>Mon, 08 Sep 2026 10:00:00 +0000</pubDate>
<description>Beta body text here.</description></item>
<item><title>Gamma post</title><link>http://feed.test/c</link>
<guid>g-c-3</guid><pubDate>Mon, 08 Sep 2026 11:00:00 +0000</pubDate>
<description>Gamma body text here.</description></item>
</channel></rss>"""

ATOM3 = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
<title>Spike Atom</title><link rel="alternate" href="http://feed.test/"/>
<entry><title>One</title><link rel="alternate" href="http://feed.test/1"/>
<id>atom-1</id><published>2026-09-08T09:00:00Z</published>
<updated>2026-09-08T09:05:00Z</updated>
<content type="html">&lt;p&gt;One body.&lt;/p&gt;</content></entry>
<entry><title>Two</title><link rel="alternate" href="http://feed.test/2"/>
<id>atom-2</id><published>2026-09-08T10:00:00Z</published>
<content>Two body.</content></entry>
<entry><title>Three</title><link rel="alternate" href="http://feed.test/3"/>
<id>atom-3</id><published>2026-09-08T11:00:00Z</published>
<summary>Three summary.</summary></entry>
</feed>"""

NOGUID = """<rss version="2.0"><channel><title>N</title>
<item><title>Solo</title><link>HTTP://Feed.Test/Solo/</link>
<description>Solo body.</description></item>
</channel></rss>"""

NOLINK = """<rss version="2.0"><channel><title>N</title>
<item><title>Lonely</title><description>Lonely body words.</description></item>
<item><title>Lonely</title><description>Lonely body words.</description></item>
</channel></rss>"""

DUPGUID = """<rss version="2.0"><channel><title>N</title>
<item><title>A</title><link>http://feed.test/a</link><guid>dup-1</guid>
<description>Same body.</description></item>
<item><title>A</title><link>http://feed.test/a</link><guid>dup-1</guid>
<description>Same body.</description></item>
</channel></rss>"""

NODATES = """<rss version="2.0"><channel><title>N</title>
<item><title>Dateless</title><link>http://feed.test/d</link>
<guid>g-d</guid><description>Body.</description></item>
</channel></rss>"""

MALFORMED = "<rss><channel><broken"
EMPTYFEED = "<rss version=\"2.0\"><channel><title>E</title></channel></rss>"

NOW = "2026-09-08T12:00:00+00:00"


class FakeResp:
    def __init__(self, body=b"", headers=None):
        self._body = body if isinstance(body, bytes) else \
            body.encode("utf-8")
        self.headers = headers or {}

    def read(self, n=-1):
        return self._body if n is None or n < 0 else self._body[:n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeOpener:
    routes = {}

    def open(self, req, timeout=None):
        url = req.full_url
        route = self.routes[url]  # KeyError = test bug (loud)
        if route[0] == "ok":
            _, body, ctype = route
            return FakeResp(body, {"Content-Type": ctype})
        if route[0] == "http":
            raise urllib.error.HTTPError(url, route[1], "e", {}, None)
        raise route[1]


_REAL_GETADDRINFO = socket.getaddrinfo
_REAL_OPENER = R._opener


def _public_dns(host, port, *a, **k):
    return [(2, 1, 6, "", ("93.184.216.34", 0))]


class RssCase(unittest.TestCase):
    def setUp(self):
        FakeOpener.routes = {}
        R._opener = FakeOpener
        socket.getaddrinfo = _public_dns

    def tearDown(self):
        R._opener = _REAL_OPENER
        socket.getaddrinfo = _REAL_GETADDRINFO

    def _route(self, url, body, ctype="application/rss+xml"):
        FakeOpener.routes[url] = ("ok", body, ctype)

    def _store(self):
        return MemoryStateStore()

    def _src(self, url="http://feed.test/rss"):
        return {"url": url, "source_id": "feed.test"}

    # ---- parse + identity ----

    def test_rss_three_new_with_mapping(self):
        self._route("http://feed.test/rss", RSS3)
        items, title, _ = R.fetch_feed("http://feed.test/rss")
        self.assertEqual(len(items), 3)
        self.assertEqual(title, "Spike Feed")
        a = items[0]
        self.assertEqual(a.item_id, "g-a-1")
        self.assertEqual(a.id_kind, "guid")
        self.assertEqual(a.url, "http://feed.test/a")
        self.assertEqual(a.published_at, "2026-09-08T09:00:00+00:00")
        self.assertEqual(len(a.content_hash), 64)
        self.assertIn("Alpha body", a.text)
        self.assertNotIn("<p>", a.text)

    def test_atom_three_new(self):
        self._route("http://feed.test/atom", ATOM3,
                    "application/atom+xml")
        items, _, _ = R.fetch_feed("http://feed.test/atom")
        self.assertEqual(len(items), 3)
        self.assertEqual(items[0].item_id, "atom-1")
        self.assertEqual(items[0].published_at, "2026-09-08T09:00:00+00:00")
        self.assertEqual(items[0].updated_at, "2026-09-08T09:05:00+00:00")
        self.assertIn("One body", items[0].text)

    def test_guid_beats_link(self):
        # same guid, changed link -> same identity
        alt = RSS3.replace("http://feed.test/a?utm_source=x",
                           "http://feed.test/a-moved")
        _, _, raws = R.parse_feed(alt.encode())
        first = R.normalize_item(raws[0], "http://feed.test/rss")
        self.assertEqual(first.item_id, "g-a-1")
        self.assertEqual(first.url, "http://feed.test/a-moved")

    def test_link_identity_without_guid(self):
        self._route("http://feed.test/noguid", NOGUID)
        items, _, _ = R.fetch_feed("http://feed.test/noguid")
        self.assertEqual(items[0].id_kind, "link")
        self.assertEqual(items[0].item_id, "http://feed.test/Solo")

    def test_content_identity_without_link(self):
        self._route("http://feed.test/nolink", NOLINK)
        items, _, _ = R.fetch_feed("http://feed.test/nolink")
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].id_kind, "content")
        self.assertTrue(items[0].item_id.startswith("hash:"))
        self.assertEqual(items[0].item_id, items[1].item_id)

    def test_canonical_url_rules(self):
        self.assertEqual(
            R.canonical_url("HTTP://Feed.Test/Solo/?utm_source=x#frag"),
            "http://feed.test/Solo")
        self.assertEqual(R.canonical_url("https://h.test/a/"),
                         "https://h.test/a")
        self.assertEqual(R.canonical_url("not a url"), "")

    # ---- monitor lifecycle ----

    def _collect(self, body, store=None, src=None, **kw):
        self._route("http://feed.test/rss", body)
        store = store or self._store()
        r = collect(src or self._src(), store=store, now=NOW, **kw)
        return r, store

    def test_first_run_all_new(self):
        r, _ = self._collect(RSS3)
        self.assertEqual(r.error, "")
        self.assertEqual([c.kind for c in r.items], ["NEW"] * 3)
        self.assertEqual(len(r.new_items()), 3)

    def test_second_run_idempotent(self):
        store = self._store()
        r1, _ = self._collect(RSS3, store=store)
        r1.commit(store)
        r2, _ = self._collect(RSS3, store=store)
        self.assertEqual([c.kind for c in r2.items],
                         ["UNCHANGED"] * 3)
        self.assertEqual(r2.new_items(), [])

    def test_no_commit_no_advance(self):
        store = self._store()
        self._collect(RSS3, store=store)  # no commit
        r, _ = self._collect(RSS3, store=store)
        self.assertEqual([c.kind for c in r.items], ["NEW"] * 3)

    def test_updated_classification(self):
        store = self._store()
        r1, _ = self._collect(RSS3, store=store)
        r1.commit(store)
        changed = RSS3.replace("Beta body text here.",
                               "Beta body CHANGED here.")
        r2, _ = self._collect(changed, store=store)
        kinds = {c.item.item_id: c.kind for c in r2.items}
        self.assertEqual(kinds["g-a-1"], "UNCHANGED")
        self.assertEqual(kinds["g-b-2"], "UPDATED")
        self.assertEqual(kinds["g-c-3"], "UNCHANGED")
        # UPDATED is not a new publication
        self.assertEqual([c.item.item_id for c in r2.new_items()], [])
        # hash refreshed, first_seen preserved
        seen = store.load("feed.test").seen
        self.assertIn("g-b-2", seen)
        r2.commit(store)
        self.assertEqual(store.load("feed.test").seen["g-b-2"]["hash"],
                         [c.item.content_hash for c in r2.items
                          if c.item.item_id == "g-b-2"][0])
        r3, _ = self._collect(changed, store=store)
        self.assertTrue(all(c.kind == "UNCHANGED" for c in r3.items))

    def test_intermediate_unseen_not_skipped(self):
        # THE autopost failure mode: an older-but-unseen item must
        # surface as NEW even when a newer item is already known.
        store = self._store()
        v1 = RSS3  # A,B,C known after commit
        r1, _ = self._collect(v1, store=store)
        r1.commit(store)
        v2 = v1.replace(
            '<item><title>Alpha post</title>',
            '<item><title>Zed older post</title>'
            '<link>http://feed.test/z</link><guid>g-z-0</guid>'
            '<pubDate>Sun, 07 Sep 2026 08:00:00 +0000</pubDate>'
            '<description>Zed body.</description></item>'
            '<item><title>Alpha post</title>')
        r2, _ = self._collect(v2, store=store)
        kinds = {c.item.item_id: c.kind for c in r2.items}
        self.assertEqual(kinds["g-z-0"], "NEW")
        self.assertEqual(kinds["g-a-1"], "UNCHANGED")

    def test_missing_dates(self):
        self._route("http://feed.test/nodates", NODATES)
        store = self._store()
        src = {"url": "http://feed.test/nodates",
               "source_id": "nodates"}
        r1 = collect(src, store=store, now=NOW)
        self.assertEqual(r1.items[0].item.published_at, "")
        self.assertEqual(r1.items[0].kind, "NEW")
        r1.commit(store)
        self.assertEqual(store.load("nodates").last_seen_published_at,
                         "")
        r2 = collect(src, store=store, now=NOW)
        self.assertEqual(r2.items[0].kind, "UNCHANGED")

    def test_horizon_filters_old(self):
        store = self._store()
        r, _ = self._collect(RSS3, store=store, horizon_days=30)
        self.assertEqual(len(r.items), 3)  # recent enough
        old = RSS3.replace("2026", "2020")
        self._route("http://feed.test/rss", old)
        r2 = collect(self._src(), store=self._store(), now=NOW,
                     horizon_days=30)
        self.assertEqual(r2.items, [])

    def test_max_items_cap(self):
        store = self._store()
        r, _ = self._collect(RSS3, store=store, max_items=2)
        self.assertEqual(len(r.items), 2)

    def test_duplicate_entries_in_feed(self):
        self._route("http://feed.test/dup", DUPGUID)
        store = self._store()
        r = collect({"url": "http://feed.test/dup",
                     "source_id": "dup"}, store=store, now=NOW)
        self.assertEqual([c.kind for c in r.items], ["NEW", "UNCHANGED"])

    # ---- failures ----

    def test_malformed_xml(self):
        self._route("http://feed.test/bad", MALFORMED, "text/xml")
        store = self._store()
        r = collect({"url": "http://feed.test/bad",
                     "source_id": "bad"}, store=store, now=NOW)
        self.assertEqual(r.items, [])
        self.assertEqual(r.error, "malformed-xml")
        r.commit(store)  # error metadata commits, cursor does not
        st = store.load("bad")
        self.assertEqual(st.last_error, "malformed-xml")
        self.assertEqual(st.seen, {})
        self.assertEqual(st.last_checked_at, NOW)

    def test_empty_feed(self):
        self._route("http://feed.test/empty", EMPTYFEED)
        r = collect({"url": "http://feed.test/empty",
                     "source_id": "e"}, store=self._store(), now=NOW)
        self.assertEqual(r.error, "empty-feed")

    def test_http_errors(self):
        FakeOpener.routes["http://feed.test/404"] = ("http", 404)
        FakeOpener.routes["http://feed.test/500"] = ("http", 500)
        for url, want in (("http://feed.test/404", "http-404"),
                          ("http://feed.test/500", "http-500")):
            r = collect({"url": url, "source_id": "h"},
                        store=self._store(), now=NOW)
            self.assertEqual(r.error, want, url)

    def test_timeout(self):
        class Boom:
            def open(self, req, timeout=None):
                raise TimeoutError("slow")
        old = R._opener
        R._opener = Boom
        try:
            r = collect({"url": "http://feed.test/t",
                         "source_id": "t"}, store=self._store(), now=NOW)
            self.assertEqual(r.error, "timeout")
        finally:
            R._opener = old

    def test_oversized(self):
        FakeOpener.routes["http://feed.test/big"] = (
            "ok", "x" * (R.MAX_BYTES + 10), "application/rss+xml")
        r = collect({"url": "http://feed.test/big", "source_id": "b"},
                    store=self._store(), now=NOW)
        self.assertEqual(r.error, "oversized")

    def test_redirect_cap(self):
        h = R._CappedRedirect()
        req = urllib.request.Request("http://feed.test/r")
        cur = req
        for _ in range(R.MAX_REDIRECTS):
            cur = h.redirect_request(cur, None, 302, "m", {}, cur.full_url)
            self.assertIsNotNone(cur)
        with self.assertRaises(R.FeedError):
            h.redirect_request(cur, None, 302, "m", {}, cur.full_url)

    # ---- security ----

    def test_ssrf_refusals(self):
        # Literals/schemes refuse before any DNS; restore the real
        # resolver so localhost resolves to itself here.
        old = socket.getaddrinfo
        socket.getaddrinfo = _REAL_GETADDRINFO
        try:
            for url in ("file:///etc/passwd", "ftp://feed.test/x",
                        "http://localhost/x", "http://127.0.0.1/x",
                        "http://[::1]/x", "javascript:alert(1)"):
                with self.assertRaises(R.FeedError, msg=url):
                    R.fetch_feed(url)
        finally:
            socket.getaddrinfo = old

    def test_private_via_dns_refused(self):
        def _priv(host, port, *a, **k):
            return [(2, 1, 6, "", ("10.1.2.3", 0))]
        old = socket.getaddrinfo
        socket.getaddrinfo = _priv
        try:
            with self.assertRaises(R.FeedError):
                R.fetch_feed("http://evil.test/feed")
        finally:
            socket.getaddrinfo = old

    # ---- adapter ----

    def test_adapter_maps_sourceitem(self):
        self._route("http://feed.test/rss", RSS3)
        res = R.RssAdapter().extract("http://feed.test/rss", 2)
        self.assertEqual(res.meta["requested"], 2)
        self.assertEqual(res.meta["returned"], 2)
        self.assertFalse(res.meta["partial"])
        it = res.items[0]
        self.assertEqual(it.source_type, "rss")
        self.assertEqual(it.item_id, "g-a-1")
        self.assertEqual(len(it.content_hash), 64)
        self.assertEqual(it.published_at, "2026-09-08T09:00:00+00:00")

    def test_adapter_failure_partial(self):
        FakeOpener.routes["http://feed.test/404"] = ("http", 404)
        res = R.RssAdapter().extract("http://feed.test/404", 3)
        self.assertEqual(res.items, [])
        self.assertTrue(res.meta["partial"])
        self.assertEqual(res.meta["reason"], "http-404")

    def test_commit_contract(self):
        store = self._store()
        r, _ = self._collect(RSS3, store=store)
        with self.assertRaises(ValueError):
            CollectionResult("x", [], NOW, "", None).commit(store)


if __name__ == "__main__":
    unittest.main()
