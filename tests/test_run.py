"""run.py tests: the new --strategy-by-integration mode and the
legacy mode. Mocks TuzzinaClient so no real HTTP is hit."""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _make_client(items=None):
    client = object()  # placeholder; we replace methods directly
    from tuzzina.client import TuzzinaClient, TuzzinaError
    c = TuzzinaClient("http://x/api", "k")
    c.integrations = lambda: items or []
    def _lookup(iid):
        for it in (items or []):
            if it.get("id") == iid:
                return it
        raise TuzzinaError(f"integration {iid} not found in Tuzzina")
    c.get_integration = _lookup
    # upload/draft stubs (run.py will not hit the network)
    c.upload_from_url = lambda url: {"id": "m1", "path": url}
    c.upload_bytes = lambda data, name, mime: {
        "id": "m2", "path": f"https://pub.r2.dev/{name}"}
    c.create_draft = lambda posts, date: [{"postId": "p1"}]
    c.create_post = lambda posts, date, post_type="draft": [{"postId": "p1"}]
    return c


def _write_strategy(tmpdir, iid, **overrides):
    from channels.strategy import (Brand, ChannelStrategy, ContentPolicy,
                                    GenerationPolicy, HashtagPolicy,
                                    PlanningPolicy)
    s = ChannelStrategy(
        integration_id=iid,
        brand=Brand(tone="loud"),
        hashtags=HashtagPolicy(enabled=True, max=2),
        links_policy="hide",
        content=ContentPolicy(),
        generation=GenerationPolicy(),
        planning=PlanningPolicy(),
    )
    for k, v in overrides.items():
        setattr(s, k, v)
    from channels.strategy_store import save
    save(s, base_dir=tmpdir)
    return s


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


def _run_run(argv, *, tmpdir, items, strict_client=None):
    os.environ["TUZZINA_API_KEY"] = "k"
    import importlib
    import run as run_mod
    orig_website = run_mod.WebsiteAdapter

    def _fake_build(base, key):
        if strict_client is not None:
            return strict_client
        return _make_client(items)

    orig_build = getattr(run_mod, "_build_client", None)
    if orig_build is not None:
        run_mod._build_client = _fake_build
    else:
        run_mod.TuzzinaClient = _fake_build
    # Avoid real network: WebsiteAdapter.extract is monkey-patched.
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
    run_mod.WebsiteAdapter = FakeWebsite
    buf_out, buf_err = io.StringIO(), io.StringIO()
    with redirect_stdout(buf_out), redirect_stderr(buf_err):
        try:
            rc = run_mod.main(argv)
        except SystemExit as e:
            rc = e.code if isinstance(e.code, int) else 1
        finally:
            if orig_build is not None:
                run_mod._build_client = orig_build
            else:
                run_mod.TuzzinaClient = orig_build  # actually orig TuzzinaClient
            run_mod.WebsiteAdapter = orig_website
    return rc, buf_out.getvalue(), buf_err.getvalue()


class RunByIntegrationTest(unittest.TestCase):
    def test_loads_strategy_and_validates_against_tuzzina(self):
        tmp = tempfile.mkdtemp(prefix="tbra_run_")
        camp = _write_campaign(tmp)
        _write_strategy(tmp, "integ-fb-1")
        items = [{"id": "integ-fb-1", "name": "Juzzir Facebook",
                   "identifier": "facebook"}]
        rc, out, err = _run_run(
            [camp, "--strategy-by-integration", "integ-fb-1",
             "--strategy-base-dir", tmp],
            tmpdir=tmp, items=items)
        self.assertEqual(rc, 0, msg=f"err={err}")
        # Platform printed = facebook (from Tuzzina, not from YAML)
        self.assertIn("platform='facebook'", out)
        self.assertIn("INJECTED", out)

    def test_strategy_cannot_override_platform(self):
        # If a malformed strategy somehow set provider_overrides with
        # a '__type' key, the run must use Tuzzina's platform, not the
        # strategy's value. The current schema doesn't accept
        # '__type' in strategy/provider_overrides (no field for it),
        # so this test only documents the current safety and the
        # 'provider' field on GenerationPolicy is advisory, not
        # authoritative. We still verify that the platform Tuzzina
        # returned is what is sent to the scheduler.
        tmp = tempfile.mkdtemp(prefix="tbra_run_")
        camp = _write_campaign(tmp)
        _write_strategy(tmp, "integ-fb-1",
                        provider_overrides={"post_type": "post"})
        items = [{"id": "integ-fb-1", "name": "Juzzir",
                   "identifier": "facebook"}]
        rc, out, err = _run_run(
            [camp, "--strategy-by-integration", "integ-fb-1",
             "--strategy-base-dir", tmp],
            tmpdir=tmp, items=items)
        self.assertEqual(rc, 0, msg=f"err={err}")
        # Build path uses cfg["channel_meta"]["identifier"]; the
        # code in run.py passes that platform to build_package. The
        # strategy's provider_overrides only contain post_type, no
        # __type leakage.
        self.assertIn("platform='facebook'", out)
        self.assertIn("INJECTED", out)

    def test_missing_integration_id_stops_before_g1(self):
        tmp = tempfile.mkdtemp(prefix="tbra_run_")
        camp = _write_campaign(tmp)
        # No strategy file; strategy_store.load will FileNotFoundError
        # before G1 runs.
        items = []
        rc, out, err = _run_run(
            [camp, "--strategy-by-integration", "missing-id",
             "--strategy-base-dir", tmp],
            tmpdir=tmp, items=items)
        self.assertNotEqual(rc, 0, msg=f"err={err}")

    def test_tuzzina_404_stops_run(self):
        tmp = tempfile.mkdtemp(prefix="tbra_run_")
        camp = _write_campaign(tmp)
        _write_strategy(tmp, "integ-1")
        # items is empty so get_integration raises; we never call G1.
        items = []
        rc, out, err = _run_run(
            [camp, "--strategy-by-integration", "integ-1",
             "--strategy-base-dir", tmp],
            tmpdir=tmp, items=items)
        self.assertNotEqual(rc, 0, msg=f"err={err}")


class DryRunTest(unittest.TestCase):
    """--dry-run performs the full read path (strategy load,
    integration lookup, G1/G2/G3, intent building, adapter
    translation) with ZERO writes: no upload, no create_post."""

    def _write_strategy(self, tmpdir, iid="integ-fb-1"):
        from channels.strategy import (Brand, ChannelStrategy,
                                        ContentPolicy, GenerationPolicy,
                                        HashtagPolicy, PlanningPolicy)
        from channels.strategy_store import save
        save(ChannelStrategy(
            integration_id=iid,
            brand=Brand(tone="loud"),
            hashtags=HashtagPolicy(enabled=True, max=2),
            links_policy="hide",
            content=ContentPolicy(),
            generation=GenerationPolicy(),
            planning=PlanningPolicy(),
        ), base_dir=tmpdir)

    def _strict_client(self, items):
        # Any write attempt raises; get_integration read allowed.
        client = _make_client(items)
        client.upload_from_url = _boom("upload_from_url")
        client.upload_bytes = _boom("upload_bytes")
        client.create_post = _boom("create_post")
        client.create_draft = _boom("create_draft")
        return client

    def test_dry_run_performs_zero_writes(self):
        tmp = tempfile.mkdtemp(prefix="tbra_dry_")
        camp = _write_campaign(tmp)
        self._write_strategy(tmp)
        items = [{"id": "integ-fb-1", "name": "Juzzir",
                   "identifier": "facebook"}]
        rc, out, err = _run_run(
            [camp, "--strategy-by-integration", "integ-fb-1",
             "--strategy-base-dir", tmp, "--dry-run"],
            tmpdir=tmp, items=items,
            strict_client=self._strict_client(items))
        self.assertEqual(rc, 0, msg=f"err={err}")
        self.assertIn("DRY-RUN", out)
        self.assertNotIn("INJECTED", out)

    def test_dry_run_output_is_safe(self):
        tmp = tempfile.mkdtemp(prefix="tbra_dry_")
        camp = _write_campaign(tmp)
        self._write_strategy(tmp)
        items = [{"id": "integ-fb-1", "name": "Juzzir",
                   "identifier": "facebook"}]
        rc, out, err = _run_run(
            [camp, "--strategy-by-integration", "integ-fb-1",
             "--strategy-base-dir", tmp, "--dry-run"],
            tmpdir=tmp, items=items,
            strict_client=self._strict_client(items))
        self.assertEqual(rc, 0, msg=f"err={err}")
        # Safe metadata shown...
        for key in ("integration_id", "identifier", "mode",
                     "post_kind", "publish_at", "adapter"):
            self.assertIn(key, out)
        # ...secrets never shown.
        blob = (out + err).lower()
        for bad in ("apikey", "api_key", "authorization", "bearer",
                    "secret", "token", "cookie", "password"):
            self.assertNotIn(bad, blob)

    def test_normal_mode_still_writes(self):
        # Regression: --dry-run must not leak into the normal path.
        tmp = tempfile.mkdtemp(prefix="tbra_dry_")
        camp = _write_campaign(tmp)
        self._write_strategy(tmp)
        items = [{"id": "integ-fb-1", "name": "Juzzir",
                   "identifier": "facebook"}]
        rc, out, err = _run_run(
            [camp, "--strategy-by-integration", "integ-fb-1",
             "--strategy-base-dir", tmp],
            tmpdir=tmp, items=items)
        self.assertEqual(rc, 0, msg=f"err={err}")
        self.assertIn("INJECTED", out)
        self.assertNotIn("DRY-RUN", out)


def _boom(name):
    def _raise(*a, **k):
        raise AssertionError(f"dry-run must never call {name}")
    return _raise


class InjectionPathTest(unittest.TestCase):
    """The orchestration path goes through CanonicalIntent +
    InjectionService (no manual payload construction)."""

    def test_schedule_mode_flows_through(self):
        tmp = tempfile.mkdtemp(prefix="tbra_run_")
        camp = _write_campaign(tmp)
        _write_strategy(tmp, "integ-fb-1")
        items = [{"id": "integ-fb-1", "name": "Juzzir",
                   "identifier": "facebook"}]
        rc, out, err = _run_run(
            [camp, "--strategy-by-integration", "integ-fb-1",
             "--strategy-base-dir", tmp, "--post-mode", "schedule"],
            tmpdir=tmp, items=items)
        self.assertEqual(rc, 0, msg=f"err={err}")
        self.assertIn("mode=schedule", out)

    def test_now_mode_flows_through(self):
        tmp = tempfile.mkdtemp(prefix="tbra_run_")
        camp = _write_campaign(tmp)
        _write_strategy(tmp, "integ-fb-1")
        items = [{"id": "integ-fb-1", "name": "Juzzir",
                   "identifier": "facebook"}]
        rc, out, err = _run_run(
            [camp, "--strategy-by-integration", "integ-fb-1",
             "--strategy-base-dir", tmp, "--post-mode", "now"],
            tmpdir=tmp, items=items)
        self.assertEqual(rc, 0, msg=f"err={err}")
        self.assertIn("mode=now", out)

    def test_no_manual_payload_construction(self):
        # run.py must not build Tuzzina post payloads by hand;
        # payload assembly lives in InjectionService only.
        import pathlib
        src = (pathlib.Path(__file__).parent.parent / "src" / "run.py"
               ).read_text(encoding="utf-8")
        self.assertNotIn("posts_payload", src)
        self.assertNotIn("create_draft", src)

    def test_stale_integration_fails_cleanly(self):
        tmp = tempfile.mkdtemp(prefix="tbra_run_")
        camp = _write_campaign(tmp)
        _write_strategy(tmp, "integ-stale")
        items = []  # Tuzzina knows nothing about integ-stale
        rc, out, err = _run_run(
            [camp, "--strategy-by-integration", "integ-stale",
             "--strategy-base-dir", tmp],
            tmpdir=tmp, items=items)
        self.assertNotEqual(rc, 0)
        self.assertNotIn("INJECTED", out)

    def test_unsupported_platform_fails_cleanly(self):
        tmp = tempfile.mkdtemp(prefix="tbra_run_")
        camp = _write_campaign(tmp)
        _write_strategy(tmp, "integ-tt-1")
        items = [{"id": "integ-tt-1", "name": "Juzzir TT",
                   "identifier": "tiktok"}]
        rc, out, err = _run_run(
            [camp, "--strategy-by-integration", "integ-tt-1",
             "--strategy-base-dir", tmp],
            tmpdir=tmp, items=items)
        self.assertEqual(rc, 4, msg=f"err={err}")
        self.assertNotIn("INJECTED", out)

    def test_attach_policy_reaches_intent_without_crash(self):
        # Regression: Phase 2 referenced p.source_ref which did not
        # exist on PlannedPost, crashing every run with
        # links_policy == "attach" (AttributeError). PlannedPost now
        # carries source_ref from the package; the attach link must
        # flow into the injected payload.
        tmp = tempfile.mkdtemp(prefix="tbra_run_")
        camp = _write_campaign(tmp)
        _write_strategy(tmp, "integ-fb-1", links_policy="attach")
        items = [{"id": "integ-fb-1", "name": "Juzzir",
                   "identifier": "facebook"}]
        rc, out, err = _run_run(
            [camp, "--strategy-by-integration", "integ-fb-1",
             "--strategy-base-dir", tmp],
            tmpdir=tmp, items=items)
        self.assertEqual(rc, 0, msg=f"err={err}")
        self.assertIn("INJECTED", out)


if __name__ == "__main__":
    unittest.main()
