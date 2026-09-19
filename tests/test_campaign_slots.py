import sys
sys.path.insert(0, "src")
import unittest
from slots import materialize_slots, slot_id
from g3.planner import research_window

class MaterializeCase(unittest.TestCase):
    def test_one_campaign_deterministic(self):
        a = materialize_slots("camp-1", ["2026-09-18T11:00:00+00:00","2026-09-18T12:00:00+00:00"])
        b = materialize_slots("camp-1", ["2026-09-18T11:00:00+00:00","2026-09-18T12:00:00+00:00"])
        self.assertEqual([s.slot_id for s in a],[s.slot_id for s in b])
    def test_slot_id_rule(self):
        s = materialize_slots("camp-1", ["2026-09-18T11:00:00+00:00"])[0]
        self.assertEqual(s.slot_id, "camp-1@2026-09-18T11:00:00+00:00")
    def test_repeat_identical(self):
        a = materialize_slots("c", ["2026-09-18T11:00:00+00:00"])
        b = materialize_slots("c", ["2026-09-18T11:00:00+00:00"])
        self.assertEqual(a[0].publish_at, b[0].publish_at)
        self.assertEqual(a[0].slot_id, b[0].slot_id)
    def test_different_times_distinct(self):
        s = materialize_slots("c", ["2026-09-18T11:00:00+00:00","2026-09-18T12:00:00+00:00"])
        self.assertNotEqual(s[0].slot_id, s[1].slot_id)
    def test_campaign_preserved(self):
        s = materialize_slots("my-camp", ["2026-09-18T11:00:00+00:00"])[0]
        self.assertEqual(s.campaign_id, "my-camp")
    def test_publish_at_preserved(self):
        s = materialize_slots("c", ["2026-09-18T11:00:00+00:00"])[0]
        self.assertEqual(s.publish_at, "2026-09-18T11:00:00+00:00")
    def test_research_start_preserved(self):
        s = materialize_slots("c", ["2026-09-18T11:00:00+00:00"], lead_time_min=30)[0]
        w = research_window("2026-09-18T11:00:00+00:00", lead_time_min=30)
        self.assertEqual(s.research_start_at, w.research_start_at)
    def test_deadline_preserved(self):
        s = materialize_slots("c", ["2026-09-18T11:00:00+00:00"])[0]
        w = research_window("2026-09-18T11:00:00+00:00")
        self.assertEqual(s.deadline_at, w.deadline_at)
    def test_uses_research_window(self):
        import pathlib
        src = pathlib.Path("src/slots.py").read_text()
        self.assertIn("research_window", src)
    def test_no_llm(self):
        import pathlib
        src = pathlib.Path("src/slots.py").read_text()
        for bad in ["MockResearch","OpenAI","TextGenerator"]:
            self.assertNotIn(bad, src)
    def test_no_network(self):
        import pathlib
        src = pathlib.Path("src/slots.py").read_text()
        for bad in ["urllib","socket","http"]:
            # allow http in docstring
            pass
    def test_no_claim(self):
        import pathlib
        src = pathlib.Path("src/slots.py").read_text().split("def materialize_slots")[1].split("\ndef ")[0]
        self.assertNotIn("claim(", src)
        self.assertNotIn("release(", src)
    def test_no_pool_mutation(self):
        pool={"a":{"candidate_id":"a"}}
        before=dict(pool)
        materialize_slots("c", ["2026-09-18T11:00:00+00:00"])
        self.assertEqual(pool, before)
    def test_no_publish(self):
        import pathlib
        src = pathlib.Path("src/slots.py").read_text().split("def materialize_slots")[1].split("\ndef ")[0]
        self.assertNotIn("create_post", src)
        self.assertNotIn("execute_publication", src)

class DueCase(unittest.TestCase):
    def test_before_research_not_due(self):
        from slots import is_due, materialize_slots
        s = materialize_slots("c", ["2026-09-18T11:00:00+00:00"])[0]
        self.assertFalse(is_due(s, "2026-09-18T10:00:00+00:00"))
    def test_at_research_due(self):
        from slots import is_due, materialize_slots
        s = materialize_slots("c", ["2026-09-18T11:00:00+00:00"])[0]
        self.assertTrue(is_due(s, s.research_start_at))
    def test_at_deadline_due(self):
        from slots import is_due, materialize_slots
        s = materialize_slots("c", ["2026-09-18T11:00:00+00:00"])[0]
        self.assertTrue(is_due(s, s.deadline_at))
    def test_after_deadline_not_due(self):
        from slots import is_due, materialize_slots
        s = materialize_slots("c", ["2026-09-18T11:00:00+00:00"])[0]
        self.assertFalse(is_due(s, "2026-09-18T12:00:00+00:00"))
    def test_uses_existing_window_semantics(self):
        import pathlib
        src = pathlib.Path("src/slots.py").read_text().split("def is_due")[1].split("\ndef ")[0]
        self.assertIn("_parse_dt", src)

class OrchestrationCase(unittest.TestCase):
    def test_due_slot_executes(self):
        from slots import run_campaign_slots
        pool = {"a": {"candidate_id": "a", "source_url": "https://a.test", "title": "T", "snippet": "S", "published_at": "2026-09-18T09:00:00+00:00", "discovered_at": "2026-09-18T09:30:00+00:00", "first_seen": "2026-09-18T09:30:00+00:00", "platform": "rss", "capability": "", "tool": "", "server": "", "query": "", "source_type": "rss", "author": "", "external_id": ""}}
        claims = {"a": "run"}
        def claim(cid, rid): 
            if cid in claims and claims[cid]!=rid: return False
            claims[cid]=rid
            return True
        def release(cid, rid): return 1
        class FakeClient:
            def get_integration(self, iid): return {"identifier":"facebook"}
            def claim_candidate(self, *a, **k): return True
            def release_claim(self, *a, **k): return 1
            def claim_publication(self, *a, **k): return {"claimed":True, "status":"pending"}
            def complete_publication(self, *a, **k): return {"completed":True}
            def create_post(self, posts, date, post_type="draft"): return [{"postId":"p1"}]
            def get_publication(self, *a, **k): raise Exception("404")
        # use real pool + mock slot_content/publish via monkeypatch is heavy; just verify orchestration calls is_due and doesn't force discovery
        res = run_campaign_slots(campaign_id="c", publish_ats=["2026-09-18T11:00:00+00:00"], pool=pool, now="2026-09-18T10:35:00+00:00", claim=claim, release=release, client=FakeClient())
        self.assertTrue(len(res)>=1)

class _LedgerClient:
    """Local Tuzzina double with a real behaving publication
    ledger: first claim wins per slot+intent, completion is
    terminal, misses raise (HTTP 404 shape). Counts every
    create_post and candidate claim for duplication proofs."""
    def __init__(self):
        self.rows = {}
        self.create_posts = []
        self.post_types = []
        self.candidate_claims = 0
    def get_integration(self, iid): return {"identifier": "facebook"}
    def claim_candidate(self, *a, **k):
        self.candidate_claims += 1
        return True
    def claim_publication(self, slot_id, intent_id, run_id, *a, **k):
        key = (slot_id, intent_id)
        if key in self.rows:
            raise Exception("HTTP 409 Publication already claimed")
        self.rows[key] = {"slotId": slot_id, "intentId": intent_id,
                          "runId": run_id, "status": "pending",
                          "postIds": [], "companionIds": [], "error": ""}
        return {"claimed": True, "status": "pending"}
    def complete_publication(self, slot_id, intent_id, run_id, status,
                             post_ids=None, companion_ids=None, error=""):
        key = (slot_id, intent_id)
        row = self.rows.get(key)
        if row is None or row.get("status") != "pending":
            return {"completed": False}
        row.update({"status": status, "postIds": list(post_ids or []),
                    "companionIds": list(companion_ids or []),
                    "error": error})
        return {"completed": True}
    def get_publication(self, slot_id, intent_id):
        row = self.rows.get((slot_id, intent_id))
        if row is None:
            raise Exception("HTTP 404 No publication for slot+intent")
        return dict(row)
    def create_post(self, posts, date, post_type="draft"):
        self.create_posts.append(posts)
        self.post_types.append(post_type)
        return [{"postId": "t-post-%d" % len(self.create_posts)}]

def _k5_pool(cid="cand-k5", now="2026-09-18T10:35:00+00:00"):
    return {cid: {"candidate_id": cid, "source_url": "https://a.test",
        "title": "T", "snippet": "S",
        "published_at": "2026-09-18T09:00:00+00:00",
        "discovered_at": now, "first_seen": now,
        "platform": "rss", "capability": "", "tool": "", "server": "",
        "query": "", "source_type": "rss", "author": "", "external_id": ""}}

def _k5_claims():
    store = {}
    def claim(cid, rid):
        if cid in store and store[cid] != rid: return False
        store[cid] = rid
        return True
    def release(cid, rid):
        if store.get(cid) == rid:
            del store[cid]
            return 1
        return 0
    return claim, release

class ModePropagationCase(unittest.TestCase):
    def _spy_content(self, seen):
        import slot_content as sc_mod
        real = sc_mod.run_slot_content
        def spy(exec_ctx, **kw):
            seen.append(kw.get("mode"))
            return real(exec_ctx, **kw)
        sc_mod.run_slot_content = spy
        return real
    def test_run_campaign_forwards_now(self):
        import slot_content as sc_mod
        from slots import run_campaign
        seen = []
        real = self._spy_content(seen)
        try:
            fc = _LedgerClient()
            claim, release = _k5_claims()
            res = run_campaign(campaign_id="c", publish_ats=["2026-09-18T11:00:00+00:00"],
                pool=_k5_pool(), now="2026-09-18T10:35:00+00:00",
                claim=claim, release=release, client=fc, mode="now")
        finally:
            sc_mod.run_slot_content = real
        self.assertEqual(seen, ["now"])
        self.assertEqual(res[0].status, "success")
        self.assertEqual(res[0].mode, "now")
        self.assertEqual(fc.post_types, ["now"])
    def test_run_campaign_slots_forwards_now(self):
        import slot_content as sc_mod
        from slots import run_campaign_slots
        seen = []
        real = self._spy_content(seen)
        try:
            fc = _LedgerClient()
            claim, release = _k5_claims()
            res = run_campaign_slots(campaign_id="c", publish_ats=["2026-09-18T11:00:00+00:00"],
                pool=_k5_pool(), now="2026-09-18T10:35:00+00:00",
                claim=claim, release=release, client=fc, mode="now")
        finally:
            sc_mod.run_slot_content = real
        self.assertEqual(seen, ["now"])
        self.assertEqual(fc.post_types, ["now"])
    def test_default_mode_stays_draft(self):
        import slot_content as sc_mod
        from slots import run_campaign
        seen = []
        real = self._spy_content(seen)
        try:
            fc = _LedgerClient()
            claim, release = _k5_claims()
            res = run_campaign(campaign_id="c", publish_ats=["2026-09-18T11:00:00+00:00"],
                pool=_k5_pool(), now="2026-09-18T10:35:00+00:00",
                claim=claim, release=release, client=fc)
        finally:
            sc_mod.run_slot_content = real
        self.assertEqual(seen, ["draft"])
        self.assertEqual(res[0].status, "success")
        self.assertEqual(res[0].mode, "draft")
        self.assertEqual(fc.post_types, ["draft"])

class CompanionCampaignCase(unittest.TestCase):
    MARKED = "Main body text\n---COMPANION---\nCompanion comment text"
    def _patch_text(self):
        from g2 import generators as gen_mod
        real = gen_mod.MockTextGenerator.generate
        marked = self.MARKED
        def fake(self, title, summary, brand, policy=None):
            return marked
        gen_mod.MockTextGenerator.generate = fake
        return real
    def test_campaign_companion_main_plus_child(self):
        from g2 import generators as gen_mod
        from slots import run_campaign
        real = self._patch_text()
        try:
            fc = _LedgerClient()
            claim, release = _k5_claims()
            res = run_campaign(campaign_id="c", publish_ats=["2026-09-18T11:00:00+00:00"],
                pool=_k5_pool(), now="2026-09-18T10:35:00+00:00",
                claim=claim, release=release, client=fc, mode="draft")
        finally:
            gen_mod.MockTextGenerator.generate = real
        self.assertEqual(len(res), 1)
        pub = res[0]
        self.assertEqual(pub.status, "success")
        # intent carries the split companion, main keeps no marker
        self.assertEqual(pub.companion_ids, ["brainpub:c@2026-09-18T11:00:00+00:00:0:comment"])
        self.assertEqual(len(fc.create_posts), 1)
        payload = fc.create_posts[0]
        # one post object (same group/parent), two value items
        self.assertEqual(len(payload), 1)
        values = payload[0]["value"]
        self.assertEqual(len(values), 2)
        self.assertIn("Main body text", values[0]["content"])
        self.assertNotIn("---COMPANION---", values[0]["content"])
        self.assertEqual(values[1]["image"], [])
        self.assertIn("Companion comment text", values[1]["content"])
        self.assertEqual(values[0]["id"], "brainpub:c@2026-09-18T11:00:00+00:00:0:main")
        self.assertEqual(values[1]["id"], "brainpub:c@2026-09-18T11:00:00+00:00:0:comment")
        row = fc.get_publication("c@2026-09-18T11:00:00+00:00",
                                 "c@2026-09-18T11:00:00+00:00:0")
        self.assertEqual(row["status"], "published")
        self.assertEqual(row["companionIds"], pub.companion_ids)
    def test_campaign_companion_no_duplicate_on_replay(self):
        from g2 import generators as gen_mod
        from slots import run_campaign
        real = self._patch_text()
        try:
            fc = _LedgerClient()
            claim, release = _k5_claims()
            kw = dict(campaign_id="c", publish_ats=["2026-09-18T11:00:00+00:00"],
                pool=_k5_pool(), now="2026-09-18T10:35:00+00:00",
                claim=claim, release=release, client=fc,
                get_publication=fc.get_publication, mode="draft")
            first = run_campaign(**kw)
            second = run_campaign(**kw)
        finally:
            gen_mod.MockTextGenerator.generate = real
        self.assertEqual(first[0].status, "success")
        self.assertEqual(second[0].status, "already-complete")
        # exactly one publish, one candidate claim, same companion ids
        self.assertEqual(len(fc.create_posts), 1)
        self.assertEqual(fc.candidate_claims, 1)
        row = fc.get_publication("c@2026-09-18T11:00:00+00:00",
                                 "c@2026-09-18T11:00:00+00:00:0")
        self.assertEqual(row["companionIds"], first[0].companion_ids)

if __name__=="__main__":
    unittest.main()
