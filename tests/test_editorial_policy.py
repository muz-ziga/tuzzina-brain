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
from stage_run import QUEUE_MAX_ATTEMPTS


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

    def test_the_configured_skill_survives_a_large_findings_block(self):
        # Research data is variable and may be truncated; the
        # configured Skill is this stage's authority and must never be
        # what gets cut away.
        marker = "ANALYSIS-SKILL-MARKER"
        policy = self._skill("Decide the post. " + marker)
        self._answer(_body(facts=["a fact"]))
        many = [fact("F%02d %s" % (i, "w" * 300), ids=("g-%d" % i,))
                for i in range(30)]
        OpenAIAnalysisModel("k").analyze(research(many), policy)
        sent = self.requests[0]
        self.assertIn(marker, sent)
        # The cap still holds: the findings tail is what got cut.
        self.assertLess(len(sent), 12000)

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

    def test_parked_material_is_offered_as_data_and_the_skill_decides(self):
        # The offer never becomes findings. It reaches the model as a
        # labelled DATA block, and whether it is used is the Skill's
        # call: one skill adopts it, the other keeps its evergreen rule.
        def _open(req, timeout=None):
            sent = req.data.decode("utf-8")
            self.requests.append(sent)
            marker = "ADOPT: when earlier collected material is offered"
            adopted = marker in sent
            return FakeResp(_body(
                facts=["a fact from the offered material"] if adopted
                else ["an evergreen truthful fact"]))
        urllib.request.urlopen = _open

        offered = research()
        offered.meta = {"unpublished": [
            {"statement": "Parked fact from an earlier run",
             "kind": "fact", "confidence": "high",
             "item_ids": ["g-old"], "source_url": "https://x/old",
             "title": "T", "attempts": 1, "queued_at": "2026-01-01"}]}
        adopt = self._skill(
            "Decide the post. ADOPT: when earlier collected material is "
            "offered and it still fits the pillars, base the post on it.",
            name="adopter")
        keep = self._skill(self.EVERGREEN, name="keeper")

        adopted = OpenAIAnalysisModel("k").analyze(offered, adopt)
        self.assertTrue(adopted.eligible)
        self.assertEqual(adopted.source_item_ids, [])
        sent = self.requests[0]
        self.assertIn("not this cycle's research", sent)
        self.assertIn("Parked fact from an earlier run", sent)
        # The engine added no findings: the result of THIS cycle stays
        # empty and the offer is only context.
        self.assertEqual(len(offered.findings), 0)

        self.assertTrue(
            OpenAIAnalysisModel("k").analyze(research(), keep).eligible)


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


class _QueueClient:
    """Client stub with a real research-state row, so the parked
    queue persists exactly like production."""

    def __init__(self):
        self.state = {}
        self.released = []

    def release_claim(self, key, run_id):
        self.released.append((key, run_id))

    def get_research_state(self, key):
        return self.state.get(key)

    def save_research_state(self, key, value):
        self.state[key] = value
        return value


class ParkedMaterialAdoptionCase(unittest.TestCase):
    """Parked material is DATA a Skill may adopt. The engine never
    adopts it, offers it without spending an attempt, and records an
    attempt only for an adoption the Skill actually made.

    Every case drives the production path: the analysis stage parks an
    ineligible verdict itself, the offer comes from the offer helper,
    and the adoption is the Skill citing the entry's own ids through
    the real model + validation.
    """

    ITEM = {"source_id": "s-1", "source_type": "website",
            "source_url": "https://x.test/old", "title": "Old title",
            "text": "old body", "item_id": "g-old"}
    RESEARCH = {"summary": "s", "topics": ["t"], "entities": [],
                "findings": [{"statement": "Parked fact from a run that "
                                            "never posted", "kind": "fact",
                              "confidence": "high", "item_ids": ["g-old"],
                              "excerpt": "Parked fact"}]}

    def setUp(self):
        self._orig_env = os.environ.get("BRAIN_SKILLS_DIR")
        self._orig_open = urllib.request.urlopen
        self.tmp = tempfile.mkdtemp(prefix="tbra_parked_")
        os.environ["BRAIN_SKILLS_DIR"] = self.tmp
        clear_cache()
        self.requests = []
        self.policy = self._skill("Neutral test Skill.")

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

    def _ctx(self, client, research_raw, model, dry_run=False):
        cfg = {"roles": ["research", "analysis", "text", "image"]}
        cfg.update(self.policy)
        return {"client": client, "run_id": "r-1", "claimed": [],
                "cfg": cfg,
                "research": research_raw, "items": [self.ITEM],
                "analysis_model": model, "now": "2026-01-01",
                "dry_run": dry_run}

    def _parked_entry(self, client):
        """One real cycle whose verdict did not post: the analysis
        stage parks the material through the production path."""
        from stage_run import run_analysis_stage

        class Ineligible:
            def analyze(self, result, policy, items=None):
                return ContentOpportunity(
                    eligible=False, topic="t", angle="", rationale="no",
                    facts=[], source_item_ids=[], content_format="post",
                    media_intent="none", audience="a", language="en",
                    priority="normal", confidence="low", constraints=[],
                    model="stub", meta={})

        client = client or _QueueClient()
        run_analysis_stage(self._ctx(client, self.RESEARCH, Ineligible()))
        return client

    def _entries(self, client):
        from stage_run import QUEUE_SOURCE
        row = client.get_research_state(QUEUE_SOURCE) or {}
        return list((row.get("candidates") or {}).values())

    def _offer_for_next_cycle(self, client, items=None):
        """Exactly what the research stage attaches: the offer helper
        plus the research payload field the analysis stage reads."""
        import stage_run
        offer = stage_run._unpublished_offer(client)
        research = {"summary": "", "topics": [], "entities": [],
                    "findings": []}
        if offer:
            research["meta"] = {"unpublished": offer}
        return research, offer

    def test_offering_does_not_spend_an_attempt(self):
        from stage_run import _unpublished_offer
        client = self._parked_entry(None)
        for _ in range(QUEUE_MAX_ATTEMPTS + 2):
            offer = _unpublished_offer(client)
            self.assertEqual(len(offer), 1)
            self.assertEqual(offer[0]["attempts"], 0)
        # Never written back: observing the offer is not using it.
        self.assertEqual(self._entries(client)[0]["attempts"], 0)

    def test_ignoring_the_offer_leaves_it_parked(self):
        from stage_run import run_analysis_stage
        self.policy = self._skill(
            "Use only this cycle's research. Never use earlier "
            "collected material.")
        self._answer(_body(facts=["a fact from this cycle"]))
        client = self._parked_entry(None)
        research, _offer = self._offer_for_next_cycle(client)
        out = run_analysis_stage(self._ctx(
            client, research, OpenAIAnalysisModel("k")))
        self.assertEqual(out["outcome"], "eligible")
        self.assertEqual(len(self._entries(client)), 1)
        self.assertEqual(self._entries(client)[0]["attempts"], 0)

    def test_skill_adoption_is_the_only_thing_that_spends_an_attempt(self):
        # The Skill cites the parked entry's own ids: that citation IS
        # the adoption, and it is what the counter records.
        from stage_run import run_analysis_stage
        self.policy = self._skill(
            "Adopt earlier collected material when it fits.")
        self._answer(_body(facts=["the parked fact is still true"],
                           source_item_ids=["g-old"]))
        client = self._parked_entry(None)
        research, offer = self._offer_for_next_cycle(client)
        # The ids are shown to the model, so it can cite them.
        sent_before = len(self.requests)
        run_analysis_stage(self._ctx(
            client, research, OpenAIAnalysisModel("k")))
        self.assertIn("g-old", self.requests[sent_before])
        entry = self._entries(client)[0]
        self.assertEqual(entry["attempts"], 1)
        self.assertEqual(entry["item_ids"], ["g-old"])
        self.assertEqual(entry["source_url"], "https://x.test/old")
        self.assertEqual(entry["statement"],
                         "Parked fact from a run that never posted")
        self.assertEqual(offer[0]["item_ids"], ["g-old"])

    def test_adopted_ids_survive_into_the_verdict_and_the_post_plan(self):
        # Citation resolution: the verdict keeps the parked ids, and
        # generation is built from that verdict (no second opinion).
        from stage_run import run_analysis_stage
        self.policy = self._skill(
            "Adopt earlier collected material when it fits.")
        self._answer(_body(facts=["the parked fact is still true"],
                           source_item_ids=["g-old"], media_intent="none"))
        client = self._parked_entry(None)
        research, _offer = self._offer_for_next_cycle(client)
        out = run_analysis_stage(self._ctx(
            client, research, OpenAIAnalysisModel("k")))
        opportunity = out["opportunity"]
        self.assertEqual(opportunity["source_item_ids"], ["g-old"])
        self.assertEqual(self._entries(client)[0]["attempts"], 1)
        plan = plan_generation(_opp(opportunity), [], policy={})
        self.assertTrue(plan.text.summary.strip())

    def test_published_adoption_consumes_the_entry(self):
        from stage_run import _queue_consume, run_analysis_stage
        self.policy = self._skill(
            "Adopt earlier collected material when it fits.")
        self._answer(_body(facts=["the parked fact is still true"],
                           source_item_ids=["g-old"]))
        client = self._parked_entry(None)
        research, _offer = self._offer_for_next_cycle(client)
        out = run_analysis_stage(self._ctx(
            client, research, OpenAIAnalysisModel("k")))
        self.assertEqual(len(self._entries(client)), 1)
        # Existing consume semantics: published ids mean gone.
        _queue_consume(client, out["opportunity"]["source_item_ids"])
        self.assertEqual(self._entries(client), [])

    def test_an_id_the_prompt_never_showed_is_still_rejected(self):
        # Adoption widens the referenceable set to what the prompt
        # actually displayed; it does not open the membership check.
        self.policy = self._skill(
            "Adopt earlier collected material when it fits.")
        self._answer(_body(facts=["f"], source_item_ids=["ghost-9"]))
        client = self._parked_entry(None)
        research, _offer = self._offer_for_next_cycle(client)
        with self.assertRaises(Exception):
            run_analysis_stage(self._ctx(
                client, research, OpenAIAnalysisModel("k")))
        self.assertEqual(self._entries(client)[0]["attempts"], 0)

    def test_dry_run_neither_adopts_nor_spends(self):
        from stage_run import run_analysis_stage
        self.policy = self._skill(
            "Adopt earlier collected material when it fits.")
        self._answer(_body(facts=["the parked fact is still true"],
                           source_item_ids=["g-old"]))
        client = self._parked_entry(None)
        research, _offer = self._offer_for_next_cycle(client)
        out = run_analysis_stage(self._ctx(
            client, research, OpenAIAnalysisModel("k"), dry_run=True))
        self.assertEqual(out["opportunity"]["source_item_ids"], ["g-old"])
        self.assertEqual(self._entries(client)[0]["attempts"], 0)

    def test_an_exhausted_entry_is_no_longer_offered(self):
        from stage_run import _unpublished_offer, run_analysis_stage
        self.policy = self._skill(
            "Adopt earlier collected material when it fits.")
        client = self._parked_entry(None)
        for _ in range(QUEUE_MAX_ATTEMPTS):
            self._answer(_body(facts=["the parked fact is still true"],
                               source_item_ids=["g-old"]))
            research, _offer = self._offer_for_next_cycle(client)
            run_analysis_stage(self._ctx(
                client, research, OpenAIAnalysisModel("k")))
        self.assertEqual(self._entries(client), [])
        self.assertEqual(_unpublished_offer(client), [])


def _opp(raw):
    from research.analysis import ContentOpportunity
    return ContentOpportunity(**raw)


if __name__ == "__main__":
    unittest.main()
