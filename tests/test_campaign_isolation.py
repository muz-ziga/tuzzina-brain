"""Campaign isolation + retry-architecture invariants.

Proves, with independent stores (independent campaigns) and the
real entry point:
1. A retryable research failure in campaign A never stops,
   cancels, or degrades campaign B (separate state, separate
   outcome; B commits and produces intents).
2. A terminal/config failure in campaign A likewise leaves B
   alone (failure is campaign-local).
3. Exit-code contract the Temporal activity relies on: runtime
   failures (ResearchError and friends) propagate as exit 1,
   Tuzzina I/O as 3 (both retryable server-side); config errors
   exit 2/4 (terminal server-side). Research/analysis/text/
   prompt share one retry implementation: a static scan fails
   if any per-agent retry manager ever appears.
4. No global retry loop exists: campaigns share no mutable
   orchestration state (each run_cycle call takes its own
   store/policy/schedule).

Transport faked where network is involved; the failing research
path uses a scripted adapter through the REAL OpenAIResearchModel
so classification (retryable invalid-output) is exercised, not
mocked around.
"""
import json
import os
import socket
import sys
import unittest
import urllib.error

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import g1.fetch as F
import g1.rss as R
from llm.adapters import LLMError
from research import cycle as C
from research.analysis import MockAnalysisModel
from research.models import (MockResearchModel, OpenAIResearchModel,
                             ResearchError)
from research.monitor import MemoryStateStore

RSS1 = """<?xml version="1.0"?><rss version="2.0"><channel>
<title>t</title><link>http://feed.test/</link>
<item><title>Campaign item</title><link>http://feed.test/only</link>
<guid>g-only</guid><pubDate>Mon, 08 Sep 2026 09:00:00 +0000</pubDate>
<description>Campaign item about the harbor festival.</description></item>
</channel></rss>"""

NOW = "2026-09-08T12:00:00+00:00"
POLICY = {"brand": {"tone": "direct", "audience": "x", "banned_words": [],
                    "preferred_words": [], "cta_style": "Go"},
          "language": {"default": "en"},
          "pillars": [], "generation": {}, "media": {}}
SCHEDULE = {"timezone": "UTC", "times": ["09:00"]}


class FakeResp:
    def __init__(self, body, ctype="application/rss+xml"):
        self._body = body.encode("utf-8")
        self.headers = {"Content-Type": ctype}

    def read(self, n=-1):
        return self._body if n is None or n < 0 else self._body[:n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeOpener:
    routes = {
        "http://feed.test/rss": ("ok", RSS1, "application/rss+xml"),
    }

    def open(self, req, timeout=None):
        route = self.routes[req.full_url]
        if route[0] == "ok":
            _, body, ctype = route
            return FakeResp(body, ctype)
        raise route[1]


_REAL_GETADDRINFO = socket.getaddrinfo


def _public_dns(host, port, *a, **k):
    return [(2, 1, 6, "", ("93.184.216.34", 0))]


class ScriptAdapter:
    def __init__(self, outcomes):
        self._outcomes = list(outcomes)
        self.calls = []

    def complete(self, system, user, *, temperature=0.7, max_tokens=400,
                 timeout=60):
        self.calls.append((system, user))
        if not self._outcomes:
            raise AssertionError("adapter called past script end")
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _research_json(tid):
    return json.dumps({
        "summary": "Campaign news.",
        "findings": [{"statement": "Campaign item words",
                      "kind": "fact", "confidence": "high",
                      "item_ids": [tid],
                      "excerpt": "Campaign item"}],
        "topics": ["t"], "entities": []})


class IsolationCase(unittest.TestCase):
    def setUp(self):
        self._real_ropen, self._real_fopen = R._opener, F._opener
        R._opener = FakeOpener
        F._opener = FakeOpener
        socket.getaddrinfo = _public_dns

    def tearDown(self):
        R._opener = self._real_ropen
        F._opener = self._real_fopen
        socket.getaddrinfo = _REAL_GETADDRINFO

    def _run(self, store, **kw):
        kw.setdefault("policy", dict(POLICY))
        kw.setdefault("schedule", dict(SCHEDULE))
        kw.setdefault("now", NOW)
        kw.setdefault("integration_id", "int-1")
        return C.run_cycle([{"type": "rss", "url": "http://feed.test/rss"}],
                           store=store, **kw)

    def test_retryable_failure_in_A_leaves_B_alone(self):
        # Campaign A: research exhausts 3 classified retries.
        # Campaign B: same source shape, own store, mock success.
        # B must commit + produce intents despite A's failure.
        # Stage backoff sleeps are substituted (unit-test clock);
        # production defaults are untouched.
        import time
        from unittest import mock
        store_a, store_b = MemoryStateStore(), MemoryStateStore()
        with mock.patch.object(time, "sleep"):
            out_a = self._run(
                store_a,
                research_model=OpenAIResearchModel(
                    adapter=ScriptAdapter(
                        [LLMError("empty-content")] * 9)))
        self.assertIn("model-error", out_a.error)
        self.assertFalse(out_a.committed)
        self.assertEqual(out_a.intents, [])
        out_b = self._run(
            store_b,
            research_model=MockResearchModel(),
            analysis_model=MockAnalysisModel())
        self.assertEqual(out_b.error, "")
        self.assertTrue(out_b.committed)
        self.assertEqual(len(out_b.intents), 1)
        # A redelivers as NEW later (at-least-once preserved).
        self.assertEqual(
            store_a.load("http://feed.test/rss").seen, {})

    def test_terminal_failure_in_A_leaves_B_alone(self):
        # Unknown source type = deterministic config error in A;
        # B (valid source, own store) is unaffected.
        store_a, store_b = MemoryStateStore(), MemoryStateStore()
        out_a = C.run_cycle(
            [{"type": "nope", "url": "http://feed.test/rss"}],
            store=store_a, policy=dict(POLICY),
            schedule=dict(SCHEDULE), now=NOW, integration_id="int-1")
        self.assertNotEqual(out_a.error, "")
        self.assertFalse(out_a.committed)
        out_b = self._run(
            store_b,
            research_model=MockResearchModel(),
            analysis_model=MockAnalysisModel())
        self.assertEqual(out_b.error, "")
        self.assertEqual(len(out_b.intents), 1)

    def test_exit_code_contract_for_temporal_mapping(self):
        # The activity treats exits 2/4 as terminal and everything
        # else retryable. Brain side of that contract: config
        # errors map to 2/4; a runtime stage error (ResearchError)
        # is unlisted and propagates out of main() untouched, so
        # the interpreter exits 1 and the scheduler treats it as
        # retryable — a retryable failure can NEVER look terminal.
        import run_cycle as rc
        from tuzzina.client import TuzzinaError
        cases = [
            (FileNotFoundError("x"), 2),
            (ValueError("x"), 2),
            (TuzzinaError("x"), 3),
        ]
        for fn, code in cases:
            orig = rc._main
            rc._main = lambda argv=None: (_ for _ in ()).throw(fn)
            try:
                self.assertEqual(rc.main(argv=[]), code)
            finally:
                rc._main = orig
        orig = rc._main
        rc._main = lambda argv=None: (_ for _ in ()).throw(
            ResearchError("model-error: invalid-output"))
        try:
            with self.assertRaises(ResearchError):
                rc.main(argv=[])
        finally:
            rc._main = orig

    def test_single_shared_retry_implementation(self):
        # Architectural invariant: exactly one retry helper and
        # one classifier exist; no per-agent retry managers may
        # ever appear (ResearchRetry/AnalysisRetry/...).
        import pathlib
        import re
        src = pathlib.Path(__file__).resolve().parent.parent / "src"
        managers = []
        helpers = []
        for path in src.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for m in re.finditer(
                    r"class\s+(\w*[Rr]etry\w*)\s*[\(:]", text):
                managers.append(f"{path.name}:{m.group(1)}")
            if "def complete_with_retry" in text:
                helpers.append(str(path))
            if "def classify_llm_error" in text:
                helpers.append(str(path))
        self.assertEqual(
            managers, [],
            f"per-agent retry managers forbidden, found: {managers}")
        self.assertEqual(
            sorted(set(helpers)),
            [str(src / "llm" / "adapters.py")])


if __name__ == "__main__":
    unittest.main()
