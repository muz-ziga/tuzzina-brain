"""Campaign orchestration audit: strict sequential gating.

Proves at the real stage classes (no mocks for the stage under
test — only scripted transport adapters):
1. research failure => analysis/text/image never STARTED.
2. research rescue => analysis unlocks, full chain completes.
3. non-retryable research failure => single attempt, downstream idle.
4. analysis failure => text/image never STARTED.
5. text failure => no intents, nothing committed.
6. image-prompt failure => error propagates, zero packages, so no
   media resolution (and no Tuzzina contact) can occur.
7. shared reuse: every LLM role rescues a first-attempt
   reasoning-only/empty reply through the same helper.
8. rescued runs produce exactly one package/intent (no dupes).

Transport is faked (scripted adapters); skill files come from the
repo defaults. No network, no keys, no live calls.
"""
import json
import os
import socket
import sys
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from g2.generators import (MockImageGenerator, MockTextGenerator,
                           OpenAIImagePromptGenerator, OpenAITextGenerator)
from llm.adapters import LLMError
from research import cycle as C
from research.analysis import OpenAIAnalysisModel
from research.models import OpenAIResearchModel, _trace_id
from research.monitor import MemoryStateStore


RSS1 = """<?xml version="1.0"?><rss version="2.0"><channel>
<title>t</title><link>http://feed.test/</link>
<item><title>Harbor masterclass announced</title><link>http://feed.test/only</link>
<guid>g-only</guid><pubDate>Mon, 08 Sep 2026 09:00:00 +0000</pubDate>
<description>Harbor masterclass announced today with loudness basics.</description></item>
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
            return FakeResp(body)
        raise route[1]


_REAL_GETADDRINFO = socket.getaddrinfo


def _public_dns(host, port, *a, **k):
    return [(2, 1, 6, "", ("93.184.216.34", 0))]


class ScriptAdapter:
    """Scripted transport: outcomes are return strings or
    exceptions. Records every call. Fails loudly if the code
    under test calls past the script end (catches unexpected
    extra attempts)."""

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
        "summary": "Harbor masterclass news.",
        "findings": [{"statement": "Harbor masterclass announced",
                      "kind": "fact", "confidence": "high",
                      "item_ids": [tid],
                      "excerpt": "Harbor masterclass announced"}],
        "topics": ["t"], "entities": []})


def _analysis_json(tid, media="none"):
    return json.dumps({
        "eligible": True, "topic": "Harbor masterclass",
        "angle": "Loudness basics for newcomers",
        "rationale": "Product relevance: NO - useful authority topic.",
        "facts": ["Harbor masterclass announced"],
        "source_item_ids": [tid], "content_format": "post",
        "media_intent": media, "audience": "a", "language": "en",
        "priority": "normal", "confidence": "high", "constraints": []})


class CountCalls:
    """Wraps a real stage object, counting invocations of one
    method without changing its behavior."""

    def __init__(self, obj, method):
        self._obj = obj
        self.calls = 0
        self._method = method
        fn = getattr(obj, method)

        def counted(*a, **k):
            self.calls += 1
            return fn(*a, **k)

        setattr(obj, method, counted)

    def __getattr__(self, name):
        return getattr(self._obj, name)


class GatingCase(unittest.TestCase):
    def setUp(self):
        import g1.rss as R
        import g1.fetch as F
        self._R, self._F = R, F
        self._real_ropen, self._real_fopen = R._opener, F._opener
        R._opener = FakeOpener
        F._opener = FakeOpener
        socket.getaddrinfo = _public_dns

    def tearDown(self):
        self._R._opener = self._real_ropen
        self._F._opener = self._real_fopen
        socket.getaddrinfo = _REAL_GETADDRINFO

    def _run(self, **kw):
        kw.setdefault("store", MemoryStateStore())
        kw.setdefault("policy", dict(POLICY))
        kw.setdefault("schedule", dict(SCHEDULE))
        kw.setdefault("now", NOW)
        kw.setdefault("integration_id", "int-1")
        return C.run_cycle([{"type": "rss", "url": "http://feed.test/rss"}],
                           **kw)

    def _tid(self):
        # Trace id the collector assigns our single item (computed
        # through the real helper, never hardcoded).
        store = MemoryStateStore()
        items, _ = C._collect_rss({"url": "http://feed.test/rss"},
                                  store, NOW, 50)
        self.assertEqual(len(items), 1)
        return _trace_id(items[0])

    def _research(self, outcomes):
        return OpenAIResearchModel(
            adapter=ScriptAdapter(outcomes))

    def _analysis(self, outcomes):
        return OpenAIAnalysisModel(
            adapter=ScriptAdapter(outcomes))

    def _text(self, outcomes):
        return OpenAITextGenerator(
            api_key="k", adapter=ScriptAdapter(outcomes))

    def _prompt(self, outcomes):
        return OpenAIImagePromptGenerator(
            api_key="k", adapter=ScriptAdapter(outcomes))

    def test_research_failure_never_starts_downstream(self):
        # Proof 1+2: research exhausts BOTH retry layers (provider
        # attempts x stage attempts) and analysis/text/image-prompt
        # are NEVER invoked; nothing to commit, no intents, no
        # execution. Clocks substituted: production backoff
        # untouched.
        from unittest import mock
        analysis = CountCalls(self._analysis(["{}"]), "analyze")
        text = CountCalls(self._text(["{}"]), "generate")
        prompt = CountCalls(self._prompt(["{}"]), "generate_prompt")
        with mock.patch("time.sleep"):
            out = self._run(
                research_model=self._research(
                    [LLMError("empty-content")] * 9),
                analysis_model=analysis,
                text_gen=text,
                policy={**POLICY, "image_prompt_gen": prompt})
        self.assertIn("model-error", out.error)
        self.assertIsNone(out.opportunity)
        self.assertEqual(out.intents, [])
        self.assertEqual(out.packages, [])
        self.assertFalse(out.committed)
        self.assertEqual(analysis.calls, 0)
        self.assertEqual(text.calls, 0)
        self.assertEqual(prompt.calls, 0)

    def test_research_rescue_unlocks_full_chain(self):
        # Proof 3+7: first attempt reasoning-only is absorbed, the
        # SAME task succeeds on attempt 2, and every downstream
        # stage then runs exactly once with exactly one intent.
        tid = self._tid()
        analysis = CountCalls(self._analysis([_analysis_json(tid)]),
                              "analyze")
        text = CountCalls(self._text(["Harbor post body here"]), "generate")
        out = self._run(
            research_model=self._research(
                [LLMError("empty-content"), _research_json(tid)]),
            analysis_model=analysis,
            text_gen=text)
        self.assertEqual(out.error, "")
        self.assertTrue(out.opportunity.eligible)
        self.assertEqual(analysis.calls, 1)
        self.assertEqual(text.calls, 1)
        self.assertEqual(len(out.packages), 1)
        self.assertEqual(len(out.intents), 1)
        self.assertTrue(out.committed)

    def test_nonretryable_research_failure_single_attempt(self):
        # Proof 4: bad credentials fail closed on attempt 1; the
        # workflow stops AT research, downstream idle.
        adapter = ScriptAdapter([LLMError("http-401: bad key"), "unused"])
        analysis = CountCalls(self._analysis(["{}"]), "analyze")
        out = self._run(
            research_model=OpenAIResearchModel(adapter=adapter),
            analysis_model=analysis,
            text_gen=MockTextGenerator())
        self.assertIn("model-error", out.error)
        self.assertEqual(len(adapter.calls), 1)
        self.assertEqual(analysis.calls, 0)
        self.assertEqual(out.intents, [])

    def test_analysis_failure_never_starts_text_or_prompt(self):
        # Proof 5 (analysis half): analysis exhausts BOTH layers;
        # text and image-prompt adapters see zero calls. Clocks
        # substituted; production backoff untouched.
        from unittest import mock
        tid = self._tid()
        text_adapter = ScriptAdapter(["unused"])
        prompt_adapter = ScriptAdapter(["unused"])
        text = OpenAITextGenerator(api_key="k", adapter=text_adapter)
        prompt = OpenAIImagePromptGenerator(api_key="k",
                                           adapter=prompt_adapter)
        with mock.patch("time.sleep"):
            out = self._run(
                research_model=self._research([_research_json(tid)]),
                analysis_model=self._analysis(
                    [LLMError("empty-content")] * 9),
                text_gen=text,
                policy={**POLICY, "image_prompt_gen": prompt})
        self.assertIn("model-error", out.error)
        self.assertEqual(len(text_adapter.calls), 0)
        self.assertEqual(len(prompt_adapter.calls), 0)
        self.assertEqual(out.intents, [])

    def test_text_failure_yields_no_intent(self):
        # Proof 5 (text half): text exhausts BOTH layers inside
        # packaging; no intent exists, so the execution half has
        # nothing to run. Clocks substituted.
        from unittest import mock
        tid = self._tid()
        text_adapter = ScriptAdapter([LLMError("http-503")] * 9)
        with mock.patch("time.sleep"):
            out = self._run(
                research_model=self._research([_research_json(tid)]),
                analysis_model=self._analysis([_analysis_json(tid)]),
                text_gen=OpenAITextGenerator(
                    api_key="k", adapter=text_adapter))
        self.assertNotEqual(out.error, "")
        self.assertEqual(out.intents, [])
        self.assertEqual(out.packages, [])
        self.assertFalse(out.committed)
        # Provider attempts (3) x stage attempts (3): both shared
        # layers exhausted before the terminal error.
        self.assertEqual(len(text_adapter.calls), 9)

    def test_prompt_failure_blocks_media_without_tuzzina_contact(self):
        # Proof 6+9: image intent + failing prompt agent => the
        # exception propagates out of packaging (zero packages),
        # so _resolve_media (the only Tuzzina image caller) can
        # never run. run_cycle itself performs no network I/O.
        tid = self._tid()
        prompt_adapter = ScriptAdapter([RuntimeError("edge-down")] * 1)
        prompt = OpenAIImagePromptGenerator(api_key="k",
                                           adapter=prompt_adapter)
        out = self._run(
            research_model=self._research([_research_json(tid)]),
            analysis_model=self._analysis([_analysis_json(tid, "image")]),
            text_gen=MockTextGenerator(),
            image_gen=MockImageGenerator(),
            policy={**POLICY, "image_prompt_gen": prompt})
        self.assertNotEqual(out.error, "")
        self.assertEqual(out.packages, [])
        self.assertEqual(out.intents, [])

    def test_prompt_rescue_returns_valid_prompt(self):
        # Proof 7 (image role shares the helper): first attempt
        # empty, second valid => prompt returned, one logical task.
        adapter = ScriptAdapter([LLMError("empty-content"),
                                 "Studio scene, soft light"])
        gen = OpenAIImagePromptGenerator(api_key="k", adapter=adapter)
        out = gen.generate_prompt("Harbor day", {"name": "B"}, {})
        self.assertEqual(out, "Studio scene, soft light")
        self.assertEqual(len(adapter.calls), 2)

    def test_text_rescue_returns_valid_post(self):
        # Proof 7 (text role shares the helper).
        adapter = ScriptAdapter([LLMError("http-429"), "Post body"])
        gen = OpenAITextGenerator(api_key="k", adapter=adapter)
        out = gen.generate("T", "S", {}, {})
        self.assertEqual(out, "Post body")
        self.assertEqual(len(adapter.calls), 2)

    def test_analysis_rescue_unlocks_text(self):
        # Proof 7 (analysis role shares the helper): fail once,
        # then decide eligible; text stage proceeds.
        tid = self._tid()
        text = CountCalls(self._text(["Body here"]), "generate")
        out = self._run(
            research_model=self._research([_research_json(tid)]),
            analysis_model=self._analysis(
                [LLMError("malformed-response"), _analysis_json(tid)]),
            text_gen=text)
        self.assertEqual(out.error, "")
        self.assertTrue(out.opportunity.eligible)
        self.assertEqual(text.calls, 1)
        self.assertEqual(len(out.intents), 1)


if __name__ == "__main__":
    unittest.main()
