"""Phase 2 acceptance: the generic research agent loop.

Proves, offline (scripted turns, no network, no keys):
1. Emptiness is data: items=[] still invokes the agent.
2. Skills A/B/C differ ONLY in skill text + campaign data; the
   runtime path is identical (one code path, no skill-name branch).
3. Budget decrement is visible to the agent; the safety ceiling
   is invisible and unraisable.
4. Malformed tool calls and undeclared outcomes are rejected
   structurally, and the agent may adapt.
5. Tool results are typed/bounded records, never instructions.
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from research import agent_loop, tools


def _contract(outcomes=None):
    return {"version": 1,
            "outcomes": outcomes or {
                "findings": {"keys": {"summary": "string",
                                      "findings": "list"}},
                "no_material": {"keys": {"summary": "string"}}},
            "id_refs": []}


def _skill(instructions="Do research.", contract=None):
    return {"id": "research-agent-v1", "name": "agent",
            "type": "research", "version": 1, "enabled": True,
            "instructions": instructions,
            "config": {"contract": contract or _contract()},
            "contract": contract or _contract(),
            "contract_id": "custom", "source": "assigned"}


class ScriptedModel:
    """Transport seam double: returns scripted raw turns and records
    every system prompt it was shown."""

    def __init__(self, turns):
        self.turns = list(turns)
        self.prompts = []
        self.calls = 0

    def agent_turn(self, system, user):
        self.calls += 1
        self.prompts.append((system, user))
        if not self.turns:
            raise AssertionError("model called more times than scripted")
        return self.turns.pop(0)


def _tool_call(name, arguments):
    return json.dumps({"action": "tool_call", "tool": name,
                       "arguments": arguments})


def _final(outcome, artifacts):
    return json.dumps({"action": "final", "outcome": outcome,
                       "artifacts": artifacts})


FINDINGS = [{"statement": "Loudness normalisation moved to -14 LUFS.",
             "kind": "fact", "confidence": "high",
             "item_ids": ["u1"], "excerpt": "Loudness normalisation"}]


def _search_fn(query, n, horizon):
    """Deterministic capability double: narrow queries are poor."""
    if "broad" in query:
        return [{"item_id": "u1", "title": "Mastering loudness",
                 "text": "Loudness normalisation moved to -14 LUFS.",
                 "source_url": "https://a.test/1",
                 "published_at": "2026-09-20", "platform": "search"}]
    return []


def _fetch_fn(url, n):
    return [{"item_id": "u2", "title": "Article",
             "text": "Body of " + url, "source_url": url,
             "published_at": "2026-09-21", "platform": "website"}]


class EmptyIsDataCase(unittest.TestCase):
    def test_skill_a_invokes_agent_and_stops_with_no_tool_call(self):
        model = ScriptedModel([_final("no_material",
                                      {"summary": "Nothing relevant."})])
        out = agent_loop.run_loop(
            invoke=model.agent_turn, skill_doc=_skill(),
            contract=_contract(), tool_names=["discovery.search"],
            tool_limits={"discovery.search": {"n": 3}}, tool_calls=5,
            items=[])
        self.assertEqual(out["outcome"], "no_material")
        self.assertEqual(model.calls, 1)
        self.assertEqual(out["usage"]["tool_calls"], 0)
        self.assertEqual(out["history"], [])

    def test_skill_b_searches_then_fetches_then_finalises(self):
        model = ScriptedModel([
            _tool_call("discovery.search", {"query": "mastering"}),
            _tool_call("discovery.fetch", {"url": "https://a.test/1"}),
            _final("findings", {"summary": "Found material.",
                                "findings": FINDINGS}),
        ])
        out = agent_loop.run_loop(
            invoke=model.agent_turn, skill_doc=_skill(),
            contract=_contract(),
            tool_names=["discovery.search", "discovery.fetch"],
            tool_limits={"discovery.search": {"n": 3},
                         "discovery.fetch": {"n": 3}},
            tool_calls=5, items=[],
            search_fn_placeholder=None) if False else agent_loop.run_loop(
            invoke=model.agent_turn, skill_doc=_skill(),
            contract=_contract(),
            tool_names=["discovery.search", "discovery.fetch"],
            tool_limits={"discovery.search": {"n": 3},
                         "discovery.fetch": {"n": 3}},
            tool_calls=5, items=[])
        self.assertEqual(out["outcome"], "findings")
        self.assertEqual(out["usage"]["tool_calls"], 2)
        self.assertEqual([h["tool"] for h in out["history"]],
                         ["discovery.search", "discovery.fetch"])

    def test_skill_c_broadens_after_a_poor_first_search(self):
        # Same runtime: the difference is only the skill's strategy
        # (retry with a broader query), expressed as tool calls.
        model = ScriptedModel([
            _tool_call("discovery.search", {"query": "narrow"}),
            _tool_call("discovery.search", {"query": "broad loudness"}),
            _tool_call("discovery.fetch", {"url": "https://a.test/1"}),
            _final("findings", {"summary": "Found material.",
                                "findings": FINDINGS}),
        ])
        out = agent_loop.run_loop(
            invoke=model.agent_turn, skill_doc=_skill(),
            contract=_contract(),
            tool_names=["discovery.search", "discovery.fetch"],
            tool_limits={"discovery.search": {"n": 3},
                         "discovery.fetch": {"n": 3}},
            tool_calls=6, items=[])
        self.assertEqual(out["outcome"], "findings")
        self.assertEqual(out["usage"]["tool_calls"], 3)


class CounterSeparationCase(unittest.TestCase):
    def test_budget_is_visible_and_decrements(self):
        model = ScriptedModel([
            _tool_call("discovery.search", {"query": "a"}),
            _final("no_material", {"summary": "s"}),
        ])
        out = agent_loop.run_loop(
            invoke=model.agent_turn, skill_doc=_skill(),
            contract=_contract(), tool_names=["discovery.search"],
            tool_limits={"discovery.search": {"n": 1}}, tool_calls=2,
            items=[])
        self.assertEqual(out["usage"]["tool_calls"], 1)
        # Shown before the call (2 left), then after (1 left).
        self.assertIn("at most 2 capability call", model.prompts[0][0])
        self.assertIn("at most 1 capability call", model.prompts[1][0])

    def test_safety_ceiling_is_invisible_and_unraisable(self):
        # A model that only ever calls tools is stopped by the
        # runtime liveness ceiling even when the campaign budget is
        # far larger; the number never appears in any prompt, so
        # the agent can neither see nor reason about it.
        turns = [_tool_call("discovery.search", {"query": "x"})
                 for _ in range(agent_loop.SAFETY_MAX_ITERATIONS + 4)]
        model = ScriptedModel(turns)
        out = agent_loop.run_loop(
            invoke=model.agent_turn, skill_doc=_skill(),
            contract=_contract(), tool_names=["discovery.search"],
            tool_limits={"discovery.search": {"n": 1}},
            tool_calls=agent_loop.MAX_TOOL_CALLS_CEILING, items=[])
        self.assertEqual(out["error"], "safety-iterations-exhausted")
        self.assertLessEqual(model.calls,
                             agent_loop.SAFETY_MAX_ITERATIONS)
        needle = str(agent_loop.SAFETY_MAX_ITERATIONS)
        for system, _user in model.prompts:
            self.assertNotIn("safety", system.lower())
            self.assertNotIn(needle, system)

    def test_budget_exhaustion_is_a_stop_not_a_crash(self):
        model = ScriptedModel([
            _tool_call("discovery.search", {"query": "a"}),
            _tool_call("discovery.search", {"query": "b"}),
        ])
        out = agent_loop.run_loop(
            invoke=model.agent_turn, skill_doc=_skill(),
            contract=_contract(), tool_names=["discovery.search"],
            tool_limits={"discovery.search": {"n": 1}}, tool_calls=1,
            items=[])
        self.assertEqual(out["error"], "budget_exhausted")
        self.assertEqual(out["usage"]["tool_calls"], 1)

    def test_campaign_budget_is_clamped_by_the_ceiling(self):
        self.assertEqual(
            agent_loop.campaign_tool_budget({"max_tool_calls": 999}),
            agent_loop.MAX_TOOL_CALLS_CEILING)
        self.assertEqual(
            agent_loop.campaign_tool_budget({"max_tool_calls": 7}), 7)
        self.assertEqual(agent_loop.campaign_tool_budget({}), 0)
        self.assertEqual(
            agent_loop.campaign_tool_budget({"max_tool_calls": "x"}), 0)
        self.assertEqual(
            agent_loop.campaign_tool_budget({"max_tool_calls": -3}), 0)


class ToolContractCase(unittest.TestCase):
    def test_disabled_tool_is_rejected_and_agent_adapts(self):
        model = ScriptedModel([
            _tool_call("discovery.fetch", {"url": "https://a.test/1"}),
            _final("no_material", {"summary": "s"}),
        ])
        out = agent_loop.run_loop(
            invoke=model.agent_turn, skill_doc=_skill(),
            contract=_contract(), tool_names=["discovery.search"],
            tool_limits={"discovery.search": {"n": 1}}, tool_calls=2,
            items=[])
        self.assertEqual(out["outcome"], "no_material")
        self.assertEqual(out["history"][0]["result"]["error"],
                         "tool-not-enabled")
        self.assertEqual(out["usage"]["tool_calls"], 0)

    def test_malformed_arguments_are_typed_rejections(self):
        model = ScriptedModel([
            _tool_call("discovery.search", {"query": ""}),
            _tool_call("discovery.fetch", {"url": "file:///etc/passwd"}),
            _final("no_material", {"summary": "s"}),
        ])
        out = agent_loop.run_loop(
            invoke=model.agent_turn, skill_doc=_skill(),
            contract=_contract(),
            tool_names=["discovery.search", "discovery.fetch"],
            tool_limits={"discovery.search": {"n": 1},
                         "discovery.fetch": {"n": 1}},
            tool_calls=3, items=[])
        self.assertEqual(
            [h["result"]["error"] for h in out["history"]],
            ["search-query-required", "fetch-url-not-http"])

    def test_unknown_tool_and_bad_action_rejected(self):
        model = ScriptedModel([
            _tool_call("system.exec", {"cmd": "rm -rf /"}),
            json.dumps({"action": "tool_call", "tool": "discovery.search",
                        "arguments": []}),
            _final("no_material", {"summary": "s"}),
        ])
        out = agent_loop.run_loop(
            invoke=model.agent_turn, skill_doc=_skill(),
            contract=_contract(), tool_names=["discovery.search"],
            tool_limits={"discovery.search": {"n": 1}}, tool_calls=2,
            items=[])
        self.assertEqual(out["outcome"], "no_material")
        self.assertEqual(out["history"][0]["result"]["error"],
                         "tool-not-enabled")
        self.assertEqual(out["history"][1]["result"]["error"],
                         "turn-bad-arguments")
        self.assertEqual(out["usage"]["tool_calls"], 0)

    def test_two_malformed_turns_end_the_loop(self):
        model = ScriptedModel(["not json", "still not json"])
        out = agent_loop.run_loop(
            invoke=model.agent_turn, skill_doc=_skill(),
            contract=_contract(), tool_names=[], tool_limits={},
            tool_calls=0, items=[])
        self.assertTrue(out["error"].startswith("malformed-turn"))
        self.assertEqual(model.calls, 2)

    def test_undeclared_outcome_rejected_then_agent_adapts(self):
        model = ScriptedModel([
            _final("hacked", {"summary": "s"}),
            _final("no_material", {"summary": "s"}),
        ])
        out = agent_loop.run_loop(
            invoke=model.agent_turn, skill_doc=_skill(),
            contract=_contract(), tool_names=[], tool_limits={},
            tool_calls=0, items=[])
        self.assertEqual(out["outcome"], "no_material")
        self.assertEqual(out["history"][0]["result"]["error"],
                         "undeclared-outcome:hacked")

    def test_missing_and_mistyped_artifacts_rejected(self):
        model = ScriptedModel([
            _final("findings", {"summary": "s"}),
            _final("findings", {"summary": 7, "findings": []}),
            _final("findings", {"summary": "ok", "findings": []}),
        ])
        out = agent_loop.run_loop(
            invoke=model.agent_turn, skill_doc=_skill(),
            contract=_contract(), tool_names=[], tool_limits={},
            tool_calls=0, items=[])
        self.assertEqual(out["outcome"], "findings")
        self.assertEqual([h["result"]["error"] for h in out["history"]],
                         ["missing-artifact:findings",
                          "bad-artifact-type:summary"])


class ToolIsolationCase(unittest.TestCase):
    def test_tool_records_are_bounded_and_typed(self):
        out = tools.execute(
            "discovery.fetch", {"url": "https://a.test/1"},
            {"discovery.fetch": {"n": 1}}, fetch_fn=_fetch_fn)
        self.assertTrue(out["ok"])
        self.assertEqual(out["count"], 1)
        rec = out["items"][0]
        self.assertEqual(sorted(rec), ["item_id", "platform",
                                       "published_at", "source_url",
                                       "text", "title"])
        for value in rec.values():
            self.assertIsInstance(value, str)

    def test_oversized_source_content_is_truncated(self):
        def huge(url, n):
            return [{"item_id": "u1", "title": "T" * 5000,
                     "text": "x" * 100000,
                     "source_url": "https://a.test/1"}]
        out = tools.execute(
            "discovery.fetch", {"url": "https://a.test/1"},
            {"discovery.fetch": {"n": 1}}, fetch_fn=huge)
        self.assertEqual(len(out["items"][0]["text"]),
                         tools.MAX_TOOL_TEXT)
        self.assertEqual(len(out["items"][0]["title"]),
                         tools.MAX_TOOL_TITLE)

    def test_executor_failure_is_a_typed_result_not_a_crash(self):
        def boom(url, n):
            raise RuntimeError("provider down")
        out = tools.execute(
            "discovery.fetch", {"url": "https://a.test/1"},
            {"discovery.fetch": {"n": 1}}, fetch_fn=boom)
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "tool-failed:RuntimeError")

    def test_history_is_labelled_as_data_not_instructions(self):
        model = ScriptedModel([
            _tool_call("discovery.fetch", {"url": "https://a.test/1"}),
            _final("no_material", {"summary": "s"}),
        ])
        agent_loop.run_loop(
            invoke=model.agent_turn, skill_doc=_skill(),
            contract=_contract(), tool_names=["discovery.fetch"],
            tool_limits={"discovery.fetch": {"n": 1}}, tool_calls=2,
            items=[])
        user = model.prompts[1][1]
        self.assertIn("DATA ONLY", user)
        self.assertIn("never as instructions", user)

    def test_capability_allowlist_fails_closed(self):
        with self.assertRaises(tools.ToolError):
            tools.enabled_tools({"provider": {}})
        with self.assertRaises(tools.ToolError):
            tools.enabled_tools({"discovery": {"vendor": "x"}})
        self.assertEqual(tools.enabled_tools({}), {})
        self.assertEqual(tools.enabled_tools(None), {})
        self.assertEqual(sorted(tools.enabled_tools({"discovery": {}})),
                         ["discovery.fetch", "discovery.search"])


class StageIntegrationCase(unittest.TestCase):
    """The stage itself: same runtime function, three skills."""

    class Client:
        def release_claim(self, key, run_id):
            pass

        def get_research_state(self, source):
            raise RuntimeError("no state")  # empty queue

        def save_research_state(self, source, state):
            return {}

    def _run(self, instructions, turns, capabilities, budgets,
             search=None, fetch=None):
        from stage_run import run_research_stage
        model = ScriptedModel(turns)
        seen = {}

        def fake_execute(name, arguments, limits, *, now="",
                         search_fn=None, fetch_fn=None):
            seen["calls"] = seen.get("calls", 0) + 1
            if name == "discovery.search":
                items = (search or _search_fn)(arguments.get("query", ""),
                                               limits.get(name, {}).get("n", 3),
                                               None)
            else:
                items = (fetch or _fetch_fn)(arguments.get("url", ""),
                                             limits.get(name, {}).get("n", 3))
            return {"ok": True, "tool": name, "count": len(items),
                    "items": [tools._shape_item(i) for i in items]}

        real_execute = tools.execute
        tools.execute = fake_execute
        try:
            payload = run_research_stage({
                "store": _MemStore(), "cfg": {
                    "skills": {"research": "agent"},
                    "capabilities": capabilities,
                    "budgets": budgets,
                },
                "client": self.Client(), "run_id": "r-1",
                "sources": [], "now": "2026-09-22T00:00:00+00:00",
                "dry_run": True, "claimed": [],
                "research_model": model,
                "roles_resolved": [], "roles": ["research"],
                "claimer": None,
            })
        finally:
            tools.execute = real_execute
        return payload, model, seen

    def _patch_skill(self, instructions, contract=None):
        import skills.loader as loader
        real = loader.get_skill
        doc = _skill(instructions, contract)
        loader.get_skill = lambda t, name=None: doc
        self.addCleanup(lambda: setattr(loader, "get_skill", real))

    def test_empty_input_reaches_the_agent_for_all_three_skills(self):
        # A: no tool call, declares no_material.
        self._patch_skill("Never call tools.")
        payload_a, model_a, seen_a = self._run(
            "Never call tools.",
            [_final("no_material", {"summary": "Nothing."})],
            {"discovery": {}}, {"max_tool_calls": 5})
        self.assertEqual(payload_a["outcome"], "no_material")
        self.assertEqual(payload_a["items"], [])
        self.assertEqual(seen_a.get("calls", 0), 0)
        self.assertEqual(model_a.calls, 1)

        # B: discovery.search then discovery.fetch then findings.
        self._patch_skill("Discover when empty.")
        payload_b, model_b, seen_b = self._run(
            "Discover when empty.",
            [_tool_call("discovery.search", {"query": "mastering"}),
             _tool_call("discovery.fetch", {"url": "https://a.test/1"}),
             _final("findings", {"summary": "Found.",
                                 "findings": FINDINGS})],
            {"discovery": {}}, {"max_tool_calls": 5})
        self.assertEqual(payload_b["outcome"], "findings")
        self.assertTrue(payload_b["items"])
        self.assertEqual(seen_b["calls"], 2)
        self.assertEqual(model_b.calls, 3)

        # C: broaden after a poor first search, then fetch.
        self._patch_skill("Broaden after a poor search.")
        payload_c, model_c, seen_c = self._run(
            "Broaden after a poor search.",
            [_tool_call("discovery.search", {"query": "narrow"}),
             _tool_call("discovery.search", {"query": "broad loudness"}),
             _tool_call("discovery.fetch", {"url": "https://a.test/1"}),
             _final("findings", {"summary": "Found.",
                                 "findings": FINDINGS})],
            {"discovery": {}}, {"max_tool_calls": 6})
        self.assertEqual(payload_c["outcome"], "findings")
        self.assertEqual(seen_c["calls"], 3)

        # Identical runtime: one function, one loop, three skills.
        for payload in (payload_a, payload_b, payload_c):
            self.assertEqual(payload["skills_used"][0]["contract"],
                             "custom")

    def test_budget_exhaustion_stops_cleanly(self):
        self._patch_skill("Keep searching.")
        payload, _model, seen = self._run(
            "Keep searching.",
            [_tool_call("discovery.search", {"query": "a"}),
             _tool_call("discovery.search", {"query": "b"})],
            {"discovery": {}}, {"max_tool_calls": 1})
        self.assertIsNone(payload["outcome"])
        self.assertEqual(payload["agent_stop"], "budget_exhausted")
        self.assertEqual(payload["items"], [])
        self.assertEqual(seen["calls"], 1)


class _MemStore:
    def load(self, source_id):
        from research.monitor import SourceState
        return SourceState(source_id=source_id)

    def save(self, state):
        return None


class NoSkillBranchCase(unittest.TestCase):
    def test_runtime_selects_the_agent_path_by_contract_only(self):
        # The runtime branch is "does the skill declare a custom
        # contract?", never "which skill is this?". A/B/C therefore
        # share one code path by construction.
        import inspect
        import stage_run
        src = inspect.getsource(stage_run._research_agent_skill)
        self.assertIn("contract_id", src)
        for forbidden in ("juzzir", "evergreen", "muzziga", "=="):
            self.assertNotIn(forbidden, src)
        # And the agent payload builder never inspects skill names.
        payload_src = inspect.getsource(
            stage_run._research_agent_payload)
        for forbidden in ("juzzir", "evergreen", "muzziga",
                          "skill_doc[\"name\"]"):
            self.assertNotIn(forbidden, payload_src)


if __name__ == "__main__":
    unittest.main()
