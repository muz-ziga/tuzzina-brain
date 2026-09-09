"""run_cycle.py CLI tests: one-shot machine contract. Fake client
(no network), stubbed website extraction, fixed clock via schedule.
Proves: result envelope, exit codes, store selection, run-id,
mock-live refusal, dry-run zero-write, live post_ids, secrecy."""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import run_cycle as rc_mod
from tuzzina.client import TuzzinaError


def _make_client(items=None, state=None, calls=None, distribution=None,
                 usage=None):
    from tuzzina.client import TuzzinaClient
    c = TuzzinaClient("http://x/api", "k")
    c.integrations = lambda: items or []
    def _lookup(iid):
        for it in (items or []):
            if it.get("id") == iid:
                return it
        raise TuzzinaError(f"integration {iid} not found in Tuzzina")
    c.get_integration = _lookup
    c.upload_from_url = lambda url: (
        calls.append(("upload_from_url", url)) or
        {"id": "m1", "path": url})
    c.upload_bytes = lambda data, name, mime: (
        calls.append(("upload_bytes", name)) or
        {"id": "m2", "path": f"https://pub.r2.dev/{name}"})
    c.get_integration_settings = lambda iid: {
        "output": {"rules": "r", "maxLength": 63206,
                   "settings": {}, "tools": []}}
    c.create_post = lambda posts, date, post_type="draft": (
        calls.append(("create_post", post_type)) or [{"postId": "p1"}])
    c.generate_image = lambda prompt: (
        calls.append(("generate_image", prompt)) or
        {"id": "m9", "path": "https://pub.r2.dev/ai.png"})
    c.get_research_state = lambda sid: (
        dict(state.get(sid)) if sid in state else None)
    c.save_research_state = lambda sid, st: (
        calls.append(("save_state", sid)) or
        state.update({sid: dict(st)}) or {"sourceId": sid})
    c.get_content_distribution = lambda iid: (
        {"integration_id": iid,
         "formats": dict(distribution) if distribution is not None
         else {"text": 10, "text+image": 5, "text+video": 3,
               "link+text": 2},
         "enabled": True})
    def _usage(iid, day):
        calls.append(("get_usage", day))
        if isinstance(usage, BaseException):
            raise usage
        return dict(usage or {})
    c.get_content_usage = _usage
    return c


def _write_strategy(tmpdir, iid="integ-fb-1"):
    from channels.strategy import (Brand, ChannelStrategy, ContentPolicy,
                                    GenerationPolicy, HashtagPolicy,
                                    PlanningPolicy)
    from channels.strategy_store import save
    save(ChannelStrategy(
        integration_id=iid, brand=Brand(tone="loud"),
        hashtags=HashtagPolicy(enabled=True, max=2),
        links_policy="hide", content=ContentPolicy(),
        generation=GenerationPolicy(), planning=PlanningPolicy()),
        base_dir=tmpdir)


CAMPAIGN = """
brand:
  name: X
language:
  default: ar
hashtags:
  enabled: true
  per_platform:
    facebook:
      max: 5
links_policy: hide
sources:
  - type: website
    url: https://example.com
    n: 1
schedule:
  timezone: Europe/Madrid
"""


def _write_campaign(tmpdir):
    p = os.path.join(tmpdir, "camp.yaml")
    with open(p, "w", encoding="utf-8") as f:
        f.write(CAMPAIGN)
    return p


class FakeWebsite:
    def extract(self, url, n):
        from contracts import ExtractionResult, Identity, SourceItem
        return ExtractionResult(
            items=[SourceItem(source_id="s1", source_type="website",
                              source_url=url, title="T", text="body",
                              images=[])],
            identity=Identity(name="X"),
            meta={"returned": 1, "requested": n,
                  "partial": False, "reason": ""})


ITEMS = [{"id": "integ-fb-1", "name": "Juzzir",
          "identifier": "facebook", "picture": "p"}]


def _run_cli(argv, *, state=None, calls=None, openai_key="",
             distribution=None, usage=None):
    calls = calls if calls is not None else []
    state = state if state is not None else {}
    os.environ["TUZZINA_API_KEY"] = "k"
    if openai_key:
        os.environ["OPENAI_API_KEY"] = openai_key
    else:
        os.environ.pop("OPENAI_API_KEY", None)
    orig_client = rc_mod._build_client
    import research.cycle as cyc_mod
    orig_website = cyc_mod.WebsiteAdapter
    rc_mod._build_client = lambda base, key: _make_client(
        ITEMS, state, calls, distribution=distribution, usage=usage)
    cyc_mod.WebsiteAdapter = FakeWebsite
    buf_out, buf_err = io.StringIO(), io.StringIO()
    try:
        with redirect_stdout(buf_out), redirect_stderr(buf_err):
            rc = rc_mod.main(argv)
        return rc, buf_out.getvalue(), buf_err.getvalue()
    finally:
        rc_mod._build_client = orig_client
        cyc_mod.WebsiteAdapter = orig_website
        os.environ.pop("TUZZINA_API_KEY", None)
        os.environ.pop("OPENAI_API_KEY", None)


def _result(out):
    lines = [l for l in out.split("\n") if l.startswith("BRAIN_RESULT ")]
    assert lines, f"no result envelope in: {out[-500:]}"
    return json.loads(lines[-1][len("BRAIN_RESULT "):])


class CycleCliCase(unittest.TestCase):
    def _case(self):
        tmp = tempfile.mkdtemp(prefix="tbra_cycle_")
        camp = _write_campaign(tmp)
        _write_strategy(tmp)
        return tmp, camp

    def test_dry_run_envelope_and_zero_writes(self):
        tmp, camp = self._case()
        calls: list = []
        rc, out, err = _run_cli(
            [camp, "--strategy-by-integration", "integ-fb-1",
             "--strategy-base-dir", tmp, "--dry-run",
             "--run-id", "r1"],
            calls=calls)
        self.assertEqual(rc, 0)
        res = _result(out)
        self.assertEqual(res["run_id"], "r1")
        self.assertEqual(res["status"], "no-op")
        self.assertEqual(res["sources_checked"], 1)
        self.assertEqual(res["items_new"], 1)
        # The cycle completes (memory cursor advances); "zero
        # writes" means zero Tuzzina/external writes, proven below.
        self.assertEqual(res["committed"], True)
        kinds = [k for k, _ in calls]
        self.assertNotIn("upload_from_url", kinds)
        self.assertNotIn("upload_bytes", kinds)
        self.assertNotIn("create_post", kinds)
        self.assertNotIn("generate_image", kinds)
        self.assertNotIn("save_state", kinds)

    def test_run_id_generated_when_absent(self):
        tmp, camp = self._case()
        rc, out, _ = _run_cli(
            [camp, "--strategy-by-integration", "integ-fb-1",
             "--strategy-base-dir", tmp, "--dry-run"])
        self.assertEqual(rc, 0)
        self.assertTrue(_result(out)["run_id"])

    def test_mock_live_refused(self):
        tmp, camp = self._case()
        rc, out, err = _run_cli(
            [camp, "--strategy-by-integration", "integ-fb-1",
             "--strategy-base-dir", tmp, "--run-id", "r2"])
        self.assertEqual(rc, 2)
        self.assertEqual(_result(out)["error"], "mock-live-refused")

    def test_distribution_gate_end_to_end(self):
        # FakeWebsite yields a text item; the fake distribution
        # allows only text+image -> the cycle stops cleanly with
        # zero intents and no writes (dry-run).
        tmp, camp = self._case()
        calls: list = []
        rc, out, err = _run_cli(
            [camp, "--strategy-by-integration", "integ-fb-1",
             "--strategy-base-dir", tmp, "--dry-run",
             "--run-id", "r-dist"],
            calls=calls, distribution={"text+image": 5})
        self.assertEqual(rc, 0, msg=err)
        res = _result(out)
        self.assertEqual(res["status"], "no-op")
        self.assertEqual(res["items_new"], 1)
        kinds = [k for k, _ in calls]
        self.assertNotIn("create_post", kinds)

    def test_live_run_posts_and_reports_ids(self):
        # Live (non-dry) run with all three roles stubbed offline:
        # proves the execution half (media resolve + inject) and
        # the post_ids in the result envelope.
        tmp, camp = self._case()
        calls: list = []
        import g2.generators as G
        from research.models import MockResearchModel
        from research.analysis import MockAnalysisModel
        orig_text = G.OpenAITextGenerator
        orig_models = rc_mod._build_models
        class FakeText(G.TextGenerator):
            def generate(self, title, summary, brand):
                return "Hello world post"
        G.OpenAITextGenerator = lambda *a, **k: FakeText()
        rc_mod._build_models = lambda mode: (MockResearchModel(),
                                             MockAnalysisModel())
        try:
            rc, out, err = _run_cli(
                [camp, "--strategy-by-integration", "integ-fb-1",
                 "--strategy-base-dir", tmp, "--openai",
                 "--run-id", "r3"],
                calls=calls, openai_key="sk-test")
        finally:
            G.OpenAITextGenerator = orig_text
            rc_mod._build_models = orig_models
        self.assertEqual(rc, 0, msg=err)
        res = _result(out)
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["post_ids"], ["p1"])
        self.assertIn(("create_post", "draft"), calls)

    def test_missing_key_is_config_error(self):
        tmp, camp = self._case()
        os.environ.pop("TUZZINA_API_KEY", None)
        orig_client = rc_mod._build_client
        import research.cycle as cyc_mod
        orig_website = cyc_mod.WebsiteAdapter
        cyc_mod.WebsiteAdapter = FakeWebsite
        buf_out = io.StringIO()
        try:
            with redirect_stdout(buf_out):
                rc = rc_mod.main(
                    [camp, "--strategy-by-integration", "integ-fb-1",
                     "--strategy-base-dir", tmp, "--dry-run"])
        finally:
            rc_mod._build_client = orig_client
            cyc_mod.WebsiteAdapter = orig_website
        self.assertEqual(rc, 2)
        self.assertEqual(_result(buf_out.getvalue())["error"],
                         "missing-api-key")

    def test_dry_run_forces_memory_store(self):
        tmp, camp = self._case()
        state: dict = {}
        calls: list = []
        rc, out, _ = _run_cli(
            [camp, "--strategy-by-integration", "integ-fb-1",
             "--strategy-base-dir", tmp, "--dry-run",
             "--state-store", "tuzzina", "--run-id", "r4"],
            state=state, calls=calls)
        # dry-run forces memory: no state writes even when asked.
        self.assertEqual(rc, 0)
        self.assertNotIn("save_state", [k for k, _ in calls])
        self.assertEqual(state, {})

    def test_tuzzina_store_commits_on_live_run(self):
        # Live run with --state-store tuzzina persists the cursor
        # through the (faked) Public API state endpoints.
        tmp, camp = self._case()
        state: dict = {}
        calls: list = []
        import g2.generators as G
        from research.models import MockResearchModel
        from research.analysis import MockAnalysisModel
        orig_text = G.OpenAITextGenerator
        orig_models = rc_mod._build_models
        class FakeText(G.TextGenerator):
            def generate(self, title, summary, brand):
                return "Hello world post"
        G.OpenAITextGenerator = lambda *a, **k: FakeText()
        rc_mod._build_models = lambda mode: (MockResearchModel(),
                                             MockAnalysisModel())
        try:
            rc, out, err = _run_cli(
                [camp, "--strategy-by-integration", "integ-fb-1",
                 "--strategy-base-dir", tmp, "--openai",
                 "--state-store", "tuzzina", "--run-id", "r6"],
                state=state, calls=calls, openai_key="sk-test")
        finally:
            G.OpenAITextGenerator = orig_text
            rc_mod._build_models = orig_models
        self.assertEqual(rc, 0, msg=err)
        self.assertIn("save_state", [k for k, _ in calls])
        self.assertTrue(any(state.values()))
        self.assertTrue(_result(out)["committed"])

    def test_no_secrets_in_result(self):
        tmp, camp = self._case()
        rc, out, err = _run_cli(
            [camp, "--strategy-by-integration", "integ-fb-1",
             "--strategy-base-dir", tmp, "--dry-run",
             "--run-id", "r5"])
        blob = out + err
        self.assertNotIn("TUZZINA_API_KEY", blob.replace(
            "TUZZINA_API_KEY is not set", ""))
        for bad in ("sk-", "Bearer", "OPENAI_API_KEY="):
            self.assertNotIn(bad, blob)


ROLE_ENV_KEYS = [f"BRAIN_{r}_{s}" for r in
                 ("RESEARCH", "ANALYSIS", "TEXT")
                 for s in ("ADAPTER", "KEY", "MODEL")]


class RoleEnvCase(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in ROLE_ENV_KEYS}
        for k in ROLE_ENV_KEYS:
            os.environ.pop(k, None)
        self._saved_shared = os.environ.get("OPENAI_API_KEY")
        os.environ["OPENAI_API_KEY"] = "sk-shared"

    def tearDown(self):
        for k in ROLE_ENV_KEYS:
            if self._saved[k] is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = self._saved[k]
        if self._saved_shared is None:
            os.environ.pop("OPENAI_API_KEY", None)
        else:
            os.environ["OPENAI_API_KEY"] = self._saved_shared

    def test_openai_defaults_without_role_env(self):
        rm, am = rc_mod._build_models("--openai")
        from llm.adapters import OpenAIAdapter
        self.assertIsInstance(rm._adapter, OpenAIAdapter)
        self.assertIsInstance(am._adapter, OpenAIAdapter)
        self.assertEqual(rm._adapter.api_key, "sk-shared")
        self.assertEqual(rm._adapter.model, "gpt-4.1")

    def test_per_role_switch(self):
        os.environ["BRAIN_TEXT_ADAPTER"] = "anthropic"
        os.environ["BRAIN_TEXT_KEY"] = "sk-ant-text"
        os.environ["BRAIN_TEXT_MODEL"] = "claude-x"
        tg = rc_mod._build_text_gen("--openai")
        from llm.adapters import AnthropicAdapter
        self.assertIsInstance(tg._adapter, AnthropicAdapter)
        self.assertEqual(tg._adapter.api_key, "sk-ant-text")
        self.assertEqual(tg._adapter.model, "claude-x")
        rm, am = rc_mod._build_models("--openai")
        from llm.adapters import OpenAIAdapter
        self.assertIsInstance(rm._adapter, OpenAIAdapter)
        self.assertIsInstance(am._adapter, OpenAIAdapter)

    def test_missing_key_fails_closed(self):
        os.environ.pop("OPENAI_API_KEY", None)
        with self.assertRaises(ValueError) as ctx:
            rc_mod._build_models("--openai")
        self.assertIn("KEY", str(ctx.exception))
        with self.assertRaises(ValueError):
            rc_mod._build_text_gen("--openai")

    def test_unknown_adapter_fails_closed(self):
        os.environ["BRAIN_RESEARCH_ADAPTER"] = "gemini"
        os.environ["BRAIN_RESEARCH_KEY"] = "k"
        with self.assertRaises(ValueError) as ctx:
            rc_mod._build_models("--openai")
        self.assertIn("unknown-llm-adapter", str(ctx.exception))

    def test_mock_ignores_role_env(self):
        os.environ["BRAIN_TEXT_ADAPTER"] = "anthropic"
        os.environ["BRAIN_TEXT_KEY"] = "sk-ant-text"
        from research.models import MockResearchModel
        from research.analysis import MockAnalysisModel
        from g2.generators import MockTextGenerator
        rm, am = rc_mod._build_models("--mock")
        self.assertIsInstance(rm, MockResearchModel)
        self.assertIsInstance(am, MockAnalysisModel)
        self.assertIsInstance(rc_mod._build_text_gen("--mock"),
                              MockTextGenerator)

    def test_roles_resolved_in_envelope_without_secrets(self):
        tmp, camp = self._case() if hasattr(self, "_case") else (None, None)
        import tempfile
        tmp = tempfile.mkdtemp(prefix="tbra_roles_")
        with open(os.path.join(tmp, "camp.yaml"), "w",
                  encoding="utf-8") as f:
            f.write(CAMPAIGN)
        _write_strategy(tmp)
        os.environ["BRAIN_TEXT_MODEL"] = "gpt-4.1-custom"
        import g2.generators as G
        from research.models import MockResearchModel
        from research.analysis import MockAnalysisModel
        orig_text = G.OpenAITextGenerator
        orig_models = rc_mod._build_models
        class FakeText(G.TextGenerator):
            def generate(self, title, summary, brand):
                return "Hello world post"
        G.OpenAITextGenerator = lambda *a, **k: FakeText()
        rc_mod._build_models = lambda mode: (MockResearchModel(),
                                             MockAnalysisModel())
        try:
            rc, out, err = _run_cli(
                [os.path.join(tmp, "camp.yaml"),
                 "--strategy-by-integration", "integ-fb-1",
                 "--strategy-base-dir", tmp, "--openai",
                 "--dry-run", "--run-id", "r-roles"],
                openai_key="sk-shared")
        finally:
            G.OpenAITextGenerator = orig_text
            rc_mod._build_models = orig_models
            os.environ.pop("BRAIN_TEXT_MODEL", None)
        self.assertEqual(rc, 0, msg=err)
        res = _result(out)
        roles = {r["role"]: r for r in res["roles_resolved"]}
        self.assertEqual(roles["text"]["model"], "gpt-4.1-custom")
        self.assertEqual(roles["text"]["adapter"], "openai")
        self.assertNotIn("credential", json.dumps(res))
        self.assertNotIn("sk-shared", out + err)


class UsageGateCase(unittest.TestCase):
    def _case(self):
        tmp = tempfile.mkdtemp(prefix="tbra_use_")
        with open(os.path.join(tmp, "camp.yaml"), "w",
                  encoding="utf-8") as f:
            f.write(CAMPAIGN)
        _write_strategy(tmp)
        return tmp, os.path.join(tmp, "camp.yaml")

    def test_exhausted_format_blocks(self):
        tmp, camp = self._case()
        calls: list = []
        rc, out, err = _run_cli(
            [camp, "--strategy-by-integration", "integ-fb-1",
             "--strategy-base-dir", tmp, "--dry-run",
             "--run-id", "r-use"],
            calls=calls,
            distribution={"text": 1},
            usage={"text": 1})
        self.assertEqual(rc, 0, msg=err)
        res = _result(out)
        self.assertEqual(res["status"], "no-op")
        self.assertEqual(res["usage"]["used"], {"text": 1})
        self.assertEqual(res["usage"]["remaining"], {"text": 0})
        self.assertEqual(res["intents"], 0)

    def test_remaining_capacity_proceeds(self):
        tmp, camp = self._case()
        calls: list = []
        rc, out, err = _run_cli(
            [camp, "--strategy-by-integration", "integ-fb-1",
             "--strategy-base-dir", tmp, "--dry-run",
             "--run-id", "r-use2"],
            calls=calls,
            distribution={"text": 2},
            usage={"text": 1})
        self.assertEqual(rc, 0, msg=err)
        res = _result(out)
        self.assertEqual(res["usage"]["remaining"], {"text": 1})
        self.assertGreater(res["intents"], 0)

    def test_usage_failure_preserves_targets(self):
        tmp, camp = self._case()
        calls: list = []
        rc, out, err = _run_cli(
            [camp, "--strategy-by-integration", "integ-fb-1",
             "--strategy-base-dir", tmp, "--dry-run",
             "--run-id", "r-use3"],
            calls=calls,
            distribution={"text": 1},
            usage=RuntimeError("down"))
        self.assertEqual(rc, 0)
        res = _result(out)
        self.assertGreater(res["intents"], 0)
        self.assertIn("usage unavailable", err)


if __name__ == "__main__":
    unittest.main()
