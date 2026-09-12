"""Phase 1 tests: multi-provider LLM execution core. All transport
is faked (no network, no keys, no live calls). Proves: OpenAI
protocol, Anthropic protocol, closed dispatch, per-role
independence, runtime switching, contract stability, secrecy."""
import io
import json
import os
import sys
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from llm.adapters import (ADAPTERS, LLMError, AnthropicAdapter,
                           OpenAIAdapter, OpenCodeZenAdapter,
                           resolve_adapter)


def _openai_envelope(text="hello world"):
    return json.dumps({"choices": [{"message": {"content": text}}]})


def _research_json(ids=("g-1",)):
    return json.dumps({
        "summary": "Summary words.",
        "findings": [{"statement": "Fact words here",
                      "kind": "fact", "confidence": "high",
                      "item_ids": list(ids), "excerpt": "Fact"}],
        "topics": ["t"], "entities": []})


def _analysis_json(ids=("g-1",)):
    return json.dumps({
        "eligible": True, "topic": "T", "angle": "A",
        "rationale": "R", "facts": ["Fact words here"],
        "source_item_ids": list(ids), "content_format": "post",
        "media_intent": "none", "audience": "a", "language": "ar",
        "priority": "normal", "confidence": "high", "constraints": []})


def _headers(req):
    return {k.lower(): v for k, v in req.header_items()}


def _anthropic_msg(text="hello world", stop="end_turn", extra=None):
    body = {"id": "msg_1", "type": "message", "role": "assistant",
            "model": "m",
            "content": [{"type": "text", "text": text}],
            "stop_reason": stop,
            "usage": {"input_tokens": 1, "output_tokens": 1}}
    if extra:
        body.update(extra)
    return json.dumps(body)


class FakeResp:
    def __init__(self, payload, code=200):
        self.payload = payload
        self.code = code

    def read(self):
        return self.payload.encode("utf-8")

    def __enter__(self):
        if self.code >= 400:
            raise urllib.error.HTTPError("u", self.code, "err", {}, None)
        return self

    def __exit__(self, *a):
        return False


CALLS = []


def fake_urlopen(req, timeout=None):
    CALLS.append({"url": req.full_url,
                  "headers": _headers(req),
                  "body": json.loads(req.data.decode())})
    kind = getattr(fake_urlopen, "mode", "openai-ok")
    if kind == "openai-ok":
        return FakeResp(_openai_envelope())
    if kind == "openai-research":
        return FakeResp(_openai_envelope(_research_json()))
    if kind == "openai-analysis":
        return FakeResp(_openai_envelope(_analysis_json()))
    if kind == "anthropic-ok":
        return FakeResp(_anthropic_msg())
    if kind == "anthropic-research":
        return FakeResp(_anthropic_msg(_research_json()))
    if kind == "anthropic-analysis":
        return FakeResp(_anthropic_msg(_analysis_json()))
    if kind == "zen-ok":
        return FakeResp(_openai_envelope())
    if kind == "zen-research":
        return FakeResp(_openai_envelope(_research_json()))
    if kind == "http-500":
        return FakeResp({}, 500)
    if kind == "http-401":
        return FakeResp({}, 401)
    if kind == "timeout":
        raise TimeoutError("slow")
    if kind == "conn":
        raise ConnectionError("down")
    if kind == "garbage":
        return FakeResp("not json{{{")
    raise AssertionError(kind)


class OpenAIProtocolCase(unittest.TestCase):
    def setUp(self):
        CALLS.clear()
        self._orig = urllib.request.urlopen
        urllib.request.urlopen = fake_urlopen
        fake_urlopen.mode = "openai-ok"

    def tearDown(self):
        urllib.request.urlopen = self._orig

    def test_valid_response(self):
        a = OpenAIAdapter("k", "gpt-4.1")
        self.assertEqual(a.complete("sys", "user"), "hello world")

    def test_request_shape(self):
        OpenAIAdapter("k", "gpt-4.1").complete(
            "sys", "user", temperature=0.2, max_tokens=10, timeout=5)
        self.assertEqual(len(CALLS), 1)
        c = CALLS[0]
        self.assertEqual(
            c["url"], "https://api.openai.com/v1/chat/completions")
        self.assertEqual(c["headers"].get("authorization"), "Bearer k")
        self.assertEqual(c["body"]["model"], "gpt-4.1")
        self.assertEqual(c["body"]["temperature"], 0.2)
        self.assertEqual(c["body"]["max_tokens"], 10)
        self.assertEqual(c["body"]["messages"],
                         [{"role": "system", "content": "sys"},
                          {"role": "user", "content": "user"}])

    def test_malformed_response(self):
        fake_urlopen.mode = "garbage"
        with self.assertRaises(LLMError):
            OpenAIAdapter("k").complete("s", "u", timeout=1)

    def test_empty_content(self):
        class R(FakeResp):
            def read(self):
                return _openai_envelope("   ").encode()
        urllib.request.urlopen = lambda *a, **k: R("")
        with self.assertRaises(LLMError) as ctx:
            OpenAIAdapter("k").complete("s", "u", timeout=1)
        self.assertIn("empty-content", str(ctx.exception))

    def test_missing_choices(self):
        urllib.request.urlopen = lambda *a, **k: FakeResp(
            json.dumps({"oops": 1}))
        with self.assertRaises(LLMError):
            OpenAIAdapter("k").complete("s", "u", timeout=1)

    def test_http_error(self):
        fake_urlopen.mode = "http-500"
        with self.assertRaises(LLMError) as ctx:
            OpenAIAdapter("k").complete("s", "u", timeout=1)
        self.assertIn("http-500", str(ctx.exception))

    def test_timeout_propagates_raw(self):
        fake_urlopen.mode = "timeout"
        with self.assertRaises(TimeoutError):
            OpenAIAdapter("k").complete("s", "u", timeout=1)

    def test_connection_mapped(self):
        fake_urlopen.mode = "conn"
        with self.assertRaises(LLMError) as ctx:
            OpenAIAdapter("k").complete("s", "u", timeout=1)
        self.assertIn("ConnectionError", str(ctx.exception))

    def test_needs_key(self):
        with self.assertRaises(ValueError):
            OpenAIAdapter("")

    def test_base_url_override(self):
        a = OpenAIAdapter("k", base_url="https://proxy.test")
        a.complete("s", "u", timeout=1)
        self.assertEqual(CALLS[0]["url"],
                         "https://proxy.test/v1/chat/completions")


class AnthropicProtocolCase(unittest.TestCase):
    def setUp(self):
        CALLS.clear()
        self._orig = urllib.request.urlopen
        urllib.request.urlopen = fake_urlopen
        fake_urlopen.mode = "anthropic-ok"

    def tearDown(self):
        urllib.request.urlopen = self._orig

    def test_valid_response(self):
        a = AnthropicAdapter("k2", "m")
        self.assertEqual(a.complete("sys", "user"), "hello world")

    def test_request_shape(self):
        AnthropicAdapter("k2", "m").complete(
            "sys", "user", temperature=0.3, max_tokens=50, timeout=5)
        c = CALLS[0]
        self.assertEqual(c["url"], "https://api.anthropic.com/v1/messages")
        self.assertEqual(c["headers"].get("x-api-key"), "k2")
        self.assertEqual(c["headers"].get("anthropic-version"),
                         "2023-06-01")
        self.assertNotIn("authorization", c["headers"])
        self.assertEqual(c["body"]["model"], "m")
        self.assertEqual(c["body"]["system"], "sys")
        self.assertEqual(c["body"]["max_tokens"], 50)
        self.assertEqual(c["body"]["messages"],
                         [{"role": "user", "content": "user"}])

    def test_multi_block_concatenation(self):
        body = json.dumps({"content": [{"type": "text", "text": "He"},
                                        {"type": "tool_use", "id": "t",
                                         "name": "n", "input": {}},
                                        {"type": "text", "text": "llo"}],
                           "stop_reason": "end_turn"})
        urllib.request.urlopen = lambda *a, **k: FakeResp(body)
        self.assertEqual(AnthropicAdapter("k").complete("s", "u",
                                                        timeout=1),
                         "Hello")

    def test_refusal_rejected(self):
        urllib.request.urlopen = lambda *a, **k: FakeResp(
            _anthropic_msg(text="", stop="refusal"))
        with self.assertRaises(LLMError) as ctx:
            AnthropicAdapter("k").complete("s", "u", timeout=1)
        self.assertIn("refusal", str(ctx.exception))

    def test_tool_use_stop_rejected(self):
        body = json.dumps({"content": [{"type": "tool_use", "id": "t",
                                         "name": "n", "input": {}}],
                           "stop_reason": "tool_use"})
        urllib.request.urlopen = lambda *a, **k: FakeResp(body)
        with self.assertRaises(LLMError):
            AnthropicAdapter("k").complete("s", "u", timeout=1)

    def test_max_tokens_stop_rejected(self):
        urllib.request.urlopen = lambda *a, **k: FakeResp(
            _anthropic_msg(stop="max_tokens"))
        with self.assertRaises(LLMError) as ctx:
            AnthropicAdapter("k").complete("s", "u", timeout=1)
        self.assertIn("max_tokens", str(ctx.exception))

    def test_missing_content_rejected(self):
        urllib.request.urlopen = lambda *a, **k: FakeResp(
            json.dumps({"stop_reason": "end_turn"}))
        with self.assertRaises(LLMError):
            AnthropicAdapter("k").complete("s", "u", timeout=1)

    def test_http_error(self):
        fake_urlopen.mode = "http-401"
        with self.assertRaises(LLMError) as ctx:
            AnthropicAdapter("k").complete("s", "u", timeout=1)
        self.assertIn("http-401", str(ctx.exception))

    def test_timeout_propagates_raw(self):
        fake_urlopen.mode = "timeout"
        with self.assertRaises(TimeoutError):
            AnthropicAdapter("k").complete("s", "u", timeout=1)

    def test_malformed_json(self):
        fake_urlopen.mode = "garbage"
        with self.assertRaises(LLMError):
            AnthropicAdapter("k").complete("s", "u", timeout=1)

    def test_needs_key(self):
        with self.assertRaises(ValueError):
            AnthropicAdapter("")


class ZenProtocolCase(unittest.TestCase):
    """OpenCode Zen speaks the OpenAI-compatible chat completions
    surface against its own base URL; the model id passes through
    unchanged and no default model is invented."""

    def setUp(self):
        CALLS.clear()
        self._orig = urllib.request.urlopen
        urllib.request.urlopen = fake_urlopen
        fake_urlopen.mode = "zen-ok"

    def tearDown(self):
        urllib.request.urlopen = self._orig

    def test_valid_response(self):
        a = OpenCodeZenAdapter("zk", "some-model-id")
        self.assertEqual(a.complete("sys", "user"), "hello world")

    def test_request_shape(self):
        OpenCodeZenAdapter("zk", "some-model-id").complete(
            "sys", "user", temperature=0.2, max_tokens=10, timeout=5)
        self.assertEqual(len(CALLS), 1)
        c = CALLS[0]
        self.assertEqual(
            c["url"], "https://opencode.ai/zen/v1/chat/completions")
        self.assertEqual(c["headers"].get("authorization"), "Bearer zk")
        self.assertEqual(c["body"]["model"], "some-model-id")
        self.assertEqual(c["body"]["temperature"], 0.2)
        self.assertEqual(c["body"]["max_tokens"], 10)
        self.assertEqual(c["body"]["messages"],
                         [{"role": "system", "content": "sys"},
                          {"role": "user", "content": "user"}])

    def test_no_default_model(self):
        a = OpenCodeZenAdapter("zk")
        self.assertEqual(a.model, "")

    def test_malformed_response(self):
        fake_urlopen.mode = "garbage"
        with self.assertRaises(LLMError):
            OpenCodeZenAdapter("zk", "m").complete("s", "u", timeout=1)

    def test_empty_content(self):
        class R(FakeResp):
            def read(self):
                return _openai_envelope("   ").encode()
        urllib.request.urlopen = lambda *a, **k: R("")
        with self.assertRaises(LLMError) as ctx:
            OpenCodeZenAdapter("zk", "m").complete("s", "u", timeout=1)
        self.assertIn("empty-content", str(ctx.exception))

    def test_http_error(self):
        fake_urlopen.mode = "http-401"
        with self.assertRaises(LLMError) as ctx:
            OpenCodeZenAdapter("zk", "m").complete("s", "u", timeout=1)
        self.assertIn("http-401", str(ctx.exception))

    def test_timeout_propagates_raw(self):
        fake_urlopen.mode = "timeout"
        with self.assertRaises(TimeoutError):
            OpenCodeZenAdapter("zk", "m").complete("s", "u", timeout=1)

    def test_needs_key(self):
        with self.assertRaises(ValueError):
            OpenCodeZenAdapter("")

    def test_roles_use_zen_independently(self):
        from research.models import OpenAIResearchModel
        from g2.generators import OpenAITextGenerator
        from contracts import SourceItem
        it = SourceItem(source_id="g-1", source_type="rss",
                        source_url="u", title="T", text="Body words.",
                        item_id="g-1", content_hash="h")
        fake_urlopen.mode = "zen-research"
        r = OpenAIResearchModel(
            adapter=OpenCodeZenAdapter("zk", "zm")).research([it])
        self.assertTrue(r.summary)
        self.assertEqual(CALLS[0]["body"]["model"], "zm")
        fake_urlopen.mode = "openai-ok"
        t = OpenAITextGenerator(
            adapter=OpenAIAdapter("ok", "om")).generate("T", "S", {})
        self.assertTrue(t)
        urls = [c["url"] for c in CALLS]
        self.assertTrue(any("opencode.ai/zen" in u for u in urls))
        self.assertTrue(any("openai.com" in u for u in urls))

    def test_strategy_instructions_unchanged_through_zen(self):
        from g2.generators import OpenAITextGenerator
        pol = {"brand": {"tone": "bold", "voice": "direct"},
               "content": {"content_pillars": ["tips"]},
               "generation": {"prompt_style": "punchy"},
               "language": {"default": "ar"}}
        tg = OpenAITextGenerator(
            adapter=OpenCodeZenAdapter("zk", "zm"))
        tg.generate("T", "S", pol["brand"], pol)
        sys_prompt = CALLS[0]["body"]["messages"][0]["content"]
        for needle in ("bold", "direct", "tips", "punchy"):
            self.assertIn(needle, sys_prompt)

    def test_key_absent_from_output(self):
        from g2.generators import OpenAITextGenerator
        out = OpenAITextGenerator(
            adapter=OpenCodeZenAdapter("zen-secret-9", "m")).generate(
                "T", "S", {})
        self.assertNotIn("zen-secret-9", out)


class DispatchCase(unittest.TestCase):
    def test_openai_resolves(self):
        self.assertIs(resolve_adapter("openai"), OpenAIAdapter)

    def test_anthropic_resolves(self):
        self.assertIs(resolve_adapter("anthropic"), AnthropicAdapter)

    def test_zen_resolves(self):
        self.assertIs(resolve_adapter("opencode_zen"), OpenCodeZenAdapter)

    def test_unknown_fails_closed(self):
        for bad in ("gemini", "OPENAI", "", None, "openai ",
                    "opencode", "zen"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                resolve_adapter(bad)

    def test_closed_set_exact(self):
        self.assertEqual(set(ADAPTERS),
                         {"openai", "anthropic", "opencode_zen"})
        for key, cls in ADAPTERS.items():
            self.assertEqual(cls.adapter_key, key)
            self.assertTrue(hasattr(cls, "complete"))

    def test_no_metadata_in_dispatch(self):
        # Code constructs only: the module docstring legitimately
        # names what the dispatch must NOT hold.
        import pathlib
        src = (pathlib.Path(__file__).parent.parent / "src" /
               "llm" / "adapters.py").read_text(encoding="utf-8")
        code = []
        in_doc = False
        for ln in src.splitlines():
            s = ln.strip()
            if '"""' in s:
                if s.count('"""') == 2:
                    continue
                in_doc = not in_doc
                continue
            if in_doc or not s or s.startswith("#"):
                continue
            code.append(ln)
        blob = "\n".join(code)
        # Default model strings are preserved production behavior,
        # not a catalog; guard vendor geography + registry shapes.
        for bad in ("claude", "gemini", "pricing", "feature matrix",
                    "catalog", "capabilities", "model_list",
                    "available_models"):
            self.assertNotIn(bad, blob, bad)


class RoleIndependenceCase(unittest.TestCase):
    def setUp(self):
        CALLS.clear()
        self._orig = urllib.request.urlopen
        urllib.request.urlopen = fake_urlopen
        fake_urlopen.mode = "anthropic-ok"

    def tearDown(self):
        urllib.request.urlopen = self._orig

    def _research(self, adapter):
        from research.models import OpenAIResearchModel
        from contracts import SourceItem
        it = SourceItem(source_id="g-1", source_type="rss",
                        source_url="http://feed.test/1", title="T",
                        text="Body words here.", item_id="g-1",
                        content_hash="h")
        return OpenAIResearchModel("k", adapter=adapter).research([it])

    def _analysis(self, adapter):
        from research.analysis import OpenAIAnalysisModel
        from research.models import Finding, ResearchResult
        r = ResearchResult(
            summary="S", findings=[Finding("Fact words", "fact",
                                           "high", ["g-1"], "Fact")],
            topics=["t"], entities=[], item_ids=["g-1"],
            earliest_published="", latest_published="", model="m",
            meta={})
        return OpenAIAnalysisModel("k", adapter=adapter).analyze(
            r, {"brand": {}, "language": {}, "pillars": [],
                "generation": {}, "media": {}})

    def _text(self, adapter):
        from g2.generators import OpenAITextGenerator
        return OpenAITextGenerator("k", adapter=adapter).generate(
            "T", "S", {"tone": "b"})

    def test_research_uses_own_adapter(self):
        fake_urlopen.mode = "anthropic-research"
        self._research(AnthropicAdapter("rk", "rm"))
        self.assertEqual(CALLS[0]["url"],
                         "https://api.anthropic.com/v1/messages")
        self.assertEqual(CALLS[0]["headers"].get("x-api-key"), "rk")
        self.assertEqual(CALLS[0]["body"]["model"], "rm")

    def test_analysis_uses_own_adapter(self):
        fake_urlopen.mode = "openai-analysis"
        self._analysis(OpenAIAdapter("ak", "am"))
        self.assertIn("openai.com", CALLS[0]["url"])
        self.assertEqual(CALLS[0]["body"]["model"], "am")

    def test_text_uses_own_adapter(self):
        fake_urlopen.mode = "openai-ok"
        out = self._text(OpenAIAdapter("tk", "tm"))
        self.assertTrue(out)
        self.assertEqual(CALLS[0]["body"]["model"], "tm")

    def test_research_and_text_differ_simultaneously(self):
        fake_urlopen.mode = "openai-research"
        r = self._research(OpenAIAdapter("rk", "rm"))
        fake_urlopen.mode = "anthropic-ok"
        t = self._text(AnthropicAdapter("tk", "tm"))
        self.assertTrue(r.summary)
        self.assertTrue(t)
        urls = [c["url"] for c in CALLS]
        self.assertTrue(any("openai.com" in u for u in urls))
        self.assertTrue(any("anthropic.com" in u for u in urls))

    def test_three_independent_assignments(self):
        from research.analysis import OpenAIAnalysisModel
        from research.models import OpenAIResearchModel
        from g2.generators import OpenAITextGenerator
        rm = OpenAIResearchModel(
            adapter=AnthropicAdapter("k1", "m1"))
        am = OpenAIAnalysisModel(
            adapter=OpenAIAdapter("k2", "m2"))
        tg = OpenAITextGenerator(
            adapter=AnthropicAdapter("k3", "m3"))
        self.assertIsNot(rm._adapter, am._adapter)
        self.assertIsNot(rm._adapter, tg._adapter)
        self.assertEqual((rm._adapter.api_key, am._adapter.api_key,
                          tg._adapter.api_key), ("k1", "k2", "k3"))

    def test_explicit_adapter_wins(self):
        from g2.generators import OpenAITextGenerator
        g = OpenAITextGenerator("ignored", "ignored-model",
                                adapter=AnthropicAdapter("real", "m"))
        self.assertIsInstance(g._adapter, AnthropicAdapter)
        self.assertEqual(g._adapter.api_key, "real")

    def test_default_stays_openai(self):
        from g2.generators import OpenAITextGenerator
        from llm.adapters import OpenAIAdapter as OA
        self.assertIsInstance(OpenAITextGenerator("k")._adapter, OA)


class SwitchingCase(unittest.TestCase):
    def setUp(self):
        CALLS.clear()
        self._orig = urllib.request.urlopen
        urllib.request.urlopen = fake_urlopen

    def tearDown(self):
        urllib.request.urlopen = self._orig

    def test_text_openai_to_anthropic(self):
        from g2.generators import OpenAITextGenerator
        fake_urlopen.mode = "openai-ok"
        a = OpenAITextGenerator("k").generate("T", "S", {})
        fake_urlopen.mode = "anthropic-ok"
        b = OpenAITextGenerator(
            adapter=AnthropicAdapter("k", "m")).generate("T", "S", {})
        self.assertTrue(a and b)
        self.assertIn("openai.com", CALLS[0]["url"])
        self.assertIn("anthropic.com", CALLS[1]["url"])

    def test_research_anthropic_to_openai(self):
        from research.models import OpenAIResearchModel
        from contracts import SourceItem
        it = SourceItem(source_id="g-1", source_type="rss",
                        source_url="u", title="T", text="Body words.",
                        item_id="g-1", content_hash="h")
        fake_urlopen.mode = "anthropic-research"
        r1 = OpenAIResearchModel(
            adapter=AnthropicAdapter("k", "m")).research([it])
        fake_urlopen.mode = "openai-research"
        r2 = OpenAIResearchModel("k").research([it])
        self.assertEqual(r1.summary, r2.summary)
        self.assertEqual(r1.findings, r2.findings)

    def test_analysis_untouched_by_text_switch(self):
        from research.analysis import OpenAIAnalysisModel
        from research.models import Finding, ResearchResult
        r = ResearchResult(
            summary="S", findings=[Finding("F", "fact", "high",
                                           ["g-1"], "F")],
            topics=[], entities=[], item_ids=["g-1"],
            earliest_published="", latest_published="", model="m",
            meta={})
        pol = {"brand": {}, "language": {}, "pillars": [],
               "generation": {}, "media": {}}
        fake_urlopen.mode = "openai-analysis"
        m = OpenAIAnalysisModel("stable-key")
        o1 = m.analyze(r, pol)
        # text role switches elsewhere; analysis instance untouched
        o2 = m.analyze(r, pol)
        self.assertEqual(o1, o2)
        self.assertTrue(all(c["body"]["model"] == "gpt-4.1"
                            for c in CALLS))


class ContractCase(unittest.TestCase):
    def setUp(self):
        self._orig = urllib.request.urlopen
        urllib.request.urlopen = fake_urlopen
        fake_urlopen.mode = "anthropic-ok"

    def tearDown(self):
        urllib.request.urlopen = self._orig

    def test_research_result_shape_stable(self):
        fake_urlopen.mode = "anthropic-research"
        from research.models import OpenAIResearchModel
        from contracts import SourceItem
        it = SourceItem(source_id="g-1", source_type="rss",
                        source_url="u", title="T", text="Body words.",
                        item_id="g-1", content_hash="h")
        r = OpenAIResearchModel(
            adapter=AnthropicAdapter("k", "m")).research([it])
        self.assertTrue(r.summary)
        self.assertTrue(r.findings)
        self.assertEqual(r.item_ids, ["g-1"])

    def test_opportunity_shape_stable(self):
        fake_urlopen.mode = "openai-analysis"
        from research.analysis import OpenAIAnalysisModel
        from research.models import Finding, ResearchResult
        r = ResearchResult(
            summary="S", findings=[Finding("F", "fact", "high",
                                           ["g-1"], "F")],
            topics=[], entities=[], item_ids=["g-1"],
            earliest_published="", latest_published="", model="m",
            meta={})
        o = OpenAIAnalysisModel("k").analyze(
            r, {"brand": {}, "language": {}, "pillars": [],
                "generation": {}, "media": {}})
        self.assertTrue(isinstance(o.eligible, bool))
        self.assertEqual(o.content_format, "post")

    def test_text_result_is_str(self):
        from g2.generators import OpenAITextGenerator
        out = OpenAITextGenerator(
            adapter=AnthropicAdapter("k", "m")).generate("T", "S", {})
        self.assertIsInstance(out, str)

    def test_downstream_contracts_provider_free(self):
        # "PlatformAdapter" (social translators) legitimately names
        # adapters; guard LLM-vendor terms, not the word itself.
        import pathlib
        base = pathlib.Path(__file__).parent.parent / "src"
        for rel in ("g2/pipeline.py", "g3/planner.py",
                    "research/generation.py", "injection/intent.py",
                    "channels/strategy.py"):
            code = (base / rel).read_text(encoding="utf-8")
            for bad in ("Anthropic", "anthropic", "OPENAI_API_KEY",
                        "api.openai.com", "api.anthropic.com",
                        "adapter_key", "resolve_adapter"):
                self.assertNotIn(bad, code, f"{rel}:{bad}")


class SecurityCase(unittest.TestCase):
    def setUp(self):
        self._orig = urllib.request.urlopen
        urllib.request.urlopen = fake_urlopen
        fake_urlopen.mode = "anthropic-ok"

    def tearDown(self):
        urllib.request.urlopen = self._orig

    def test_keys_absent_from_outputs(self):
        from g2.generators import OpenAITextGenerator
        out = OpenAITextGenerator(
            adapter=AnthropicAdapter("super-secret-1", "m")).generate(
                "T", "S", {})
        self.assertNotIn("super-secret-1", out)

    def test_keys_absent_from_domain(self):
        import json
        fake_urlopen.mode = "anthropic-research"
        from research.models import OpenAIResearchModel
        from contracts import SourceItem
        it = SourceItem(source_id="g-1", source_type="rss",
                        source_url="u", title="T", text="Body words.",
                        item_id="g-1", content_hash="h")
        r = OpenAIResearchModel(
            adapter=AnthropicAdapter("super-secret-2", "m")).research(
                [it])
        self.assertNotIn("super-secret-2",
                         json.dumps(r.__dict__, default=str))

    def test_keys_absent_from_streams(self):
        import contextlib
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            AnthropicAdapter("super-secret-3", "m").complete(
                "s", "u", timeout=1)
        self.assertNotIn("super-secret-3", out.getvalue())
        self.assertNotIn("super-secret-3", err.getvalue())

    def test_no_argv_or_trace_secrets(self):
        # "_trace_id" (source traceability) and boundary prose
        # legitimately contain "trace"; guard real secret sinks.
        import pathlib
        base = pathlib.Path(__file__).parent.parent / "src"
        for rel in ("llm/adapters.py", "g2/generators.py",
                    "research/models.py", "research/analysis.py"):
            code = (base / rel).read_text(encoding="utf-8")
            for bad in ("sys.argv", "os.environ", "getenv(",
                        "logging.", "print(", "trace.emit", "Tracer("):
                self.assertNotIn(bad, code, f"{rel}:{bad}")


class HttpErrorBodyCase(unittest.TestCase):
    """_post_json keeps a safe redacted excerpt of provider error
    bodies: status plus evidence, never secrets. No network."""

    def setUp(self):
        self._orig = urllib.request.urlopen

    def tearDown(self):
        urllib.request.urlopen = self._orig

    def _fail(self, code, body: bytes):
        def _raise(req, timeout=None):
            raise urllib.error.HTTPError(
                req.full_url, code, "err", {}, io.BytesIO(body))
        urllib.request.urlopen = _raise

    def _complete(self):
        with self.assertRaises(LLMError) as ctx:
            OpenAIAdapter("k", "m").complete("s", "u", timeout=5)
        return str(ctx.exception)

    def test_400_keeps_json_excerpt(self):
        self._fail(400, b'{"type":"error","error":'
                        b'{"type":"invalid_request",'
                        b'"message":"temperature 0.2 not supported"}}')
        msg = self._complete()
        self.assertTrue(msg.startswith("http-400: "))
        self.assertIn("temperature 0.2 not supported", msg)

    def test_401_redacts_bearer_and_keys(self):
        self._fail(401, b'{"error":"bad key sk-test-abc123, '
                        b'header Bearer deadbeef, api_key=supersecret, '
                        b'password: hunter2"}')
        msg = self._complete()
        self.assertIn("http-401", msg)
        for secret in ("sk-test-abc123", "deadbeef",
                       "supersecret", "hunter2"):
            self.assertNotIn(secret, msg)
        self.assertIn("[REDACTED]", msg)

    def test_403_preserved(self):
        self._fail(403, b'{"error":{"message":"forbidden region"}}')
        msg = self._complete()
        self.assertIn("http-403", msg)
        self.assertIn("forbidden region", msg)

    def test_large_body_capped(self):
        self._fail(400, b'{"error":"' + b"x" * 5000 + b'"}')
        msg = self._complete()
        self.assertIn("http-400", msg)
        self.assertLessEqual(len(msg), len("http-400: ") + 500)

    def test_empty_body_keeps_bare_contract(self):
        self._fail(400, b"")
        self.assertEqual(self._complete(), "http-400")

    def test_unreadable_body_keeps_bare_contract(self):
        def _raise(req, timeout=None):
            raise urllib.error.HTTPError(
                req.full_url, 400, "err", {}, None)
        urllib.request.urlopen = _raise
        self.assertEqual(self._complete(), "http-400")

    def test_non_utf8_body_never_raises(self):
        self._fail(400, b"\xff\xfe\x00bad")
        msg = self._complete()
        self.assertTrue(msg.startswith("http-400"))


if __name__ == "__main__":
    unittest.main()
