"""R4 tests: content opportunity analysis. Mock is deterministic;
OpenAI role runs on faked transport (no network, no key). No
fetching, no state, no Tuzzina, no publishing anywhere here."""
import json
import os
import socket
import sys
import unittest
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from contracts import SourceItem
from research.analysis import (AnalysisError, ContentOpportunity,
                               MockAnalysisModel, OpenAIAnalysisModel)
from research.models import Finding, ResearchResult


def finding(stmt, kind="fact", ids=None, conf="high"):
    return Finding(statement=stmt, kind=kind, confidence=conf,
                   item_ids=list(ids or ["g-1"]),
                   excerpt=stmt[:60])


def research(findings, topics=None, summary="Harbor roundup."):
    return ResearchResult(
        summary=summary, findings=list(findings),
        topics=list(topics or ["harbor"]), entities=[],
        item_ids=sorted({i for f in findings for i in f.item_ids}),
        earliest_published="2026-09-08T09:00:00+00:00",
        latest_published="2026-09-08T11:00:00+00:00",
        model="mock", meta={})


def policy(**over):
    p = {"brand": {"tone": "direct", "audience": "Libya"},
         "language": {"default": "ar"},
         "pillars": [], "generation": {}, "media": {}}
    p.update(over)
    return p


def media_item(iid="m1"):
    return SourceItem(
        source_id=iid, source_type="website",
        source_url=f"http://demo.test/{iid}", title="T", text="x",
        images=["http://demo.test/a.jpg"], published_at="",
        platform="website", item_id=iid, content_hash="h")


class FakeResp:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return self.payload.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _openai_body(**over):
    inner = {"eligible": True, "topic": "Harbor festival",
             "angle": "Festival boats angle",
             "rationale": "Two facts support it.",
             "facts": ["Boats gather at dawn"],
             "source_item_ids": ["g-1"],
             "content_format": "post", "media_intent": "none",
             "audience": "Libya", "language": "ar",
             "priority": "normal", "confidence": "high",
             "constraints": ["language=ar"]}
    inner.update(over)
    return json.dumps({"choices": [{"message": {
        "content": json.dumps(inner)}}]})


class MockCase(unittest.TestCase):
    def test_relevant_eligible(self):
        r = research([finding("Boats gather at dawn"),
                      finding("Gulls follow the boats", ids=["g-2"])])
        o = MockAnalysisModel().analyze(r, policy())
        self.assertIsInstance(o, ContentOpportunity)
        self.assertTrue(o.eligible)
        self.assertTrue(o.topic)
        self.assertTrue(o.angle)
        self.assertEqual(o.facts,
                         ["Boats gather at dawn",
                          "Gulls follow the boats"])
        self.assertEqual(o.source_item_ids, ["g-1", "g-2"])
        self.assertEqual(o.audience, "Libya")
        self.assertEqual(o.language, "ar")
        self.assertEqual(o.confidence, "high")
        self.assertIn("language=ar", o.constraints)

    def test_irrelevant_pillar_mismatch(self):
        r = research([finding("Boats gather at dawn")])
        o = MockAnalysisModel().analyze(
            r, policy(pillars=["cooking"]))
        self.assertFalse(o.eligible)
        self.assertEqual(o.facts, [])
        self.assertIn("pillar-mismatch", o.meta["reason"])

    def test_multiple_items_one_opportunity(self):
        r = research([finding("A", ids=["g-1"]),
                      finding("B", ids=["g-2"]),
                      finding("C", ids=["g-3"])])
        o = MockAnalysisModel().analyze(r, policy())
        self.assertTrue(o.eligible)
        self.assertEqual(len(o.facts), 3)
        self.assertEqual(o.priority, "high")

    def test_unrelated_rejected(self):
        # No shared pillar, disjoint topics -> single batch judged
        # as a whole; mismatch rejects (separation across topics
        # is the orchestrator's future job, one call = one verdict).
        r = research([finding("Sourdough starter tips")],
                     topics=["baking"])
        o = MockAnalysisModel().analyze(r, policy(pillars=["music"]))
        self.assertFalse(o.eligible)

    def test_traceability(self):
        r = research([finding("Fact one", ids=["g-1"]),
                      finding("A guess", kind="inference",
                            ids=["g-1", "g-2"], conf="low")])
        o = MockAnalysisModel().analyze(r, policy())
        self.assertTrue(o.eligible)
        # facts stay FACT-sourced; inference never becomes a fact
        self.assertEqual(o.facts, ["Fact one"])
        # ...but its items join the references
        self.assertEqual(o.source_item_ids, ["g-1", "g-2"])

    def test_strategy_match(self):
        r = research([finding("Harbor music night")], topics=["music"])
        o = MockAnalysisModel().analyze(r, policy(pillars=["music"]))
        self.assertTrue(o.eligible)
        self.assertEqual(o.meta["pillar"], "music")
        self.assertIn("pillar=music", o.constraints)

    def test_inference_only_ineligible(self):
        r = research([finding("Maybe relevant", kind="inference",
                              conf="low")])
        o = MockAnalysisModel().analyze(r, policy())
        self.assertFalse(o.eligible)
        self.assertIn("no-source-facts", o.meta["reason"])

    def test_empty_research_ineligible(self):
        r = research([])
        o = MockAnalysisModel().analyze(r, policy())
        self.assertFalse(o.eligible)
        self.assertIn("no-findings", o.meta["reason"])

    def test_text_only_default(self):
        r = research([finding("Calm water")])
        o = MockAnalysisModel().analyze(r, policy())
        self.assertEqual(o.media_intent, "none")
        self.assertEqual(o.content_format, "post")

    def test_image_via_material(self):
        r = research([finding("Calm water")])
        o = MockAnalysisModel().analyze(
            r, policy(), items=[media_item()])
        self.assertEqual(o.media_intent, "image")

    def test_image_via_min_items(self):
        r = research([finding("Calm water")])
        o = MockAnalysisModel().analyze(
            r, policy(media={"min_items": 1}))
        self.assertEqual(o.media_intent, "image")

    def test_video_via_explicit_prefer(self):
        r = research([finding("Calm water")])
        o = MockAnalysisModel().analyze(
            r, policy(media={"prefer": "video"}))
        self.assertEqual(o.media_intent, "video")

    def test_no_heuristic_video(self):
        # Without explicit prefer, video is never invented.
        r = research([finding("Calm water")])
        o = MockAnalysisModel().analyze(
            r, policy(), items=[media_item()])
        self.assertNotEqual(o.media_intent, "video")

    def test_provider_poison_ignored(self):
        poison = policy()
        poison.update({"maxLength": 63206, "post_types": ["post"],
                       "provider": {"x": 1}, "post_type": "story",
                       "capabilities": {"all": True}})
        r = research([finding("Calm water")])
        clean = MockAnalysisModel().analyze(r, policy())
        dirty = MockAnalysisModel().analyze(r, poison)
        self.assertEqual(clean, dirty)
        blob = json.dumps(dirty.__dict__, default=str)
        self.assertNotIn("63206", blob)
        self.assertNotIn("post_types", blob)

    def test_deterministic(self):
        r = research([finding("A"), finding("B", ids=["g-2"])])
        m = MockAnalysisModel()
        self.assertEqual(m.analyze(r, policy()),
                         m.analyze(r, policy()))

    def test_malformed_inputs(self):
        m = MockAnalysisModel()
        with self.assertRaises(AnalysisError):
            m.analyze(None, policy())
        with self.assertRaises(AnalysisError):
            m.analyze(research([finding("A")]), None)
        with self.assertRaises(AnalysisError):
            m.analyze(research([finding("A")]),
                       {"brand": "not-a-dict"})
        with self.assertRaises(AnalysisError):
            m.analyze(research([finding("A")]), {"pillars": "music"})
        bad = research([finding("A")])
        bad.findings = ["not-a-finding"]
        with self.assertRaises(AnalysisError):
            m.analyze(bad, policy())


class OpenAIRoleCase(unittest.TestCase):
    def setUp(self):
        self._orig = urllib.request.urlopen

    def tearDown(self):
        urllib.request.urlopen = self._orig

    def _fake(self, payload=None, exc=None):
        def _open(req, timeout=None):
            if exc is not None:
                raise exc
            return FakeResp(payload)
        urllib.request.urlopen = _open

    def _result(self):
        return research([finding("Boats gather at dawn")])

    def test_needs_key(self):
        with self.assertRaises(AnalysisError):
            OpenAIAnalysisModel("")

    def test_valid_output(self):
        self._fake(_openai_body())
        o = OpenAIAnalysisModel("k").analyze(self._result(), policy())
        self.assertTrue(o.eligible)
        self.assertEqual(o.topic, "Harbor festival")
        self.assertEqual(o.facts, ["Boats gather at dawn"])
        self.assertEqual(o.source_item_ids, ["g-1"])
        self.assertEqual(o.model, "gpt-4.1")

    def test_unknown_id_rejected(self):
        self._fake(_openai_body(source_item_ids=["ghost-9"]))
        with self.assertRaises(AnalysisError):
            OpenAIAnalysisModel("k").analyze(self._result(), policy())

    def test_eligible_without_facts_rejected(self):
        self._fake(_openai_body(facts=[]))
        with self.assertRaises(AnalysisError):
            OpenAIAnalysisModel("k").analyze(self._result(), policy())

    def test_bad_enum_rejected(self):
        self._fake(_openai_body(media_intent="hologram"))
        with self.assertRaises(AnalysisError):
            OpenAIAnalysisModel("k").analyze(self._result(), policy())

    def test_prose_rejected(self):
        self._fake(json.dumps({"choices": [{"message": {
            "content": "Looks promising, go ahead."}}]}))
        with self.assertRaises(AnalysisError):
            OpenAIAnalysisModel("k").analyze(self._result(), policy())

    def test_timeout(self):
        self._fake(exc=TimeoutError("slow"))
        with self.assertRaises(AnalysisError) as ctx:
            OpenAIAnalysisModel("k").analyze(self._result(), policy())
        self.assertIn("model-timeout", str(ctx.exception))

    def test_socket_timeout(self):
        self._fake(exc=socket.timeout("slow"))
        with self.assertRaises(AnalysisError) as ctx:
            OpenAIAnalysisModel("k").analyze(self._result(), policy())
        self.assertIn("model-timeout", str(ctx.exception))

    def test_provider_failure(self):
        self._fake(exc=ConnectionError("down"))
        with self.assertRaises(AnalysisError):
            OpenAIAnalysisModel("k").analyze(self._result(), policy())

    def test_empty_research_raises(self):
        with self.assertRaises(AnalysisError):
            OpenAIAnalysisModel("k").analyze(research([]), policy())


class BoundaryCase(unittest.TestCase):
    def _code(self, rel):
        import pathlib
        src = (pathlib.Path(__file__).parent.parent / "src" / rel
               ).read_text(encoding="utf-8")
        out, in_doc = [], False
        for ln in src.splitlines():
            s = ln.strip()
            if s.startswith('"""'):
                if s.count('"""') == 2:
                    continue
                in_doc = not in_doc
                continue
            if in_doc or s.startswith("#"):
                continue
            out.append(ln)
        return "\n".join(out)

    def test_no_execution_duties(self):
        code = self._code("research/analysis.py")
        for bad in ("get_integration", "create_post", "upload",
                    "saveFile", "sqlite", "postgres", "publish_now",
                    ".publish(", "schedule_post", "generate_image",
                    "generate_video", "StateStore", "monitor",
                    "fetch_feed", "urlopen(req", "chat/completions",
                    "api.openai.com"):
            self.assertNotIn(bad, code, bad)

    def test_transport_delegated_to_adapter(self):
        # Phase 1: role modules own prompts+validation only; the
        # single HTTP call lives in llm/adapters.py.
        import pathlib
        blob = (pathlib.Path(__file__).parent.parent / "src" /
                "llm" / "adapters.py").read_text(encoding="utf-8")
        self.assertEqual(blob.count("urlopen(req"), 1)

    def test_no_secrets(self):
        code = self._code("research/analysis.py")
        for bad in ("oauth", "refresh_token", "client_secret",
                    "TUZZINA", "cookie", "password", "sk-"):
            self.assertNotIn(bad, code, bad)

    def test_no_registries(self):
        code = self._code("research/analysis.py")
        for bad in ("registry", "Registry", "REGISTRY"):
            self.assertNotIn(bad, code, bad)

    def test_roles_separate_files(self):
        import pathlib
        base = pathlib.Path(__file__).parent.parent / "src" / "research"
        self.assertTrue((base / "models.py").is_file())
        self.assertTrue((base / "analysis.py").is_file())
        self.assertNotEqual(
            (base / "models.py").read_text(encoding="utf-8"),
            (base / "analysis.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
