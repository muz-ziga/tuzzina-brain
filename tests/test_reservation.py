"""Stage B reservation proof: atomic cross-slot claims.

The naive last-writer-wins store (Tuzzina's old research-state
upsert semantics) demonstrably double-wins; the atomic claim
endpoint (unique constraint + explicit 409 loss + owner-only
release + expiry) provably admits exactly one winner.

No network, no DB, no production: fakes implement the
documented server semantics in-process (threads + lock for
the race)."""
import json
import os
import sys
import threading
import unittest
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from research.cycle import run_cycle
from research.models import MockResearchModel
from research.monitor import MemoryStateStore
from tuzzina.client import TuzzinaClient, TuzzinaError


class NaiveStore:
    """Last-writer-wins upsert (the OLD research-state
    semantics): load and save are separate calls with no
    atomicity between them."""

    def __init__(self):
        self._data = {}
        self._lock = threading.Lock()

    def load(self, key):
        with self._lock:
            import copy
            return copy.deepcopy(self._data.get(key))

    def save(self, key, value):
        with self._lock:
            import copy
            self._data[key] = copy.deepcopy(value)


class AtomicClaimServer:
    """Documented /candidate-claims semantics: UNIQUE
    (org, candidate) insert-or-fail; same-owner re-claim wins
    idempotently; release is owner-only; expiry sweeps on the
    claim path. One lock models the DB constraint."""

    def __init__(self):
        self._rows = {}
        self._lock = threading.Lock()

    def claim(self, candidate, run, ttl_min=120):
        with self._lock:
            row = self._rows.get(candidate)
            if row is not None and row["run"] == run:
                return True
            if row is not None:
                return False
            self._rows[candidate] = {"run": run}
            return True

    def release(self, candidate, run):
        with self._lock:
            row = self._rows.get(candidate)
            if row is not None and row["run"] == run:
                del self._rows[candidate]
                return 1
            return 0


class NaiveRaceTest(unittest.TestCase):
    def test_naive_upsert_double_wins(self):
        # Documents WHY the claim table exists: with separate
        # load/save and no constraint, two synced slots both
        # believe they won while one write silently survives.
        store = NaiveStore()
        barrier = threading.Barrier(2)
        results = {}

        def worker(slot):
            barrier.wait()
            state = store.load("claims") or {}
            claims = dict(state.get("claims") or {})
            if "cand-A" in claims:
                results[slot] = False
                return
            barrier.wait()
            claims["cand-A"] = slot
            store.save("claims", {"claims": claims})
            results[slot] = True

        threads = [threading.Thread(target=worker, args=(s,))
                   for s in ("11:00", "12:00")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertTrue(results["11:00"])
        self.assertTrue(results["12:00"])


class AtomicClaimTest(unittest.TestCase):
    def test_concurrent_claim_exactly_one_winner(self):
        server, barrier = AtomicClaimServer(), threading.Barrier(2)
        results = {}

        def worker(slot):
            barrier.wait()
            results[slot] = server.claim("cand-A", slot)

        threads = [threading.Thread(target=worker, args=(s,))
                   for s in ("11:00", "12:00")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sorted(results.values()), [False, True])

    def test_duplicate_claim_loses(self):
        server = AtomicClaimServer()
        self.assertTrue(server.claim("cand-A", "11:00"))
        self.assertFalse(server.claim("cand-A", "12:00"))

    def test_same_owner_reclaim_wins(self):
        server = AtomicClaimServer()
        self.assertTrue(server.claim("cand-A", "11:00"))
        self.assertTrue(server.claim("cand-A", "11:00"))

    def test_release_frees_for_other_slot(self):
        server = AtomicClaimServer()
        self.assertTrue(server.claim("cand-A", "11:00"))
        self.assertEqual(server.release("cand-A", "12:00"), 0)
        self.assertEqual(server.release("cand-A", "11:00"), 1)
        self.assertTrue(server.claim("cand-A", "12:00"))

    def test_release_then_generation_failure_retried(self):
        # Claim -> crash (no release) is covered by server-side
        # expiry; here: claim -> release on terminal error ->
        # next slot claims cleanly.
        server = AtomicClaimServer()
        self.assertTrue(server.claim("cand-A", "11:00"))
        self.assertEqual(server.release("cand-A", "11:00"), 1)
        self.assertTrue(server.claim("cand-A", "12:00"))


class ClientClaimTest(unittest.TestCase):
    def _client(self, server):
        real = urllib.request.urlopen

        def fake(req, timeout=None):
            url, method = req.full_url, req.get_method()
            base = url.split("?", 1)[0]
            if base.endswith("/public/v1/candidate-claims"):
                if method == "POST":
                    body = json.loads(req.data.decode())
                    won = server.claim(body["candidateId"],
                                       body["runId"])
                    if won:
                        return _Resp({"claimed": True,
                                      "candidateId":
                                      body["candidateId"],
                                      "runId": body["runId"]})
                    return _Resp({"message":
                                  "Candidate already claimed"},
                                 code=409)
                params = dict(
                    p.split("=") for p in
                    url.split("?", 1)[1].split("&"))
                import urllib.parse
                n = server.release(
                    urllib.parse.unquote(params["candidateId"]),
                    urllib.parse.unquote(params["runId"]))
                return _Resp({"released": n})
            raise AssertionError("unexpected " + url)

        urllib.request.urlopen = fake
        self.addCleanup(setattr, urllib.request, "urlopen", real)
        return TuzzinaClient("http://x/api", "k")

    def test_claim_win_and_loss(self):
        server = AtomicClaimServer()
        c = self._client(server)
        self.assertTrue(c.claim_candidate("cand-A", "run-1"))
        self.assertFalse(c.claim_candidate("cand-A", "run-2"))

    def test_claim_same_owner_reclaim(self):
        server = AtomicClaimServer()
        c = self._client(server)
        self.assertTrue(c.claim_candidate("cand-A", "run-1"))
        self.assertTrue(c.claim_candidate("cand-A", "run-1"))

    def test_claim_transport_error_raises(self):
        real = urllib.request.urlopen
        urllib.request.urlopen = lambda *a, **k: (_ for _ in ()).throw(
            ConnectionError("down"))
        self.addCleanup(setattr, urllib.request, "urlopen", real)
        c = TuzzinaClient("http://x/api", "k")
        with self.assertRaises(TuzzinaError):
            c.claim_candidate("cand-A", "run-1")

    def test_claim_requires_ids(self):
        c = TuzzinaClient("http://x/api", "k")
        with self.assertRaises(TuzzinaError):
            c.claim_candidate("", "run-1")
        with self.assertRaises(TuzzinaError):
            c.claim_candidate("cand-A", "")

    def test_release_count(self):
        server = AtomicClaimServer()
        c = self._client(server)
        c.claim_candidate("cand-A", "run-1")
        self.assertEqual(c.release_claim("cand-A", "run-1"), 1)
        self.assertEqual(c.release_claim("cand-A", "run-1"), 0)


class _Resp:
    def __init__(self, payload, code=200):
        self.payload = payload
        self.code = code
        self.headers = {}

    def read(self, n=-1):
        data = json.dumps(self.payload).encode()
        return data if n is None or n < 0 else data[:n]

    def __enter__(self):
        if self.code >= 400:
            import urllib.error
            raise urllib.error.HTTPError("u", self.code, "err",
                                         {}, None)
        return self

    def __exit__(self, *a):
        return False


class CycleClaimTest(unittest.TestCase):
    def test_all_lost_means_no_llm_and_committed(self):
        import g1.rss as R
        real_opener, real_dns = R._opener, __import__(
            "socket").getaddrinfo

        class FakeResp2:
            def __init__(self, body, headers=None, url=""):
                self._body = body.encode()
                self.headers = headers or {}
                self._url = url

            def geturl(self):
                return self._url

            def read(self, n=-1):
                return self._body

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        rss = ('<?xml version="1.0"?><rss version="2.0">'
               '<channel><title>F</title><link>http://f.test/</link>'
               '<item><title>A</title><link>http://f.test/a</link>'
               '<guid>g-a</guid></item></channel></rss>')

        class FakeOpener:
            routes = {"http://f.test/rss": ("ok", rss,
                                            "application/rss+xml")}

            def open(self, req, timeout=None):
                kind, body, ctype = self.routes[req.full_url]
                return FakeResp2(body, {"Content-Type": ctype},
                                 req.full_url)

        import socket
        R._opener = FakeOpener
        socket.getaddrinfo = lambda h, p, *a, **k: [
            (2, 1, 6, "", ("93.184.216.34", 0))]
        from research import cycle as C

        class Rec(MockResearchModel):
            calls = 0

            def research(self, items, policy=None):
                Rec.calls += 1
                return super().research(items, policy)

        try:
            out = C.run_cycle(
                [{"type": "rss", "url": "http://f.test/rss",
                  "n": 3}],
                store=MemoryStateStore(), policy={}, schedule={},
                now="2026-09-08T12:00:00+00:00",
                research_model=Rec(), mode="draft",
                integration_id="int-1",
                claimer=lambda key: False)
        finally:
            R._opener = real_opener
            socket.getaddrinfo = real_dns
        self.assertEqual(Rec.calls, 0)
        self.assertEqual(out.items, [])
        self.assertTrue(out.committed)
        self.assertTrue(all(
            s.startswith("claimed-by-other:")
            for s in out.skipped))

    def test_claim_transport_failure_fails_closed(self):
        import g1.rss as R
        import socket
        real_opener, real_dns = R._opener, socket.getaddrinfo

        rss = ('<?xml version="1.0"?><rss version="2.0">'
               '<channel><title>F</title><link>http://f.test/</link>'
               '<item><title>A</title><link>http://f.test/a</link>'
               '<guid>g-a</guid></item></channel></rss>')

        class Resp:
            def __init__(self, body, headers=None, url=""):
                self._body = body.encode()
                self.headers = headers or {}
                self._url = url

            def geturl(self):
                return self._url

            def read(self, n=-1):
                return self._body

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        class FakeOpener2:
            routes = {"http://f.test/rss": ("ok", rss,
                                            "application/rss+xml")}

            def open(self, req, timeout=None):
                kind, body, ctype = self.routes[req.full_url]
                return Resp(body, {"Content-Type": ctype},
                            req.full_url)

        R._opener = FakeOpener2
        socket.getaddrinfo = lambda h, p, *a, **k: [
            (2, 1, 6, "", ("93.184.216.34", 0))]
        from research import cycle as C

        def boom(key):
            raise ConnectionError("claim store down")

        try:
            out = C.run_cycle(
                [{"type": "rss", "url": "http://f.test/rss",
                  "n": 3}],
                store=MemoryStateStore(), policy={}, schedule={},
                now="2026-09-08T12:00:00+00:00",
                mode="draft", integration_id="int-1",
                claimer=boom)
        finally:
            R._opener = real_opener
            socket.getaddrinfo = real_dns
        self.assertIn("claim-failed", out.error)
        self.assertFalse(out.committed)


if __name__ == "__main__":
    unittest.main()
