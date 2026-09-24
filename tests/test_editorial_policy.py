"""The engine supplies data; the active Skill supplies policy.

What the engine owns: load the Skill, pass its instructions and the
run data to the model, and validate the STRUCTURAL contract (types,
id membership) only. What it never owns: what counts as an
opportunity, what a fact must be, what an empty research result
means, or what repetition is.

Every case below is therefore driven by the Skill DOCUMENT, never by
a config flag read by Python: two Skills with different prose and an
unchanged engine produce different behavior.
"""
import json
import os
import sys
import tempfile
import unittest
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import yaml

from contracts import SourceItem
from research.analysis import ContentOpportunity, OpenAIAnalysisModel
from research.generation import (GenerationPlan, TextRequest,
                                 editorial_item_for, plan_generation,
                                 primary_item_for)

from research.models import Finding, ResearchResult
from skills.loader import clear_cache


def research(findings=(), summary=""):
    return ResearchResult(
        summary=summary, findings=list(findings), topics=[], entities=[],
        item_ids=sorted({i for f in findings for i in f.item_ids}),
        earliest_published="", latest_published="", model="mock",
        meta={})


def fact(stmt, ids=("g-1",)):
    return Finding(statement=stmt, kind="fact", confidence="high",
                   item_ids=list(ids), excerpt=stmt[:60])


class FakeResp:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return self.payload.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _body(**over):
    inner = {"eligible": True, "topic": "Editorial topic",
             "angle": "Editorial angle", "rationale": "Per the skill.",
             "facts": ["A truthful fact"], "source_item_ids": [],
             "content_format": "post", "media_intent": "none",
             "audience": "a", "language": "en", "priority": "normal",
             "confidence": "medium", "constraints": []}
    inner.update(over)
    return json.dumps({"choices": [{"message": {
        "content": json.dumps(inner)}}]})


class SkillPolicyCase(unittest.TestCase):
    """One Skill document per case, exactly as in production: the
    Skill is the policy, the engine is unchanged."""

    STRICT = ("Eligible ONLY with at least one source-backed fact "
              "from the findings. Never invent a fact.")
    EVERGREEN = ("Decide the post. When the research findings are "
                 "empty, still choose one evergreen topic from the "
                 "configured pillars and mark it eligible.")

    def setUp(self):
        self._orig_env = os.environ.get("BRAIN_SKILLS_DIR")
        self._orig_open = urllib.request.urlopen
        self.tmp = tempfile.mkdtemp(prefix="tbra_policy_")
        os.environ["BRAIN_SKILLS_DIR"] = self.tmp
        clear_cache()
        self.requests = []

    def tearDown(self):
        urllib.request.urlopen = self._orig_open
        clear_cache()
        if self._orig_env is None:
            os.environ.pop("BRAIN_SKILLS_DIR", None)
        else:
            os.environ["BRAIN_SKILLS_DIR"] = self._orig_env

    def _skill(self, instructions, name="a1"):
        doc = {"id": "analysis-x1", "name": name, "type": "analysis",
               "version": 1, "enabled": True, "instructions": instructions}
        with open(os.path.join(self.tmp, "analysis.%s.yaml" % name), "w",
                  encoding="utf-8") as handle:
            yaml.safe_dump(doc, handle)
        clear_cache()
        return {"skills": {"analysis": name}}

    def _answer(self, payload):
        def _open(req, timeout=None):
            self.requests.append(req.data.decode("utf-8"))
            return FakeResp(payload)
        urllib.request.urlopen = _open

    # --- structural contract (engine-owned) -------------------------
    def test_structural_contract_still_enforced(self):
        # An id outside the input is a structural violation, whatever
        # the Skill says.
        self._skill(self.STRICT)
        self._answer(_body(source_item_ids=["ghost-9"], facts=["f"]))
        with self.assertRaises(Exception):
            OpenAIAnalysisModel("k").analyze(
                research([fact("Boats gather")]), {})

    def test_engine_prompt_carries_no_editorial_policy(self):
        # The engine states structure only; the Skill states policy.
        self._skill(self.STRICT)
        self._answer(_body(source_item_ids=["g-1"]))
        OpenAIAnalysisModel("k").analyze(research([fact("Boats gather")]),
                                         self._skill(self.STRICT, "b1"))
        sent = self.requests[0]
        self.assertIn("source_item_id MUST come from the input", sent)
        self.assertNotIn("eligible=true ONLY with", sent)
        self.assertNotIn("source-backed only", sent)
        # The policy itself reached the model, verbatim.
        self.assertIn(self.STRICT, sent)

    # --- history as data (engine-owned) -----------------------------
    def test_recent_history_reaches_the_decision_as_data(self):
        self._skill(self.EVERGREEN)
        self._answer(_body())
        OpenAIAnalysisModel("k").analyze(
            research([fact("Boats gather")]),
            dict(self._skill(self.EVERGREEN, "c1"),
                 recent_topics=[{"topic": "Boats", "angle": "Dawn"}]))
        sent = self.requests[0]
        self.assertIn("Recently covered in this campaign", sent)
        self.assertIn("- Boats (angle: Dawn)", sent)
        # No repetition rule is reconstructed in Python; the Skill
        # owns what repetition means for the campaign.
        self.assertNotIn("do not repeat the same topic+angle", sent)

    # --- isolation: same engine, different Skill, different result --
    def test_two_skills_different_behavior_without_code_change(self):
        # Engine untouched; the only variable is the Skill document.
        # The transport stands in for a model that obeys the skill it
        # is given: it declines an empty-research decision when the
        # skill demands a source-backed fact, and proposes a topic
        # when the skill asks for an evergreen one.
        def _open(req, timeout=None):
            sent = req.data.decode("utf-8")
            self.requests.append(sent)
            if self.STRICT in sent:
                return FakeResp(_body(eligible=False, facts=[],
                                      rationale="no source-backed fact",
                                      topic="", angle=""))
            return FakeResp(_body(facts=["an evergreen truthful fact"]))
        urllib.request.urlopen = _open

        strict = self._skill(self.STRICT, "strict")
        evergreen = self._skill(self.EVERGREEN, "evergreen")
        # Identical engine, identical empty research input.
        self.assertFalse(
            OpenAIAnalysisModel("k").analyze(research(), strict).eligible)
        self.assertTrue(
            OpenAIAnalysisModel("k").analyze(research(), evergreen).eligible)
        # Both calls carried their own skill text, nothing else.
        self.assertEqual(len(self.requests), 2)
        self.assertIn(self.STRICT, self.requests[0])
        self.assertIn(self.EVERGREEN, self.requests[1])


class EditorialUnitCase(unittest.TestCase):
    """A decision with no source item still needs a content unit; it
    must never look like collected research material."""

    def test_editorial_unit_carries_no_research_identity(self):
        plan = GenerationPlan(text=TextRequest(title="T", summary="S"))
        item = editorial_item_for(plan)
        self.assertIsNotNone(item)
        self.assertEqual(item.title, "T")
        self.assertEqual(item.text, "S")
        self.assertEqual(item.source_type, "editorial")
        self.assertEqual(item.item_id, "")
        self.assertEqual(item.source_id, "")
        self.assertEqual(item.source_url, "")
        self.assertEqual(item.images, [])
        self.assertEqual(item.links, [])

    def test_editorial_unit_is_absent_without_a_text_request(self):
        self.assertIsNone(editorial_item_for(GenerationPlan()))
        self.assertIsNone(editorial_item_for(None))

    def test_real_item_wins_when_research_exists(self):
        item = SourceItem(source_id="s1", source_type="rss",
                          source_url="http://x.test/1", title="T", text="S",
                          item_id="g-1", platform="rss")
        self.assertIs(primary_item_for(None, [item]), item)
        self.assertIsNone(primary_item_for(None, []))

    def test_decision_without_items_still_plans_text(self):
        opportunity = ContentOpportunity(
            eligible=True, topic="Evergreen topic", angle="Angle",
            facts=["truthful fact"], source_item_ids=[],
            media_intent="none")
        plan = plan_generation(opportunity, [])
        self.assertIsNotNone(plan.text)
        self.assertEqual(plan.text.title, "Evergreen topic")
        self.assertIsNotNone(editorial_item_for(plan))


class PipelineCase(unittest.TestCase):
    """The pipeline shape: research ALWAYS hands a result to the
    next stage; provider failures keep using the existing generic
    retry path; the engine carries no editorial branch of its own."""

    def _stage_ctx(self, collected, model=None, claimer=None):
        import research.cycle as cycle
        import stage_run

        def _collect(sources, store, now, out, pending):
            out.items.extend(collected)
            for item in out.items:
                out.sources.append(
                    cycle.SourceReport("website", item.source_url, True,
                                       "", 1, 1))
        real = cycle.collect_all_sources
        cycle.collect_all_sources = _collect
        self.addCleanup(setattr, cycle, "collect_all_sources", real)
        return {
            "store": stage_run.MemoryStateStore() if hasattr(
                stage_run, "MemoryStateStore") else object(),
            "cfg": {"roles": ["research", "analysis", "text", "image"]},
            "client": _NoQueueClient(), "run_id": "r-1",
            "sources": [], "now": "t", "dry_run": True,
            "research_model": model,
        }

    def test_findings_reach_the_next_stage_as_findings(self):
        from research.models import Finding, ResearchResult
        from stage_run import run_research_stage

        class Model:
            def research(self, items, cfg):
                return ResearchResult(
                    summary="s",
                    findings=[Finding(statement="A fact", kind="fact",
                                     confidence="high",
                                     item_ids=[items[0].item_id],
                                     excerpt="A fact")],
                    topics=["t"], entities=[], item_ids=[items[0].item_id],
                    model="stub", meta={})

        item = SourceItem(source_id="s-1", source_type="website",
                          source_url="http://x.test/1", title="T",
                          text="body", item_id="g-1")
        out = run_research_stage(
            self._stage_ctx([item], model=Model()))
        self.assertEqual(out["outcome"], "findings")
        self.assertEqual(out["items_new"], 1)
        self.assertEqual(len(out["research"]["findings"]), 1)

    def test_zero_findings_reach_the_next_stage_the_same_way(self):
        from stage_run import run_research_stage
        out = run_research_stage(self._stage_ctx([]))
        # Same outcome, same payload type: the next stage always gets
        # a research result, and the absence of findings is data.
        self.assertEqual(out["outcome"], "findings")
        self.assertIsNotNone(out["research"])
        self.assertEqual(out["research"]["findings"], [])
        self.assertEqual(out["items"], [])

    def test_no_central_no_material_route_exists(self):
        # Source-level guard: no executable line of the engine branches
        # on a special empty-research outcome (prose in comments is
        # fine; code is not).
        import pathlib
        import stage_run
        code = []
        for line in pathlib.Path(stage_run.__file__).read_text(
                encoding="utf-8").splitlines():
            stripped = line.split("#", 1)[0]
            if "no_material" in stripped:
                code.append(stripped.strip())
        self.assertEqual(code, [])

    def test_provider_failure_keeps_the_existing_retry_contract(self):
        from llm.adapters import LLMError
        from research.models import ResearchError
        from stage_run import stage_error_envelope
        # A provider/contract failure is still an error, still
        # retryable, and carries the same code as before this change.
        for error in (LLMError("invalid-output"),
                      ResearchError("model-error: invalid-output")):
            stage, status, payload, message = stage_error_envelope(
                "research", error)
            self.assertEqual(stage, "research")
            self.assertEqual(status, "RETRYABLE_STAGE_FAILURE")
            self.assertIsNone(payload)
        # And it is NOT silently turned into a successful result.
        from stage_run import run_research_stage

        class Boom:
            def research(self, items, cfg):
                raise ResearchError("model-error: invalid-output")

        item = SourceItem(source_id="s-1", source_type="website",
                          source_url="http://x.test/1", title="T",
                          text="body", item_id="g-1")
        with self.assertRaises(ResearchError):
            run_research_stage(self._stage_ctx([item], model=Boom()))


class _NoQueueClient:
    """Client stub with no queue and no claims: the stage must work
    without touching shared state in a dry run."""

    def release_claim(self, key, run_id):
        pass

    def get_research_state(self, key):
        return None

    def save_research_state(self, key, value):
        return value


if __name__ == "__main__":
    unittest.main()
