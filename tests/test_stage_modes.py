"""Stage-scoped Brain entry points: contracts without duplication.

Proves, with scripted adapters and stub clients (no network,
no keys, no live calls):
1. analysis/text/prompt stages return validated payloads.
2. failing stages raise with the original domain error
   (callers map to envelopes; contracts unchanged).
3. serialization round-trips (dict -> object -> dict).
4. FixedText/FixedPrompt replay is byte-identical to the
   legacy path for the same validated strings.
5. execute dry-run performs zero writes and assigns
   deterministic value ids.
6. CLI harness fails closed (exit 2) on bad args/files.
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from g2.generators import (FixedPromptGenerator, FixedTextGenerator,
                           MockImageGenerator, MockTextGenerator)
from g2.pipeline import build_package
from llm.adapters import LLMError
from research.models import _trace_id
from stage_run import (_as_opportunity, _as_research_result,
                       _as_source_item, _to_jsonable, main_stage,
                       run_analysis_stage, run_execute_stage,
                       run_prompt_stage, run_research_stage,
                       run_text_stage, STAGE_BASE_KEYS,
                       QUEUE_SOURCE, _queue_consume, _queue_take)


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


def _item_dict(i=0):
    return {
        "source_id": f"s-{i}", "source_type": "rss",
        "source_url": f"https://demo.test/{i}",
        "title": f"Harbor news {i}",
        "text": "Harbor masterclass announced today.",
        "images": [], "links": [], "hashtags": [],
        "published_at": "2026-09-08T09:00:00+00:00",
        "platform": "rss", "item_id": f"g-{i}",
        "content_hash": f"h{i}",
    }


def _research_dict(tid="g-0"):
    return {
        "summary": "s", "findings": [
            {"statement": "Harbor masterclass announced",
             "kind": "fact", "confidence": "high",
             "item_ids": [tid], "excerpt": "Harbor masterclass"}],
        "topics": ["t"], "entities": [], "item_ids": [tid],
        "earliest_published": "", "latest_published": "",
        "model": "", "meta": {}}


def _opp_dict(media="none"):
    return {
        "eligible": True, "topic": "Harbor masterclass",
        "angle": "Loudness basics", "rationale": "R",
        "facts": ["Harbor masterclass announced"],
        "source_item_ids": ["g-0"], "content_format": "post",
        "media_intent": media, "audience": "a", "language": "en",
        "priority": "normal", "confidence": "high",
        "constraints": [], "model": "", "meta": {}}


BRAND = {"tone": "bold", "audience": "founders",
         "banned_words": ["spam"], "preferred_words": ["launch"],
         "cta_style": "Try it now"}
POLICY = {"brand": dict(BRAND), "language": {"default": "en"},
          "hashtags": {"enabled": False}, "links_policy": "hide",
          "pillars": [], "generation": {}, "media": {}}


class FakeClient:
    def __init__(self):
        self.released = []
        self.calls = []

    def release_claim(self, key, run_id):
        self.released.append((key, run_id))
        return 1


class RoundTripCase(unittest.TestCase):
    def test_source_item_round_trip(self):
        raw = _item_dict()
        back = _to_jsonable(_as_source_item(raw))
        for key in ("source_id", "title", "text", "item_id",
                    "platform"):
            self.assertEqual(back[key], raw[key])

    def test_research_round_trip(self):
        back = _to_jsonable(_as_research_result(_research_dict()))
        self.assertEqual(back["findings"][0]["kind"], "fact")
        self.assertEqual(back["summary"], "s")

    def test_opportunity_round_trip(self):
        back = _to_jsonable(_as_opportunity(_opp_dict("image")))
        self.assertTrue(back["eligible"])
        self.assertEqual(back["media_intent"], "image")

    def test_rejects_garbage(self):
        for fn, bad in ((_as_source_item, []),
                        (_as_research_result, "x"),
                        (_as_opportunity, None)):
            with self.assertRaises(ValueError):
                fn(bad)


class AnalysisStageCase(unittest.TestCase):
    def _ctx(self, adapter):
        from research.analysis import OpenAIAnalysisModel
        return {"client": FakeClient(), "run_id": "r1",
                "cfg": dict(POLICY),
                "analysis_model": OpenAIAnalysisModel(adapter=adapter),
                "research": _research_dict(),
                "items": [_item_dict()],
                "claimed": []}

    def test_valid_opportunity(self):
        from research.analysis import OpenAIAnalysisModel  # noqa
        adapter = ScriptAdapter([json.dumps({
            "eligible": True, "topic": "T", "angle": "A",
            "rationale": "R", "facts": ["Harbor masterclass announced"],
            "source_item_ids": ["g-0"], "content_format": "post",
            "media_intent": "none", "audience": "a", "language": "en",
            "priority": "normal", "confidence": "high",
            "constraints": []})])
        out = run_analysis_stage(self._ctx(adapter))
        self.assertTrue(out["opportunity"]["eligible"])
        self.assertEqual(out["claimed"], [])

    def test_invalid_output_propagates(self):
        adapter = ScriptAdapter([LLMError("empty-content")] * 3)
        with self.assertRaises(Exception) as ctx:
            run_analysis_stage(self._ctx(adapter))
        self.assertIn("model-error", str(ctx.exception))


class TextStageCase(unittest.TestCase):
    def _ctx(self, adapter):
        from g2.generators import OpenAITextGenerator
        return {"client": FakeClient(), "run_id": "r1",
                "cfg": dict(POLICY),
                "text_gen": OpenAITextGenerator(api_key="k",
                                                adapter=adapter),
                "opportunity": _opp_dict(),
                "items": [_item_dict()],
                "claimed": []}

    def test_valid_text(self):
        out = run_text_stage(self._ctx(ScriptAdapter(["Hello harbor"])))
        self.assertIn("Hello harbor", out["text"])

    def test_text_failure_releases_claims(self):
        client = FakeClient()
        ctx = self._ctx(ScriptAdapter([LLMError("http-500")] * 3))
        ctx["client"] = client
        ctx["claimed"] = ["k1"]
        with self.assertRaises(Exception):
            run_text_stage(ctx)
        self.assertEqual(client.released, [("k1", "r1")])


class PromptStageCase(unittest.TestCase):
    def _ctx(self, adapter):
        from g2.generators import OpenAIImagePromptGenerator
        return {"client": FakeClient(), "run_id": "r1",
                "cfg": dict(POLICY),
                "prompt_gen": OpenAIImagePromptGenerator(
                    api_key="k", adapter=adapter),
                "topic": "Harbor day",
                "claimed": []}

    def test_valid_prompt(self):
        out = run_prompt_stage(
            self._ctx(ScriptAdapter(["Studio scene, soft light"])))
        self.assertEqual(out["prompt"], "Studio scene, soft light")

    def test_prompt_failure_propagates(self):
        with self.assertRaises(Exception):
            run_prompt_stage(
                self._ctx(ScriptAdapter([LLMError("empty-content")] * 3)))


class FixedReplayCase(unittest.TestCase):
    def test_fixed_text_matches_legacy_path(self):
        from contracts import SourceItem
        item = SourceItem(**_item_dict())
        brand, policy = dict(BRAND), dict(POLICY)
        legacy = build_package(
            item, brand, policy.get("language"),
            policy.get("hashtags"), "hide",
            MockTextGenerator(), None, platform="facebook",
            policy=policy)
        fixed = build_package(
            item, brand, policy.get("language"),
            policy.get("hashtags"), "hide",
            FixedTextGenerator(legacy.content), None,
            platform="facebook", policy=policy)
        self.assertEqual(fixed.content, legacy.content)
        self.assertEqual(fixed.hashtags, legacy.hashtags)

    def test_fixed_prompt_matches_legacy_path(self):
        from contracts import SourceItem
        item = SourceItem(**_item_dict())
        brand, policy = dict(BRAND), dict(POLICY)
        legacy = build_package(
            item, brand, policy.get("language"),
            policy.get("hashtags"), "hide",
            MockTextGenerator(), MockImageGenerator(),
            platform="facebook", policy=policy)
        fixed_policy = dict(policy)
        fixed_policy["image_prompt_gen"] = FixedPromptGenerator(
            legacy.media[0]["prompt"])
        fixed = build_package(
            item, brand, policy.get("language"),
            policy.get("hashtags"), "hide",
            MockTextGenerator(), MockImageGenerator(),
            platform="facebook", policy=fixed_policy)
        self.assertEqual(fixed.media[0]["prompt"],
                         legacy.media[0]["prompt"])


class ExecuteStageCase(unittest.TestCase):
    def _ctx(self):
        return {
            "client": FakeClient(), "run_id": "run-9",
            "cfg": dict(POLICY),
            "opportunity": _opp_dict(),
            "items": [_item_dict()],
            "text": "Harbor post body here",
            "prompt": None,
            "claimed": ["k1"],
            "dry_run": True, "mode": "draft",
            "state_store": "memory",
            "integration_id": "int-1",
            "schedule": {"timezone": "UTC", "times": ["09:00"]},
        }

    def test_dry_run_writes_nothing_with_deterministic_ids(self):
        client = FakeClient()
        ctx = self._ctx()
        ctx["client"] = client
        out = run_execute_stage(ctx)
        self.assertEqual(out["post_ids"], [])
        self.assertEqual(out["intents"], 1)
        self.assertEqual(out["packages"], 1)
        # Claims released at end even on the dry path.
        self.assertEqual(client.released, [("k1", "run-9")])

    def test_deterministic_value_ids_shape(self):
        out = run_execute_stage(self._ctx())
        self.assertEqual(out["value_ids"], ["brainrun:run-9:0:main"])
        self.assertEqual(out["media_prompts"], [])

    def test_image_intent_records_prompt_without_resolving(self):
        # Image intent + dry-run: the Fixed replay prompt lands in
        # the package (visible as media_prompts evidence) while
        # zero Tuzzina calls happen (no bytes, no upload).
        ctx = self._ctx()
        ctx["opportunity"] = _opp_dict("image")
        ctx["prompt"] = "STUDIO PROMPT HERE"
        out = run_execute_stage(ctx)
        self.assertEqual(out["intents"], 1)
        self.assertEqual(out["media_prompts"], ["STUDIO PROMPT HERE"])
        self.assertEqual(out["post_ids"], [])
        self.assertEqual(out["media"], [])


class EnvelopeMappingCase(unittest.TestCase):
    def test_retryable_error_maps_to_retryable_envelope(self):
        from llm.adapters import LLMError
        from stage_run import RETRYABLE, SUCCESS, TERMINAL, \
            stage_error_envelope
        stage, status, payload, error = stage_error_envelope(
            "research", LLMError("empty-content"))
        self.assertEqual((stage, status, payload), ("research", RETRYABLE, None))
        self.assertIn("empty-content", error)
        self.assertEqual(RETRYABLE, "RETRYABLE_STAGE_FAILURE")
        self.assertEqual(TERMINAL, "TERMINAL_FAILURE")
        self.assertEqual(SUCCESS, "SUCCESS")

    def test_terminal_error_maps_to_terminal_envelope(self):
        from stage_run import TERMINAL, stage_error_envelope
        stage, status, payload, error = stage_error_envelope(
            "analysis", ValueError("bad-config"))
        self.assertEqual((stage, status, payload),
                         ("analysis", TERMINAL, None))
        self.assertIn("bad-config", error)

    def test_envelope_round_trip_through_emit(self):
        import io
        from contextlib import redirect_stdout
        from stage_run import stage_error_envelope
        from llm.adapters import LLMError
        import json as _json
        buf = io.StringIO()
        with redirect_stdout(buf):
            from stage_run import _emit_stage
            rc = _emit_stage(
                *stage_error_envelope("text", LLMError("http-503")))
        self.assertEqual(rc, 0)
        line = buf.getvalue().strip()
        self.assertTrue(line.startswith("BRAIN_STAGE "))
        env = _json.loads(line[len("BRAIN_STAGE "):])
        self.assertEqual(env["status"], "RETRYABLE_STAGE_FAILURE")
        self.assertEqual(env["stage"], "text")


class StageModeErrorPathCase(unittest.TestCase):
    def test_stage_exception_becomes_retryable_envelope(self):
        # Regression: the exception-to-envelope mapping itself
        # must never raise (a NameError here once turned every
        # retryable failure into an exit-1 crash).
        import io
        import json as _json
        from contextlib import redirect_stdout
        import stage_run as _sr
        from llm.adapters import LLMError
        real_research = _sr.run_research_stage
        real_ctx = _sr._stage_ctx

        def boom(_ctx):
            raise LLMError("empty-content")

        class Base:
            mode = "--openai"
            dry_run = True
            state_store = "memory"
            strategy_by_integration = "int-1"
            campaign = "c.yaml"
            run_id = "r-1"

        _sr.run_research_stage = boom
        _sr._stage_ctx = lambda base, run_id, stage_input: {}
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                rc = _sr._run_stage_mode("research", "", Base())
        finally:
            _sr.run_research_stage = real_research
            _sr._stage_ctx = real_ctx
        self.assertEqual(rc, 0)
        line = buf.getvalue().strip()
        self.assertTrue(line.startswith("BRAIN_STAGE "))
        env = _json.loads(line[len("BRAIN_STAGE "):])
        self.assertEqual(env["status"], "RETRYABLE_STAGE_FAILURE")
        self.assertEqual(env["stage"], "research")

    def test_stage_terminal_error_becomes_terminal_envelope(self):
        import io
        import json as _json
        from contextlib import redirect_stdout
        import stage_run as _sr
        real_text = _sr.run_text_stage
        real_ctx = _sr._stage_ctx

        def boom(_ctx):
            raise ValueError("bad-config")

        class Base:
            mode = "--openai"
            dry_run = True
            state_store = "memory"
            strategy_by_integration = "int-1"
            campaign = "c.yaml"
            run_id = "r-1"

        _sr.run_text_stage = boom
        _sr._stage_ctx = lambda base, run_id, stage_input: {}
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                rc = _sr._run_stage_mode("text", "", Base())
        finally:
            _sr.run_text_stage = real_text
            _sr._stage_ctx = real_ctx
        self.assertEqual(rc, 0)
        env = _json.loads(buf.getvalue().strip()[len("BRAIN_STAGE "):])
        self.assertEqual(env["status"], "TERMINAL_FAILURE")


class ResearchPayloadCapCase(unittest.TestCase):
    def test_full_collection_trimmed_to_max_items(self):
        # Regression: a campaign collecting up to 80 items
        # (~4KB each) burst the 512KB stage envelope and died
        # with oversize payload. The research LLM only ever
        # reads MAX_ITEMS, so the stage carries exactly those;
        # items_new keeps the discovery count, claims stay held.
        import research.cycle as _cyc
        from contracts import SourceItem
        from research.models import MAX_ITEMS, ResearchResult
        real_collect = _cyc.collect_all_sources

        def fake_collect(sources, store, now, out, pending):
            for i in range(MAX_ITEMS * 4):
                out.items.append(SourceItem(
                    source_id="s-%d" % i, source_type="website",
                    source_url="https://x/%d" % i,
                    title="t-%d" % i, text="w" * 4000))

        class Model:
            def research(self, items, cfg):
                return ResearchResult(
                    summary="s", findings=[], topics=[],
                    entities=[], item_ids=[], model="stub",
                    meta={})

        ctx = {"store": object(), "cfg": {},
               "client": object(), "run_id": "r-1",
               "sources": [{"type": "website", "url": "https://x"}],
               "now": "t", "research_model": Model()}
        _cyc.collect_all_sources = fake_collect
        try:
            payload = run_research_stage(ctx)
        finally:
            _cyc.collect_all_sources = real_collect
        self.assertEqual(payload["items_new"], MAX_ITEMS * 4)
        self.assertLessEqual(len(payload["items"]), MAX_ITEMS)
        self.assertLess(len(json.dumps(payload)), 512 * 1024)


class AnalysisSeesResearchCase(unittest.TestCase):
    def test_base_keys_carry_research(self):
        # Regression: "research" was missing from the carried
        # keys, so analysis always read an empty result and every
        # run ended as a no-findings no-op no matter what the
        # research agent found.
        self.assertIn("research", STAGE_BASE_KEYS)

    def test_analysis_honors_carried_research(self):
        # The carried research findings must drive the verdict:
        # a FACT finding means eligible, not no-findings.
        from contracts import SourceItem
        from research.analysis import MockAnalysisModel
        from research.models import Finding, ResearchResult
        it = SourceItem(source_id="g-1", source_type="website",
                        source_url="https://x/1", title="T",
                        text="Body words.", item_id="g-1",
                        content_hash="h")

        class Client:
            def release_claim(self, key, run_id):
                pass

        ctx = {"client": Client(), "run_id": "r-1",
               "cfg": {"brand": {"tone": "direct",
                                 "audience": "producers"},
                       "language": {"default": "en"},
                       "pillars": [], "media": {}},
               "research": _to_jsonable(ResearchResult(
                   summary="S",
                   findings=[Finding(
                       statement="Fact words", kind="fact",
                       confidence="high", item_ids=["g-1"],
                       excerpt="Fact")],
                   topics=["t"], entities=[], item_ids=["g-1"],
                   model="stub", meta={})),
               "items": [_to_jsonable(it)], "claimed": [],
               "analysis_model": MockAnalysisModel()}
        payload = run_analysis_stage(ctx)
        self.assertTrue(payload["opportunity"]["eligible"])


class QueueCase(unittest.TestCase):
    class FakeClient:
        def __init__(self, states=None, broken=False):
            self.states = dict(states or {})
            self.broken = broken

        def get_research_state(self, source):
            if self.broken:
                raise ConnectionError("down")
            return self.states.get(source)

        def save_research_state(self, source, state):
            if self.broken:
                raise ConnectionError("down")
            self.states[source] = state
            return {}

    def entry(self, stmt="Fact words", iid="g-1", attempts=0):
        return {"statement": stmt, "kind": "fact",
                "confidence": "high", "item_ids": [iid],
                "excerpt": stmt[:60], "title": "T",
                "text": "Body words.", "source_url": "https://x/1",
                "attempts": attempts, "queued_at": "2026-01-01"}

    def queued_state(self, entries):
        return {QUEUE_SOURCE: {"candidates": {
            ("g-%d" % i if e["item_ids"] == ["g-1"] else "q:%d" % i): e
            for i, e in enumerate(entries)}}}

    def test_starving_cycle_reuses_queue(self):
        # No fresh items + parked findings = the run proceeds
        # on queued material instead of stopping with nothing.
        import research.cycle as _cyc
        real_collect = _cyc.collect_all_sources
        fc = self.FakeClient(self.queued_state([self.entry()]))
        _cyc.collect_all_sources = lambda *a: None
        try:
            payload = run_research_stage(
                {"store": object(), "cfg": {}, "client": fc,
                 "run_id": "r-1", "sources": [], "now": "t",
                 "dry_run": False})
        finally:
            _cyc.collect_all_sources = real_collect
        self.assertTrue(payload["queued"])
        self.assertEqual(len(payload["items"]), 1)
        self.assertEqual(
            payload["research"]["findings"][0]["statement"],
            "Fact words")
        saved = fc.states[QUEUE_SOURCE]["candidates"]
        self.assertEqual(list(saved.values())[0]["attempts"], 1)

    def test_ineligible_parks_findings(self):
        # Analysis says no: the findings park for the next
        # cycle instead of dying with this run.
        from research.analysis import ContentOpportunity

        class Nope:
            def analyze(self, result, policy, items=None):
                return ContentOpportunity(
                    eligible=False, rationale="no-fit")

        fc = self.FakeClient()
        finding = {"statement": "Fact words", "kind": "fact",
                   "confidence": "high", "item_ids": ["g-1"],
                   "excerpt": "Fact words"}
        item = {"source_id": "g-1", "source_type": "website",
                "source_url": "https://x/1", "title": "T",
                "text": "Body words.", "item_id": "g-1"}
        payload = run_analysis_stage(
            {"client": fc, "run_id": "r-1", "cfg": {},
             "research": {"summary": "S", "findings": [finding],
                          "topics": [], "entities": [],
                          "item_ids": ["g-1"], "model": "m",
                          "meta": {}},
             "items": [item], "claimed": [],
             "analysis_model": Nope(), "dry_run": False,
             "now": "t"})
        self.assertFalse(payload["opportunity"]["eligible"])
        saved = fc.states[QUEUE_SOURCE]["candidates"]
        self.assertEqual(len(saved), 1)
        self.assertEqual(
            list(saved.values())[0]["statement"], "Fact words")

    def test_publish_consumes_queue(self):
        # Posted entries leave; the rest stays unpublished.
        fc = self.FakeClient(self.queued_state(
            [self.entry("One", "g-1"), self.entry("Two", "g-2")]))
        _queue_consume(fc, ["g-1"])
        saved = fc.states[QUEUE_SOURCE]["candidates"]
        self.assertEqual(len(saved), 1)
        self.assertEqual(
            list(saved.values())[0]["statement"], "Two")

    def test_stale_entries_drop(self):
        fc = self.FakeClient(self.queued_state(
            [self.entry(attempts=3)]))
        self.assertIsNone(_queue_take(fc))
        self.assertEqual(
            fc.states[QUEUE_SOURCE]["candidates"], {})

    def test_broken_transport_never_clobbers(self):
        fc = self.FakeClient(broken=True)
        self.assertIsNone(_queue_take(fc))
        from stage_run import _queue_add
        _queue_add(fc, [{"statement": "S", "kind": "fact",
                         "confidence": "high", "item_ids": ["g-9"],
                         "excerpt": "S"}], [], "t")
        self.assertEqual(fc.states, {})


class CliCase(unittest.TestCase):
    def test_bad_stage_rejected(self):
        self.assertEqual(
            main_stage(["--stage", "nope", "campaign.yaml"]), 2)

    def test_missing_input_file_rejected(self):
        self.assertEqual(
            main_stage(["--stage", "research", "--stage-input",
                        "C:\\nope\\missing.json", "campaign.yaml",
                        "--strategy-by-integration", "x"]), 2)


if __name__ == "__main__":
    unittest.main()
