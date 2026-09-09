"""R3 tests: structured Research Model. Mock is deterministic;
OpenAI role is exercised through faked transport (no network, no
key). No fetching, no state, no Tuzzina anywhere in this file."""
import json
import os
import socket
import sys
import unittest
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from contracts import SourceItem
from research.models import (FACT, HIGH, INFERENCE, LOW, MEDIUM,
                             MockResearchModel, OpenAIResearchModel,
                             ResearchError, ResearchResult)


def rss_item(i, title="Harbor dawn report", text=None):
    return SourceItem(
        source_id=f"g-{i}", source_type="rss",
        source_url=f"http://feed.test/{i}", title=title,
        text=text if text is not None else
        f"Quiet water and slow boats drifting near the harbor {i}.",
        published_at="2026-09-08T09:00:00+00:00", platform="rss",
        item_id=f"g-{i}", content_hash=f"h{i}")


def yt_item(vid, title="New harbor video"):
    return SourceItem(
        source_id=vid, source_type="youtube",
        source_url=f"https://www.youtube.com/watch?v={vid}",
        title=title, text=f"Video description about the harbor {vid}.",
        published_at="2026-09-08T11:00:00+00:00", platform="youtube",
        item_id=vid, content_hash=f"y{vid}")


class FakeResp:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return self.payload.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _valid_openai_body():
    inner = {
        "summary": "Two harbor reports, calm water theme.",
        "findings": [
            {"statement": "Water is quiet",
             "kind": "fact", "confidence": "high",
             "item_ids": ["g-1"], "excerpt": "Quiet water"},
            {"statement": "Both cover the harbor",
             "kind": "inference", "confidence": "low",
             "item_ids": ["g-1", "g-2"], "excerpt": ""}],
        "topics": ["harbor"],
        "entities": []}
    outer = {"choices": [{"message": {"content": json.dumps(inner)}}]}
    return json.dumps(outer)


class MockCase(unittest.TestCase):
    def test_one_rss_item(self):
        r = MockResearchModel().research([rss_item(1)])
        self.assertIsInstance(r, ResearchResult)
        self.assertEqual(r.item_ids, ["g-1"])
        self.assertEqual(len(r.findings), 2)  # 1 fact + 1 inference
        self.assertEqual(r.findings[0].kind, FACT)
        self.assertEqual(r.findings[0].item_ids, ["g-1"])
        self.assertEqual(r.model, "mock")

    def test_multiple_rss_items(self):
        r = MockResearchModel().research([rss_item(1), rss_item(2),
                                          rss_item(3)])
        self.assertEqual(r.item_ids, ["g-1", "g-2", "g-3"])
        facts = [f for f in r.findings if f.kind == FACT]
        self.assertEqual(len(facts), 3)
        self.assertEqual(r.meta["inputs"], 3)
        self.assertFalse(r.meta["truncated"])

    def test_one_youtube_item(self):
        r = MockResearchModel().research([yt_item("AAA111")])
        self.assertEqual(r.item_ids, ["AAA111"])
        self.assertEqual(r.earliest_published,
                         "2026-09-08T11:00:00+00:00")

    def test_mixed_sources(self):
        r = MockResearchModel().research([rss_item(1), yt_item("V9")])
        kinds = {f.item_ids[0] for f in r.findings if f.kind == FACT}
        self.assertEqual(kinds, {"g-1", "V9"})
        self.assertIn("g-1", r.findings[-1].item_ids)
        self.assertIn("V9", r.findings[-1].item_ids)

    def test_cross_source_inference(self):
        items = [rss_item(1, title="Harbor festival opens",
                          text="The harbor festival opens with boats."),
                 yt_item("V9", title="Harbor festival video")]
        r = MockResearchModel().research(items)
        inf = [f for f in r.findings if f.kind == INFERENCE]
        self.assertEqual(len(inf), 1)
        self.assertEqual(inf[0].confidence, LOW)
        self.assertIn("harbor", r.topics)

    def test_duplicate_references(self):
        a = rss_item(1)
        r = MockResearchModel().research([a, a])
        self.assertEqual(r.item_ids, ["g-1", "g-1"])
        for f in r.findings:
            self.assertTrue(set(f.item_ids) <= {"g-1"})

    def test_traceability(self):
        items = [rss_item(1), rss_item(2), yt_item("V9")]
        r = MockResearchModel().research(items)
        known = {"g-1", "g-2", "V9"}
        for f in r.findings:
            self.assertTrue(f.item_ids)
            self.assertTrue(set(f.item_ids) <= known)
        covered = {i for f in r.findings for i in f.item_ids}
        self.assertEqual(covered, known)

    def test_deterministic(self):
        items = [rss_item(1), yt_item("V9")]
        self.assertEqual(MockResearchModel().research(items),
                         MockResearchModel().research(items))

    def test_empty_input_raises(self):
        with self.assertRaises(ResearchError):
            MockResearchModel().research([])

    def test_malformed_item_raises(self):
        with self.assertRaises(ResearchError):
            MockResearchModel().research([{"title": "dict, not item"}])
        with self.assertRaises(ResearchError):
            MockResearchModel().research(["just a string"])


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

    def test_needs_key(self):
        with self.assertRaises(ResearchError):
            OpenAIResearchModel("")

    def test_valid_output_parsed(self):
        self._fake(_valid_openai_body())
        r = OpenAIResearchModel("k").research([rss_item(1), rss_item(2)])
        self.assertIn("harbor", r.summary.lower())
        self.assertEqual(len(r.findings), 2)
        self.assertEqual(r.findings[0].confidence, HIGH)
        self.assertEqual(r.topics, ["harbor"])
        self.assertEqual(r.model, "gpt-4.1")

    def test_prose_output_rejected(self):
        self._fake(json.dumps({"choices": [{"message": {
            "content": "Just some prose, no JSON."}}]}))
        with self.assertRaises(ResearchError):
            OpenAIResearchModel("k").research([rss_item(1)])

    def test_unknown_id_rejected(self):
        body = json.loads(_valid_openai_body())
        content = json.loads(body["choices"][0]["message"]["content"])
        content["findings"][0]["item_ids"] = ["ghost-9"]
        body["choices"][0]["message"]["content"] = json.dumps(content)
        self._fake(json.dumps(body))
        with self.assertRaises(ResearchError):
            OpenAIResearchModel("k").research([rss_item(1)])

    def test_bad_enum_rejected(self):
        body = json.loads(_valid_openai_body())
        content = json.loads(body["choices"][0]["message"]["content"])
        content["findings"][0]["confidence"] = "certainly"
        body["choices"][0]["message"]["content"] = json.dumps(content)
        self._fake(json.dumps(body))
        with self.assertRaises(ResearchError):
            OpenAIResearchModel("k").research([rss_item(1)])

    def test_timeout(self):
        self._fake(exc=TimeoutError("slow"))
        with self.assertRaises(ResearchError) as ctx:
            OpenAIResearchModel("k").research([rss_item(1)])
        self.assertIn("model-timeout", str(ctx.exception))

    def test_provider_failure(self):
        self._fake(exc=ConnectionError("down"))
        with self.assertRaises(ResearchError):
            OpenAIResearchModel("k").research([rss_item(1)])

    def test_socket_timeout_maps(self):
        self._fake(exc=socket.timeout("slow"))
        with self.assertRaises(ResearchError) as ctx:
            OpenAIResearchModel("k").research([rss_item(1)])
        self.assertIn("model-timeout", str(ctx.exception))


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

    def test_no_fetch_no_state_no_tuzzina(self):
        # "published_at" is a SourceItem contract field (allowed);
        # guard actual publishing/scheduling duties instead.
        code = self._code("research/models.py")
        for bad in ("monitor", "StateStore", "get_integration",
                    "create_post", "upload", "saveFile", "sqlite",
                    "postgres", "publish_now", ".publish(",
                    "schedule_post", "generate_image",
                    "generate_video"):
            self.assertNotIn(bad, code, bad)

    def test_transport_delegated_to_adapter(self):
        # Phase 1: role modules own prompts+validation only; the
        # single HTTP call lives in llm/adapters.py.
        code = self._code("research/models.py")
        self.assertEqual(code.count("urlopen(req"), 0)
        self.assertNotIn("chat/completions", code)
        self.assertNotIn("api.openai.com", code)
        import pathlib
        blob = (pathlib.Path(__file__).parent.parent / "src" /
                "llm" / "adapters.py").read_text(encoding="utf-8")
        self.assertEqual(blob.count("urlopen(req"), 1)

    def test_no_secrets(self):
        # "Bearer" scheme word is required API syntax (key itself is
        # a constructor arg, never a literal); guard the rest.
        code = self._code("research/models.py")
        for bad in ("oauth", "refresh_token", "client_secret",
                    "TUZZINA", "cookie", "password", "sk-"):
            self.assertNotIn(bad, code, bad)

    def test_fact_inference_vocab(self):
        from research import models as M
        self.assertEqual((M.FACT, M.INFERENCE), ("fact", "inference"))
        self.assertEqual((M.HIGH, M.MEDIUM, M.LOW),
                         ("high", "medium", "low"))

    def test_medium_confidence_when_undated(self):
        it = rss_item(1)
        it.published_at = ""
        r = MockResearchModel().research([it])
        self.assertEqual(r.findings[0].confidence, MEDIUM)
        self.assertEqual(r.earliest_published, "")


if __name__ == "__main__":
    unittest.main()
