"""Current-stage durable retry (Level 2): shared run_stage.

Proves, with deterministic fakes (no network, no sleeps):
1. provider attempts exhaust -> stage stays current, waits,
   retries the SAME stage (same closure inputs).
2. stage success unlocks (returns value; caller proceeds).
3. terminal failure raises immediately with zero waits.
4. exhaustion raises the last error (callers map it exactly
   as before: cycle envelope, slot terminal states).
5. backoff grows exponentially and is capped.
6. envelope shapes for the future stage contract.
7. STAGES vocabulary is exact (research/analysis/package;
   execute is deliberately not stage-retried: side effects).
8. unknown stage / bad attempts fail closed before any call.
9. full cycle integration: research rescue inside ONE run_cycle
   call completes the chain with exactly one package/intent
   (no restart, no duplicates).
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from g2.generators import OpenAITextGenerator
from llm.adapters import LLMError
from stages import (RETRYABLE_STAGE_FAILURE, STAGES, SUCCESS,
                    TERMINAL_FAILURE, classify_stage_error, run_stage,
                    stage_fail, stage_ok)


def _raise(exc):
    raise exc


def _nosleep():
    calls = []

    def sleep(s):
        calls.append(s)

    sleep.calls = calls
    return sleep


class ClassifyStageCase(unittest.TestCase):
    def test_llm_retryable_codes(self):
        for code in ("empty-content", "malformed-response",
                     "invalid-output", "http-429", "http-500",
                     "http-503: x", "transport-URLError"):
            self.assertEqual(classify_stage_error(LLMError(code)),
                             "retryable", code)

    def test_llm_terminal_codes(self):
        for code in ("http-401: bad", "http-403", "http-400",
                     "refusal:max_tokens", "weird-9"):
            self.assertEqual(classify_stage_error(LLMError(code)),
                             "terminal", code)

    def test_domain_wrappers(self):
        self.assertEqual(
            classify_stage_error(Exception("model-error: invalid-output")),
            "retryable")
        self.assertEqual(
            classify_stage_error(Exception("model-error: http-429")),
            "retryable")
        self.assertEqual(
            classify_stage_error(Exception("model-error: http-401: bad")),
            "terminal")
        self.assertEqual(
            classify_stage_error(Exception("model-timeout")), "retryable")
        self.assertEqual(
            classify_stage_error(Exception("boom")), "terminal")
        self.assertEqual(
            classify_stage_error(ValueError("empty-input")), "terminal")

    def test_transport_errors(self):
        import socket
        import urllib.error
        for e in (TimeoutError(), socket.timeout(),
                  ConnectionError("reset"),
                  urllib.error.URLError("dns")):
            self.assertEqual(classify_stage_error(e), "retryable")


class RunStageCase(unittest.TestCase):
    def test_rescue_returns_value_with_same_inputs(self):
        seen = []

        def fn(a, b):
            seen.append((a, b))
            if len(seen) < 3:
                raise LLMError("empty-content")
            return "ok-value"

        sleep = _nosleep()
        out = run_stage("research", lambda: fn(1, 2), sleep=sleep,
                        jitter=False)
        self.assertEqual(out, "ok-value")
        # SAME closure inputs on every attempt: one logical task.
        self.assertEqual(seen, [(1, 2)] * 3)
        self.assertEqual(len(sleep.calls), 2)

    def test_terminal_fails_fast_without_waiting(self):
        sleep = _nosleep()
        with self.assertRaises(ValueError):
            run_stage("analysis", lambda: _raise(ValueError("bad-config")),
                      sleep=sleep)
        self.assertEqual(sleep.calls, [])

    def test_exhaustion_raises_last_error(self):
        sleep = _nosleep()
        with self.assertRaises(LLMError) as ctx:
            run_stage("research",
                      lambda: _raise(LLMError("http-503")),
                      attempts=3, sleep=sleep, jitter=False)
        self.assertIn("http-503", str(ctx.exception))
        self.assertEqual(len(sleep.calls), 2)

    def test_backoff_grows_and_caps(self):
        sleep = _nosleep()
        try:
            run_stage("analysis", lambda: _raise(TimeoutError()),
                      attempts=4, base_delay=45.0,
                      max_delay=100.0, sleep=sleep, jitter=False)
        except TimeoutError:
            pass
        self.assertEqual(sleep.calls, [45.0, 90.0, 100.0])

    def test_stats_records_stage_attempts(self):
        sleep = _nosleep()
        stats: dict = {}
        run_stage("research", lambda: "v", sleep=sleep, stats=stats)
        self.assertEqual(stats, {"stage": "research", "attempts": 1,
                                 "retried": []})
        stats2: dict = {}
        calls = []

        def failing():
            calls.append(1)
            raise LLMError("empty-content")

        try:
            run_stage("analysis", failing, attempts=2, sleep=sleep,
                      jitter=False, stats=stats2)
        except LLMError:
            pass
        self.assertEqual(stats2["attempts"], 2)
        self.assertEqual(stats2["stage"], "analysis")
        self.assertEqual(len(stats2["retried"]), 1)

    def test_stage_retry_emits_trace_line_without_secrets(self):
        import io
        from contextlib import redirect_stderr
        sleep = _nosleep()
        buf = io.StringIO()
        with redirect_stderr(buf):
            try:
                run_stage("research",
                          lambda: _raise(LLMError("http-429")),
                          attempts=2, sleep=sleep, jitter=False)
            except LLMError:
                pass
        trace = buf.getvalue()
        self.assertIn("stage=research", trace)
        self.assertIn("retrying same stage", trace)
        self.assertNotIn("sk-", trace)

    def test_unknown_stage_and_bad_attempts_rejected(self):
        with self.assertRaises(ValueError):
            run_stage("publish", lambda: 1, sleep=_nosleep())
        with self.assertRaises(ValueError):
            run_stage("research", lambda: 1, attempts=0,
                      sleep=_nosleep())

    def test_envelope_shapes(self):
        ok = stage_ok("research", {"a": 1})
        self.assertEqual(ok, {"stage": "research", "status": SUCCESS,
                              "payload": {"a": 1}, "error": ""})
        retry = stage_fail("analysis", LLMError("empty-content"),
                           retryable=True)
        self.assertEqual(retry["status"], RETRYABLE_STAGE_FAILURE)
        self.assertEqual(retry["payload"], None)
        term = stage_fail("analysis", ValueError("bad"), retryable=False)
        self.assertEqual(term["status"], TERMINAL_FAILURE)

    def test_stage_vocabulary_exact(self):
        # research/analysis/package are the retried pre-execution
        # stages; execute is deliberately absent (side effects stay
        # under the existing idempotent execution paths).
        self.assertEqual(STAGES, ("research", "analysis", "package"))
        self.assertNotIn("execute", STAGES)


class CycleStageIntegrationCase(unittest.TestCase):
    """run_stage inside the REAL cycle: a rescued research stage
    completes the chain in ONE run_cycle call (no restart, single
    package/intent, downstream unlocked by SUCCESS only)."""

    def test_rescued_research_completes_chain_without_restart(self):
        import urllib.error as _urlerr

        import g1.fetch as F
        import g1.rss as R
        import socket as _socket
        from research import cycle as C
        from research.analysis import OpenAIAnalysisModel
        from research.models import OpenAIResearchModel, _trace_id
        from research.monitor import MemoryStateStore

        class _Resp:
            def __init__(self, body):
                self._body = body.encode("utf-8")
                self.headers = {"Content-Type": "application/rss+xml"}

            def read(self, n=-1):
                return self._body if n is None or n < 0 else self._body[:n]

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        rss = """<?xml version="1.0"?><rss version="2.0"><channel>
<title>t</title><link>http://feed.test/</link>
<item><title>Stage item</title><link>http://feed.test/only</link>
<guid>g-only</guid><pubDate>Mon, 08 Sep 2026 09:00:00 +0000</pubDate>
<description>Stage item about the harbor.</description></item>
</channel></rss>"""

        class _Opener:
            routes = {"http://feed.test/rss": ("ok", rss,
                                               "application/rss+xml")}

            def open(self, req, timeout=None):
                route = self.routes[req.full_url]
                if route[0] == "ok":
                    return _Resp(route[1])
                raise _urlerr.HTTPError(req.full_url, 404, "e", {},
                                        None)

        policy = {"brand": {"tone": "direct", "audience": "x",
                            "banned_words": [], "preferred_words": [],
                            "cta_style": "Go"},
                  "language": {"default": "en"},
                  "pillars": [], "generation": {}, "media": {}}
        now = "2026-09-08T12:00:00+00:00"
        real_ro, real_fo = R._opener, F._opener
        real_dns = _socket.getaddrinfo
        R._opener = _Opener
        F._opener = _Opener
        _socket.getaddrinfo = lambda h, p, *a, **k: [
            (2, 1, 6, "", ("93.184.216.34", 0))]
        try:
            with mock.patch("time.sleep"):
                store = MemoryStateStore()
                items, _ = C._collect_rss(
                    {"url": "http://feed.test/rss"}, store,
                    now, 50)
                tid = _trace_id(items[0])

                class ScriptAdapter:
                    def __init__(self, outcomes):
                        self._outcomes = list(outcomes)
                        self.calls = []

                    def complete(self, system, user, *, temperature=0.7,
                                 max_tokens=400, timeout=60):
                        self.calls.append((system, user))
                        outcome = self._outcomes.pop(0)
                        if isinstance(outcome, BaseException):
                            raise outcome
                        return outcome

                research_calls: list = []
                orig_research = OpenAIResearchModel.research

                def spy_research(self, items_arg, policy=None):
                    research_calls.append(1)
                    return orig_research(self, items_arg, policy)

                import json as _json

                def rjson():
                    return _json.dumps({
                        "summary": "s",
                        "findings": [{"statement": "stmt",
                                      "kind": "fact",
                                      "confidence": "high",
                                      "item_ids": [tid],
                                      "excerpt": "stmt"}],
                        "topics": ["t"], "entities": []})

                def ajson():
                    return _json.dumps({
                        "eligible": True, "topic": "T", "angle": "A",
                        "rationale": "R", "facts": ["stmt"],
                        "source_item_ids": [tid],
                        "content_format": "post",
                        "media_intent": "none", "audience": "a",
                        "language": "en", "priority": "normal",
                        "confidence": "high", "constraints": []})

                radapter = ScriptAdapter(
                    [LLMError("empty-content"), rjson()])
                aadapter = ScriptAdapter([ajson()])
                tadapter = ScriptAdapter(["Body here"])
                with mock.patch.object(OpenAIResearchModel, "research",
                                       spy_research):
                    sched = {"timezone": "UTC", "times": ["09:00"]}
                    out = C.run_cycle(
                        [{"type": "rss",
                          "url": "http://feed.test/rss"}],
                        store=MemoryStateStore(), policy=dict(policy),
                        schedule=dict(sched), now=now,
                        integration_id="int-1",
                        research_model=OpenAIResearchModel(
                            adapter=radapter),
                        analysis_model=OpenAIAnalysisModel(
                            adapter=aadapter),
                        text_gen=OpenAITextGenerator(
                            api_key="k", adapter=tadapter))
                self.assertEqual(out.error, "")
                self.assertTrue(out.opportunity.eligible)
                self.assertEqual(len(out.packages), 1)
                self.assertEqual(len(out.intents), 1)
                # Two-tier proof: ONE stage invocation containing
                # TWO provider attempts (Level 2 x Level 1), then
                # single calls downstream. No restart happened:
                # this all occurred inside a single run_cycle call.
                self.assertEqual(len(research_calls), 1)
                self.assertEqual(len(radapter.calls), 2)
                self.assertEqual(len(aadapter.calls), 1)
                self.assertEqual(len(tadapter.calls), 1)
        finally:
            R._opener = real_ro
            F._opener = real_fo
            _socket.getaddrinfo = real_dns


if __name__ == "__main__":
    unittest.main()
