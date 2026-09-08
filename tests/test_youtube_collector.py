"""R2A tests: YouTube public channel collector. Fixtures mirror
the live-verified feed shape (yt:videoId, published/updated,
media:description, media:thumbnail). Fake transport/DNS; no
network, no keys, no OAuth."""
import os
import socket
import sys
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from g1 import rss as R
from g1 import youtube as Y
from research.monitor import MemoryStateStore, NEW, UNCHANGED, UPDATED
from research.monitor import collect as monitor_collect

CID = "UCBR8-60-B28hp2BmDPdntcQ"
FEED_URL = ("https://www.youtube.com/feeds/videos.xml"
            "?channel_id=" + CID)

YT3 = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"
 xmlns="http://www.w3.org/2005/Atom"
 xmlns:media="http://search.yahoo.com/mrss/">
<title>YouTube Channel</title>
<link rel="alternate" href="https://www.youtube.com/channel/UCBR1"/>
<entry><id>yt:video:AAA111</id><yt:videoId>AAA111</yt:videoId>
<title>First video title</title>
<link rel="alternate" href="https://www.youtube.com/watch?v=AAA111"/>
<author><name>Chan</name></author>
<published>2026-09-07T10:00:00+00:00</published>
<updated>2026-09-07T10:05:00+00:00</updated>
<media:group><media:title>First video title</media:title>
<media:content url="https://www.youtube.com/v/AAA111" type="video"/>
<media:thumbnail url="https://i1.ytimg.com/vi/AAA111/hqdefault.jpg"
 width="480" height="360"/>
<media:description>First description words here.</media:description>
</media:group></entry>
<entry><id>yt:video:BBB222</id><yt:videoId>BBB222</yt:videoId>
<title>Second video title</title>
<link rel="alternate" href="https://www.youtube.com/watch?v=BBB222"/>
<published>2026-09-08T09:00:00+00:00</published>
<updated>2026-09-08T09:00:00+00:00</updated>
<media:group>
<media:description>Second description words here.</media:description>
<media:thumbnail url="https://i1.ytimg.com/vi/BBB222/hqdefault.jpg"/>
</media:group></entry>
<entry><id>yt:video:CCC333</id><yt:videoId>CCC333</yt:videoId>
<title>Third video title</title>
<link rel="alternate" href="https://www.youtube.com/watch?v=CCC333"/>
<published>2026-09-08T11:00:00+00:00</published>
<media:group>
<media:description>Third description words here.</media:description>
</media:group></entry>
</feed>"""

NOW = "2026-09-08T12:00:00+00:00"


def yt15():
    parts = ['<?xml version="1.0"?><feed xmlns:yt='
             '"http://www.youtube.com/xml/schemas/2015" '
             'xmlns="http://www.w3.org/2005/Atom" '
             'xmlns:media="http://search.yahoo.com/mrss/">'
             "<title>C</title>"]
    for i in range(15):
        vid = f"VID{i:06d}"
        parts.append(
            f"<entry><id>yt:video:{vid}</id>"
            f"<yt:videoId>{vid}</yt:videoId><title>V{i}</title>"
            f'<link rel="alternate" '
            f'href="https://www.youtube.com/watch?v={vid}"/>'
            f"<published>2026-09-08T{i:02d}:00:00+00:00</published>"
            f"<media:group><media:description>D{i}</media:description>"
            "</media:group></entry>")
    parts.append("</feed>")
    return "".join(parts)


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
        route = self.routes[req.full_url]
        if route[0] == "ok":
            _, body, ctype = route
            return FakeResp(body, {"Content-Type": ctype})
        if route[0] == "http":
            raise urllib.error.HTTPError(req.full_url, route[1],
                                         "e", {}, None)
        raise route[1]


_REAL_GETADDRINFO = socket.getaddrinfo
_REAL_OPENER = R._opener


def _public_dns(host, port, *a, **k):
    return [(2, 1, 6, "", ("93.184.216.34", 0))]


class YtCase(unittest.TestCase):
    def setUp(self):
        FakeOpener.routes = {}
        R._opener = FakeOpener
        socket.getaddrinfo = _public_dns

    def tearDown(self):
        R._opener = _REAL_OPENER
        socket.getaddrinfo = _REAL_GETADDRINFO

    def _route(self, body, ctype="text/xml"):
        FakeOpener.routes[FEED_URL] = ("ok", body, ctype)

    def _src(self):
        return {"url": FEED_URL, "source_id": f"youtube:{CID}"}

    def test_channel_id_validation(self):
        self.assertEqual(Y.youtube_feed_url(CID), FEED_URL)
        for bad in ("", "UCshort", "UCBR8 60", "XBBR8-60-B28hp2BmDPdntcQ",
                    "UCBR8-60-B28hp2BmDPdntcQ?x=1",
                    "https://www.youtube.com/feeds/videos.xml"
                    "?channel_id=" + CID,
                    "../etc/passwd", "UC" + "A" * 23):
            with self.assertRaises(ValueError, msg=bad):
                Y.youtube_feed_url(bad)

    def test_url_not_overridable(self):
        import inspect
        sig = inspect.signature(Y.YouTubeFeedAdapter.extract)
        self.assertEqual(list(sig.parameters), ["self", "channel_id", "n"])
        # even a full URL as "channel_id" is refused before network
        res = Y.YouTubeFeedAdapter().extract(FEED_URL, 3)
        self.assertEqual(res.items, [])
        self.assertEqual(res.meta["reason"], "invalid-channel-id")

    def test_valid_feed_fields(self):
        self._route(YT3)
        items, _, _ = R.fetch_feed(FEED_URL)
        self.assertEqual(len(items), 3)
        a = items[0]
        self.assertEqual(a.item_id, "AAA111")
        self.assertEqual(a.id_kind, "guid")
        self.assertEqual(a.published_at, "2026-09-07T10:00:00+00:00")
        self.assertEqual(a.updated_at, "2026-09-07T10:05:00+00:00")
        self.assertEqual(a.title, "First video title")
        self.assertIn("First description", a.text)
        self.assertEqual(
            a.thumbnail, "https://i1.ytimg.com/vi/AAA111/hqdefault.jpg")
        self.assertEqual(
            a.url, "https://www.youtube.com/watch?v=AAA111")

    def test_videoid_preferred_over_id(self):
        # feed <id>yt:video:X</id> exists too; explicit yt:videoId wins
        self._route(YT3)
        items, _, _ = R.fetch_feed(FEED_URL)
        self.assertEqual(items[1].item_id, "BBB222")

    def test_adapter_mapping(self):
        self._route(YT3)
        res = Y.YouTubeFeedAdapter().extract(CID, 3)
        self.assertEqual(res.meta["returned"], 3)
        it = res.items[0]
        self.assertEqual(it.source_type, "youtube")
        self.assertEqual(it.platform, "youtube")
        self.assertEqual(it.source_id, "AAA111")
        self.assertEqual(it.item_id, "AAA111")
        self.assertEqual(it.images,
                         ["https://i1.ytimg.com/vi/AAA111/hqdefault.jpg"])
        self.assertTrue(it.content_hash)

    def test_monitor_first_run_new(self):
        self._route(YT3)
        store = MemoryStateStore()
        r = monitor_collect(self._src(), store=store, now=NOW)
        self.assertEqual([c.kind for c in r.items], ["NEW"] * 3)
        r.commit(store)
        self.assertEqual(store.load(f"youtube:{CID}").last_seen_item_id,
                         "CCC333")

    def test_monitor_second_run_unchanged(self):
        self._route(YT3)
        store = MemoryStateStore()
        r1 = monitor_collect(self._src(), store=store, now=NOW)
        r1.commit(store)
        r2 = monitor_collect(self._src(), store=store, now=NOW)
        self.assertEqual([c.kind for c in r2.items], ["UNCHANGED"] * 3)
        self.assertEqual(r2.new_items(), [])

    def test_monitor_updated_description(self):
        self._route(YT3)
        store = MemoryStateStore()
        r1 = monitor_collect(self._src(), store=store, now=NOW)
        r1.commit(store)
        changed = YT3.replace("Second description words here.",
                              "Second description EDITED here.")
        FakeOpener.routes[FEED_URL] = ("ok", changed, "text/xml")
        r2 = monitor_collect(self._src(), store=store, now=NOW)
        kinds = {c.item.item_id: c.kind for c in r2.items}
        self.assertEqual(kinds["BBB222"], UPDATED)
        self.assertEqual(kinds["AAA111"], UNCHANGED)
        self.assertEqual(r2.new_items(), [])

    def test_missing_videoid_fallback(self):
        no_id = YT3.replace("<yt:videoId>BBB222</yt:videoId>", "")
        no_id = no_id.replace("<id>yt:video:BBB222</id>", "")
        self._route(no_id)
        items, _, _ = R.fetch_feed(FEED_URL)
        b = [i for i in items if "BBB222" in (i.url or "")][0]
        # falls back to canonical link identity (documented)
        self.assertEqual(b.id_kind, "link")
        self.assertEqual(b.item_id,
                         "https://www.youtube.com/watch?v=BBB222")

    def test_15_entries_processed_completely(self):
        self._route(yt15())
        res = Y.YouTubeFeedAdapter().extract(CID, 15)
        self.assertEqual(res.meta["returned"], 15)
        self.assertFalse(res.meta["partial"])
        ids = [i.item_id for i in res.items]
        self.assertEqual(len(set(ids)), 15)
        store = MemoryStateStore()
        r = monitor_collect(self._src(), store=store, now=NOW)
        self.assertEqual(len(r.new_items()), 15)

    def test_malformed(self):
        self._route("<feed><broken")
        res = Y.YouTubeFeedAdapter().extract(CID, 3)
        self.assertEqual(res.items, [])
        self.assertEqual(res.meta["reason"], "malformed-xml")

    def test_oversized(self):
        FakeOpener.routes[FEED_URL] = (
            "ok", "x" * (R.MAX_BYTES + 10), "text/xml")
        res = Y.YouTubeFeedAdapter().extract(CID, 3)
        self.assertEqual(res.meta["reason"], "oversized")

    def test_timeout(self):
        class Boom:
            def open(self, req, timeout=None):
                raise TimeoutError("slow")
        old = R._opener
        R._opener = Boom
        try:
            res = Y.YouTubeFeedAdapter().extract(CID, 3)
            self.assertEqual(res.meta["reason"], "timeout")
        finally:
            R._opener = old

    def test_no_oauth_no_key(self):
        import inspect
        # Code constructs only: docstrings document the absence
        # ("no OAuth") and must not trip the guard.
        code = []
        for src in (inspect.getsource(Y), inspect.getsource(R.fetch_feed),
                    inspect.getsource(R._http_get)):
            lines = []
            in_doc = False
            for ln in src.splitlines():
                s = ln.strip()
                if s.startswith('"""'):
                    if s.count('"""') == 2:
                        continue
                    in_doc = not in_doc
                    continue
                if in_doc or s.startswith("#"):
                    continue
                lines.append(ln)
            code.append("\n".join(lines))
        blob = "\n".join(code)
        for bad in ("OAuth", "oauth", "api_key", "apikey", "API_KEY",
                    "Authorization", "Bearer", "client_secret"):
            self.assertNotIn(bad, blob)


if __name__ == "__main__":
    unittest.main()
