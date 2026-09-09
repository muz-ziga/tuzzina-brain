"""Phase 6 tests: durable monitoring state. TuzzinaStateStore
speaks through the real TuzzinaClient with faked transport (no
network, no keys): proves paths, methods, 404-fresh mapping,
serialization, and secrecy. Plus website classify parity."""
import json
import os
import sys
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from research.monitor import (MemoryStateStore, NEW, UNCHANGED,
                              UPDATED, SourceState, classify_candidates,
                              collect)
from tuzzina.client import TuzzinaClient, TuzzinaError
from tuzzina.state_store import (TuzzinaStateStore, state_from_dict,
                                 state_to_dict)

CALLS = []
ROWS = {}


class FakeResp:
    def __init__(self, payload, code=200):
        self.payload = payload
        self.code = code

    def read(self):
        return json.dumps(self.payload).encode()

    def __enter__(self):
        if self.code >= 400:
            raise urllib.error.HTTPError("u", self.code, "err", {}, None)
        return self

    def __exit__(self, *a):
        return False


def fake_urlopen(req, timeout=None):
    CALLS.append((req.full_url, req.get_method(),
                  json.loads(req.data.decode()) if req.data else None))
    if req.get_method() == "GET":
        if req.full_url in ROWS:
            return FakeResp({"sourceId": "x", "state": ROWS[req.full_url],
                             "updatedAt": "2026-09-08T12:00:00+00:00"})
        return FakeResp({}, 404)
    if req.get_method() == "PUT":
        body = json.loads(req.data.decode())
        ROWS[req.full_url] = body["state"]
        return FakeResp({"sourceId": "x",
                         "updatedAt": "2026-09-08T12:00:00+00:00"})
    return FakeResp({}, 400)


NOW = "2026-09-08T12:00:00+00:00"


class ContractCase(unittest.TestCase):
    def test_roundtrip(self):
        st = SourceState(
            source_id="s", last_seen_item_id="g-9",
            last_seen_published_at="2026-09-08T11:00:00+00:00",
            seen={"g-9": {"hash": "h", "first_seen": NOW}},
            first_seen_at=NOW, last_checked_at=NOW, last_error="")
        back = state_from_dict("s", state_to_dict(st))
        self.assertEqual(back, st)

    def test_defensive_rebuild(self):
        self.assertEqual(state_from_dict("s", None),
                         SourceState(source_id="s"))
        self.assertEqual(state_from_dict("s", "junk"),
                         SourceState(source_id="s"))
        back = state_from_dict("s", {"seen": [1, 2],
                                     "mystery": "drop",
                                     "last_error": 7})
        self.assertEqual(back.seen, {})
        self.assertEqual(back.last_error, "7")
        self.assertFalse(hasattr(back, "mystery"))

    def test_dict_keys_stable(self):
        self.assertEqual(sorted(state_to_dict(SourceState()).keys()),
                         ["first_seen_at", "last_checked_at",
                          "last_error", "last_seen_item_id",
                          "last_seen_published_at", "seen",
                          "source_id"])


class StoreCase(unittest.TestCase):
    def setUp(self):
        CALLS.clear()
        ROWS.clear()
        self._orig = urllib.request.urlopen
        urllib.request.urlopen = fake_urlopen
        self.c = TuzzinaClient("http://x/api", "k")
        self.store = TuzzinaStateStore(self.c)

    def tearDown(self):
        urllib.request.urlopen = self._orig

    def test_load_hit(self):
        ROWS["http://x/api/public/v1/research-state/feed.test"] = {
            "source_id": "feed.test", "seen": {
                "g-1": {"hash": "h1", "first_seen": NOW}},
            "last_seen_item_id": "g-1",
            "last_seen_published_at": "2026-09-08T09:00:00+00:00",
            "first_seen_at": NOW, "last_checked_at": NOW,
            "last_error": ""}
        st = self.store.load("feed.test")
        self.assertEqual(st.seen["g-1"]["hash"], "h1")
        self.assertEqual(st.last_seen_item_id, "g-1")
        self.assertEqual(CALLS[0][1], "GET")

    def test_load_miss_is_fresh(self):
        st = self.store.load("never-seen")
        self.assertEqual(st, SourceState(source_id="never-seen"))
        self.assertEqual(CALLS[0][1], "GET")

    def test_load_server_error_raises(self):
        urllib.request.urlopen = lambda *a, **k: FakeResp({}, 500)
        with self.assertRaises(TuzzinaError):
            self.store.load("feed.test")

    def test_save_roundtrip(self):
        st = SourceState(source_id="feed.test", last_error="")
        st.seen["g-1"] = {"hash": "h1", "first_seen": NOW}
        self.store.save(st)
        self.assertEqual(CALLS[0][1], "PUT")
        self.assertEqual(CALLS[0][0],
                         "http://x/api/public/v1/research-state/feed.test")
        body = CALLS[0][2]
        self.assertEqual(set(body.keys()), {"state"})
        self.assertEqual(body["state"]["seen"]["g-1"]["hash"], "h1")
        # durable: a NEW store instance observes the commit
        other = TuzzinaStateStore(self.c)
        self.assertEqual(other.load("feed.test").seen["g-1"]["hash"],
                         "h1")

    def test_save_requires_source_id(self):
        with self.assertRaises(TuzzinaError):
            self.store.save(SourceState())

    def test_save_failure_raises(self):
        urllib.request.urlopen = lambda *a, **k: FakeResp({}, 500)
        with self.assertRaises(TuzzinaError):
            self.store.save(SourceState(source_id="s"))

    def test_source_id_encoded(self):
        st = SourceState(source_id="http://feed.test/rss?a=b c/d")
        self.store.save(st)
        url = CALLS[0][0]
        self.assertTrue(url.startswith(
            "http://x/api/public/v1/research-state/http%3A"))
        self.assertNotIn(" ", url)
        self.assertNotIn("/rss", url.split("/research-state/")[1])

    def test_no_secrets_in_payloads(self):
        st = SourceState(source_id="s")
        st.seen["g-1"] = {"hash": "h", "first_seen": NOW}
        self.store.save(st)
        blob = json.dumps([CALLS, ROWS])
        for bad in ("Authorization", "Bearer", "api_key", "secret",
                    "password", "token", "cookie"):
            self.assertNotIn(bad, blob)

    def test_needs_client(self):
        with self.assertRaises(TuzzinaError):
            TuzzinaStateStore(None)


class ClassifyCase(unittest.TestCase):
    def test_shared_rules(self):
        from research.monitor import SourceState
        state = SourceState(source_id="w")
        cands = [{"carrier": "A", "item_id": "u-a",
                  "content_hash": "h1", "published_at": ""}]
        out, staged = classify_candidates("w", cands, state, NOW)
        self.assertEqual(out, [("A", NEW)])
        self.assertIn("u-a", staged.seen)
        # collect() on feeds uses the same helper (parity spot-check
        # lives in the rss suite; here: unidentifiable skipped)
        out2, staged2 = classify_candidates(
            "w", [{"carrier": "B", "item_id": "",
                   "content_hash": "h", "published_at": ""}],
            staged, NOW)
        self.assertEqual(out2, [])
        self.assertNotIn("", staged2.seen)

    def test_updated_then_stable(self):
        state = SourceState(source_id="w")
        mk = lambda h: [{"carrier": "A", "item_id": "u-a",
                         "content_hash": h, "published_at": ""}]
        out1, s1 = classify_candidates("w", mk("h1"), state, NOW)
        self.assertEqual([k for _, k in out1], [NEW])
        out2, s2 = classify_candidates("w", mk("h2"), s1, NOW)
        self.assertEqual([k for _, k in out2], [UPDATED])
        out3, _ = classify_candidates("w", mk("h2"), s2, NOW)
        self.assertEqual([k for _, k in out3], [UNCHANGED])


class IsolationCase(unittest.TestCase):
    def setUp(self):
        CALLS.clear()
        ROWS.clear()
        self._orig = urllib.request.urlopen
        urllib.request.urlopen = fake_urlopen
        self.c = TuzzinaClient("http://x/api", "k")

    def tearDown(self):
        urllib.request.urlopen = self._orig

    def test_source_ids_do_not_collide(self):
        a = TuzzinaStateStore(self.c)
        a.save(SourceState(source_id="org-a-feed",
                           seen={"g-1": {"hash": "h", "first_seen": NOW}}))
        b = TuzzinaStateStore(self.c)
        self.assertEqual(b.load("org-b-feed"), SourceState(
            source_id="org-b-feed"))
        # server-side org scoping is by API key; distinct source ids
        # never share rows (URLs prove separation: 1 PUT + 1 GET)
        urls = {u for u, _, _ in CALLS}
        self.assertEqual(len(urls), 2)


if __name__ == "__main__":
    unittest.main()
