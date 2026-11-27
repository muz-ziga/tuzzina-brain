"""Content-wheel tests: discovery list, deterministic mix, LRU,
scoped guard, composition mode and the ledger round-trip.

All offline: transport for the list adapter is patched, skills live
in a temp directory, models are mocks and time is explicit.
"""
import os
import sys
import json
import tempfile
import unittest
from dataclasses import asdict
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import g1.api_list as AL
from contracts import ExtractionResult, Identity, SourceItem
from g2 import generators as G
from g2.pipeline import build_package
from research.analysis import ContentOpportunity, MockAnalysisModel, \
    OpenAIAnalysisModel
from research.cycle import CollectionResult, _collect_api_list
from research.models import Finding, MockResearchModel, ResearchResult
from research.monitor import ClassifiedItem, MemoryStateStore, SourceState
from research.wheel import plan_next, select_lru
from stage_run import _guard_violations, run_analysis_stage, \
    run_research_stage, _to_jsonable, _wheel_select


NOW = "2026-11-26T12:00:00+00:00"


def source_item(**kw):
    data = dict(
        source_id="https://play.test/artist/1",
        source_type="api-list",
        source_url="https://play.test/artist/1",
        title="Alpha",
        text="source_id: https://play.test/artist/1 | name: Alpha",
        images=["https://media.test/a.webp"],
        links=[], hashtags=[], published_at="",
        platform="api-list", item_id="https://play.test/artist/1",
        content_hash="h", author="Alpha", content_class="artist",
    )
    data.update(kw)
    return SourceItem(**data)


class StrategySourceTest(unittest.TestCase):
    def test_campaign_wheel_reaches_resolved_policy(self):
        from channels.profile import resolve_strategy
        from channels.strategy import ChannelStrategy
        wheel = {"pattern": ["identity", "content"],
                 "content_classes": ["artist"]}
        cfg = resolve_strategy(
            {"wheel": wheel, "brand": {"name": "M"},
             "language": {"default": "ar"}},
            ChannelStrategy(integration_id="int-1"), {})
        self.assertEqual(cfg["wheel"], wheel)

    def test_api_list_and_class_are_preserved(self):
        from channels.strategy_store import _from_yaml_dict
        strategy = _from_yaml_dict({
            "integration_id": "int-1",
            "strategy": {"sources": [{
                "type": "api-list",
                "url": "https://api.test/list",
                "page_template": "https://play.test/{class}/{id}",
                "max_pages": 3,
                "csrf": True,
                "class": "catalogue",
                "n": 50,
                "text_keys": ["name", "description"],
                "nested_text_keys": ["artists"],
            }]},
        })
        source = strategy.sources[0]
        self.assertEqual(source["type"], "api-list")
        self.assertEqual(source["page_template"],
                         "https://play.test/{class}/{id}")
        self.assertEqual(source["max_pages"], 3)
        self.assertTrue(source["csrf"])
        self.assertEqual(source["class"], "catalogue")
        self.assertEqual(source["text_keys"], ["name", "description"])
        self.assertEqual(source["nested_text_keys"], ["artists"])

    def test_api_list_requires_page_template(self):
        from channels.strategy_store import _from_yaml_dict
        with self.assertRaises(ValueError):
            _from_yaml_dict({
                "integration_id": "int-1",
                "strategy": {"sources": [{
                    "type": "api-list",
                    "url": "https://api.test/list",
                }]},
            })

    def test_website_class_is_preserved(self):
        from channels.strategy_store import _from_yaml_dict
        strategy = _from_yaml_dict({
            "integration_id": "int-1",
            "strategy": {"sources": [{
                "type": "website",
                "url": "https://site.test/about",
                "class": "identity",
            }]},
        })
        self.assertEqual(strategy.sources[0]["class"], "identity")


class ApiListAdapterTest(unittest.TestCase):
    ROW_A = {
        "id": 1, "name": "Alpha", "model_type": "artist",
        "image_small": "https://media.test/a.webp",
        "created_at": "2026-01-01T00:00:00Z",
    }
    ROW_B = {
        "id": 2, "name": "Beta", "model_type": "album",
        "image": "https://media.test/b.webp",
        "release_date": "2020-09-25T00:00:00Z",
        "tracks_count": 8,
        "artists": [{"id": 1, "name": "Alpha",
                     "image_small": "https://media.test/a.webp"}],
    }
    ROW_DISABLED = {
        "id": 3, "name": "Hidden", "model_type": "artist",
        "disabled": True,
    }

    def _fake(self, url, config):
        if "page=2" in url:
            return ({"pagination": {"data": [self.ROW_B],
                                    "next_page": None}}, url)
        return ({"pagination": {"data": [self.ROW_A, self.ROW_DISABLED],
                                "next_page": 2}}, url)

    def test_pagination_rows_classes_and_pages(self):
        with mock.patch.object(AL, "_fetch_json", side_effect=self._fake):
            res = AL.ApiListAdapter().extract(
                "https://api.test/list", 50,
                config={"page_template":
                        "https://play.test/{class}/{id}",
                        "max_pages": 2,
                        "text_keys": ["name", "release_date",
                                      "tracks_count"],
                        "nested_text_keys": ["artists", "album"]})
        self.assertFalse(res.meta["partial"])
        self.assertEqual(len(res.items), 2)
        first, second = res.items
        self.assertEqual(first.item_id, "https://play.test/artist/1")
        self.assertEqual(first.content_class, "artist")
        self.assertEqual(first.images, ["https://media.test/a.webp"])
        self.assertEqual(first.author, "")
        self.assertIn("source_id: https://play.test/artist/1",
                      first.text)
        self.assertEqual(second.item_id, "https://play.test/album/2")
        self.assertEqual(second.content_class, "album")
        self.assertEqual(second.images[0], "https://media.test/b.webp")
        self.assertEqual(second.author, "Alpha")
        self.assertIn("artists: Alpha", second.text)
        self.assertIn("tracks_count: 8", second.text)

    def test_limit_stops_before_second_page(self):
        with mock.patch.object(AL, "_fetch_json", side_effect=self._fake) \
                as fetched:
            res = AL.ApiListAdapter().extract(
                "https://api.test/list", 1,
                config={"page_template":
                        "https://play.test/{class}/{id}",
                        "max_pages": 2})
        self.assertEqual(len(res.items), 1)
        self.assertEqual(fetched.call_count, 1)

    def test_single_object_fallback(self):
        with mock.patch.object(AL, "_fetch_json",
                               return_value=({"id": 9, "name": "Solo",
                                              "model_type": "track"},
                                             "https://api.test/solo")):
            res = AL.ApiListAdapter().extract(
                "https://api.test/solo", 5,
                config={"page_template":
                        "https://play.test/{class}/{id}"})
        self.assertEqual(len(res.items), 1)
        self.assertEqual(res.items[0].title, "Solo")
        self.assertEqual(res.items[0].content_class, "track")

    def test_generic_text_keys_are_config_driven(self):
        row = {"id": 5, "name": "Dish", "description": "Spicy",
               "price": 12, "type": "dish",
               "image": "https://media.test/dish.webp"}
        with mock.patch.object(AL, "_fetch_json",
                               return_value=({"pagination": {
                                   "data": [row],
                                   "next_page": None}}, "u")):
            res = AL.ApiListAdapter().extract(
                "https://api.test/dishes", 5,
                config={"page_template":
                        "https://site.test/{class}/{id}",
                        "class": "dish",
                        "text_keys": ["name", "description", "price"]})
        item = res.items[0]
        self.assertEqual(item.content_class, "dish")
        self.assertIn("price: 12", item.text)
        self.assertNotIn("artist:", item.text)
        self.assertNotIn("album:", item.text)

    def test_source_class_overrides_row_model_type(self):
        with mock.patch.object(AL, "_fetch_json",
                               return_value=({"pagination": {
                                   "data": [self.ROW_A],
                                   "next_page": None}}, "u")):
            res = AL.ApiListAdapter().extract(
                "https://api.test/list", 5,
                config={"page_template":
                        "https://play.test/{class}/{id}",
                        "class": "identity"})
        self.assertEqual(res.items[0].content_class, "identity")

    def test_known_item_stops_after_its_page(self):
        with mock.patch.object(AL, "_fetch_json", side_effect=self._fake) \
                as fetched:
            res = AL.ApiListAdapter().extract(
                "https://api.test/list", 50,
                config={"page_template":
                        "https://play.test/{class}/{id}",
                        "max_pages": 2},
                known_ids={"https://play.test/artist/1"})
        self.assertEqual([i.item_id for i in res.items],
                         ["https://play.test/artist/1"])
        self.assertEqual(fetched.call_count, 1)
        self.assertTrue(res.meta["scan_complete"])

    def test_cursor_stops_after_older_page(self):
        pages = {
            "https://api.test/list": ({
                "pagination": {"data": [{
                    "id": 1, "name": "New", "model_type": "artist",
                    "created_at": "2026-02-01T00:00:00Z",
                }], "next_page": 2}}, "u"),
            "https://api.test/list?page=2": ({
                "pagination": {"data": [{
                    "id": 2, "name": "Old", "model_type": "artist",
                    "created_at": "2026-01-01T00:00:00Z",
                }], "next_page": 3}}, "u"),
        }

        def fake(url, config):
            return pages[url]

        with mock.patch.object(AL, "_fetch_json", side_effect=fake) \
                as fetched:
            res = AL.ApiListAdapter().extract(
                "https://api.test/list", 50,
                config={"page_template":
                        "https://play.test/{class}/{id}",
                        "max_pages": 5},
                cursor="2026-01-15T00:00:00Z")
        self.assertEqual([i.title for i in res.items], ["New", "Old"])
        self.assertEqual(fetched.call_count, 2)
        self.assertTrue(res.meta["scan_complete"])


class CatalogueCacheTest(unittest.TestCase):
    SOURCE = {
        "type": "api-list", "url": "https://api.test/artists",
        "page_template": "https://play.test/{class}/{id}",
        "max_pages": 3, "class": "artist", "n": 50,
    }

    def _item(self, ident, title, created):
        page = "https://play.test/artist/%d" % ident
        return SourceItem(
            source_id=page, source_type="api-list", source_url=page,
            title=title, text="source_id: %s | name: %s" % (page, title),
            images=["https://media.test/%d.webp" % ident],
            published_at=created, platform="api-list", item_id=page,
            author="", content_class="artist")

    def test_incremental_page_keeps_full_cached_catalogue(self):
        a = self._item(1, "Alpha", "2026-02-01T00:00:00Z")
        b = self._item(2, "Beta", "2026-01-01T00:00:00Z")
        store = MemoryStateStore()
        with mock.patch.object(
                AL.ApiListAdapter, "extract",
                return_value=ExtractionResult(
                    [a, b], Identity(), {"partial": False})):
            _, first = _collect_api_list(
                self.SOURCE, 50, store=store, now=NOW)
        first.commit(store)
        self.assertEqual([i.title for i in first.catalogue], ["Alpha", "Beta"])
        self.assertIn("item", store.load(self.SOURCE["url"]).seen[a.item_id])
        with mock.patch.object(
                AL.ApiListAdapter, "extract",
                return_value=ExtractionResult(
                    [a], Identity(), {"partial": False})) as fetched:
            _, second = _collect_api_list(
                self.SOURCE, 50, store=store, now=NOW)
        self.assertEqual([i.title for i in second.catalogue],
                         ["Alpha", "Beta"])
        kwargs = fetched.call_args.kwargs
        self.assertEqual(set(kwargs["known_ids"]), {a.item_id, b.item_id})
        self.assertTrue(kwargs["cursor"])


class WheelPlanTest(unittest.TestCase):
    WHEEL = {
        "pattern": ["identity", "identity", "content",
                    "identity", "identity", "content",
                    "identity", "identity", "content", "identity"],
        "content_classes": ["artist", "track", "album"],
        "start_slot": 0,
    }

    def test_seven_identity_three_content_and_sub_rotation(self):
        history = []
        plans = []
        for i in range(10):
            plan = plan_next(history, self.WHEEL)
            plans.append(plan)
            history.insert(0, {
                "topic": "topic-%d" % i,
                "class": plan["class"],
                "wheel_slot": plan["slot"],
                "wheel_bucket": plan["bucket"],
                "item_ids": ["item-%d" % i],
                "at": "2026-11-26T12:%02d:00+00:00" % i,
            })
        buckets = [p["bucket"] for p in plans]
        self.assertEqual(buckets.count("identity"), 7)
        self.assertEqual(buckets.count("content"), 3)
        self.assertEqual(
            [p["class"] for p in plans if p["bucket"] == "content"],
            ["artist", "track", "album"])
        self.assertEqual(plan_next(history, self.WHEEL)["slot"], 0)

    def test_drafts_do_not_advance_the_wheel(self):
        history = [{
            "topic": "published", "class": "identity",
            "wheel_slot": 2, "wheel_bucket": "content",
            "item_ids": ["x"], "at": "2026-11-26T10:00:00+00:00",
        }, {
            "topic": "draft", "class": "identity",
            "wheel_slot": 3, "wheel_bucket": "identity",
            "state": "draft", "item_ids": ["y"],
            "at": "2026-11-26T11:00:00+00:00",
        }]
        plan = plan_next(history, self.WHEEL)
        self.assertEqual(plan["slot"], 3)

    def test_start_slot_can_open_on_content(self):
        wheel = dict(self.WHEEL, start_slot=2)
        plan = plan_next([], wheel)
        self.assertEqual(plan["bucket"], "content")
        self.assertEqual(plan["class"], "artist")

    def test_identity_bucket_can_accept_capability(self):
        wheel = dict(self.WHEEL, identity_classes=["identity", "capability"])
        plan = plan_next([], wheel)
        self.assertEqual(plan["class"], "identity")
        self.assertEqual(plan["accept"], ["identity", "capability"])


class LruTest(unittest.TestCase):
    def setUp(self):
        self.a = source_item(title="A", item_id="a", source_id="a",
                             source_url="a", author="A")
        self.b = source_item(title="B", item_id="b", source_id="b",
                             source_url="b", author="B")
        self.t1 = source_item(title="T1", item_id="t1", source_id="t1",
                              source_url="t1", content_class="track",
                              author="A")

    def test_unused_first_then_oldest(self):
        self.assertEqual(select_lru([self.a, self.b], [], "artist"), self.a)
        recent = [{"item_ids": ["a"], "at": "2026-01-01",
                   "class": "artist"}]
        self.assertEqual(select_lru([self.a, self.b], recent, "artist"),
                         self.b)
        recent = [
            {"item_ids": ["a"], "at": "2026-01-02", "class": "artist"},
            {"item_ids": ["b"], "at": "2026-01-01", "class": "artist"},
        ]
        self.assertEqual(select_lru([self.a, self.b], recent, "artist"),
                         self.b)

    def test_draft_does_not_consume_an_item(self):
        recent = [{"item_ids": ["a"], "at": "2026-01-01",
                   "state": "draft"}]
        self.assertEqual(select_lru([self.a, self.b], recent, "artist"),
                         self.a)

    def test_class_filter(self):
        self.assertEqual(select_lru([self.a, self.t1], [], "track"),
                         self.t1)
        self.assertIsNone(select_lru([self.a], [], "album"))

    def test_historical_topic_without_item_ids_still_consumes(self):
        old = source_item(title="متغيرة", item_id="alb-1",
                          source_id="alb-1", source_url="alb-1",
                          content_class="album")
        new = source_item(title="صدفة", item_id="alb-2",
                          source_id="alb-2", source_url="alb-2",
                          content_class="album")
        recent = [{"topic": "ألبوم متغيرة", "angle": "قديم",
                   "class": "album", "at": "2026-01-01"}]
        self.assertEqual(select_lru([old, new], recent, "album"), new)

    def test_exhausted_content_class_skips_to_next_class(self):
        track = source_item(title="قولولي", item_id="trk-1",
                            source_id="trk-1", source_url="trk-1",
                            content_class="track")
        pending = [CollectionResult(
            "pool", [ClassifiedItem(track, "UNCHANGED")],
            NOW, "", SourceState(source_id="pool"))]
        plan = {"slot": 8, "bucket": "content", "class": "album",
                "accept": ["album"],
                "content_classes": ["artist", "track", "album"]}
        out_plan, selected = _wheel_select(
            pending, [], {"fallback": False}, plan=plan)
        self.assertEqual(selected.title, "قولولي")
        self.assertEqual(out_plan["class"], "track")
        self.assertTrue(out_plan["fallback_content"])


class GuardTest(unittest.TestCase):
    def setUp(self):
        self.item = source_item(
            title="قولولي", author="دونبايا",
            text="source_id: x | artist: دونبايا | album: صدفة")
        self.guard = {
            "allowed_hosts": [], "block_unsourced_urls": True,
            "block_years": True, "require_item_terms": True,
            "forbidden_terms": ["متغيرة"],
        }

    def test_correct_work_and_artist_pass(self):
        text = "استمع لتراك «قولولي» لدونبايا على موزيقا"
        self.assertEqual(
            _guard_violations(text, self.guard, [self.item], None), [])

    def test_wrong_work_is_blocked_even_with_right_names(self):
        text = "استمع لتراك «قولولي» لدونبايا على ألبوم متغيرة"
        out = _guard_violations(text, self.guard, [self.item], None)
        self.assertIn("forbidden-term:متغيرة", out)

    def test_missing_title_and_artist_are_blocked(self):
        out = _guard_violations(
            "استمع لتراك آخر على موزيقا", self.guard, [self.item], None)
        self.assertIn("item-title-missing", out)
        self.assertIn("item-author-missing", out)


class CompositionModeTest(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get("BRAIN_SKILLS_DIR")
        self.tmp = tempfile.mkdtemp(prefix="tbra_wheel_img_")
        os.environ["BRAIN_SKILLS_DIR"] = self.tmp
        import yaml
        with open(os.path.join(self.tmp, "image.img.yaml"), "w",
                  encoding="utf-8") as f:
            yaml.safe_dump({
                "id": "image-img", "name": "img", "type": "image",
                "version": 1, "instructions": "Fixture image skill.",
                "config": {"composition": {
                    "mode": "auto",
                    "allowedHosts": ["media.test"],
                    "modeByClass": {
                        "artist": "cover-blur-card",
                        "track": "cover-blur-card",
                        "album": "cover-blur-card",
                        "identity": "brand-card",
                    },
                    "brand": {"pattern": "rings"},
                }},
            }, f)
        from skills.loader import clear_cache
        clear_cache()

    def tearDown(self):
        from skills.loader import clear_cache
        clear_cache()
        if self._old is None:
            os.environ.pop("BRAIN_SKILLS_DIR", None)
        else:
            os.environ["BRAIN_SKILLS_DIR"] = self._old

    def _build(self, content_class):
        item = source_item(content_class=content_class,
                           title="Work", text="artist: Alpha")
        policy = {
            "skills": {"image": "img"},
            "roles": ["image"],
            "brand": {"cta_style": "Go"},
            "language": {"default": "ar"},
            "hashtags": {"enabled": False},
            "links_policy": "hide",
            "media": {},
        }
        return build_package(
            item, {"cta_style": "Go"}, {"default": "ar"},
            {"enabled": False}, "hide", G.MockTextGenerator(),
            G.MockImageGenerator(), policy=policy)

    def test_work_uses_its_own_artwork(self):
        pkg = self._build("artist")
        media = pkg.media[0]
        self.assertEqual(media["kind"], "cover-card")
        self.assertEqual(media["composition"]["mode"], "cover-blur-card")
        self.assertEqual(media["source"], "https://media.test/a.webp")
        self.assertEqual(media["prompt"], "cover-blur-card")

    def test_identity_uses_brand_card(self):
        pkg = self._build("identity")
        media = pkg.media[0]
        self.assertEqual(media["composition"]["mode"], "brand-card")
        self.assertEqual(media["prompt"], "brand-card")


class LedgerTest(unittest.TestCase):
    class C:
        def __init__(self):
            self.state = {}

        def get_research_state(self, sid):
            return self.state.get(sid)

        def save_research_state(self, sid, value):
            self.state[sid] = value
            return value

    def test_wheel_fields_roundtrip_and_legacy_shape(self):
        from run_cycle import _record_published_topic, _recent_topics
        client = self.C()
        _record_published_topic(
            client, "work", "angle", "artist",
            item_ids=["https://play.test/artist/1"],
            state="draft", wheel_slot=3, wheel_bucket="content")
        out = _recent_topics(client)
        self.assertEqual(out[0]["item_ids"],
                         ["https://play.test/artist/1"])
        self.assertEqual(out[0]["state"], "draft")
        self.assertEqual(out[0]["wheel_slot"], 3)
        self.assertEqual(out[0]["wheel_bucket"], "content")
        client2 = self.C()
        _record_published_topic(client2, "legacy", "angle", "album")
        legacy = _recent_topics(client2)[0]
        self.assertNotIn("item_ids", legacy)
        self.assertNotIn("state", legacy)
        self.assertNotIn("wheel_slot", legacy)


class WheelResearchStageTest(unittest.TestCase):
    def _pending(self, items):
        return CollectionResult(
            "catalogue", [ClassifiedItem(item, "UNCHANGED")
                          for item in items], NOW, "",
            SourceState(source_id="catalogue"))

    def _ctx(self, items, cfg):
        class Client:
            def release_claim(self, key, run_id):
                pass

        class Capturing(MockResearchModel):
            def __init__(self):
                self.items = []

            def research(self, items, policy=None):
                self.items = list(items)
                return super().research(items, policy)

        model = Capturing()
        ctx = {
            "store": MemoryStateStore(),
            "cfg": cfg,
            "client": Client(),
            "run_id": "run-wheel",
            "dry_run": False,
            "sources": [
                {"type": "website", "url": "about",
                 "class": "identity"},
                {"type": "api-list", "url": "alpha",
                 "class": "artist", "index": True},
            ],
            "now": NOW,
            "claimer": None,
            "roles_resolved": [],
            "research_model": model,
        }
        return ctx, model

    def _run(self, items, cfg):
        ctx, model = self._ctx(items, cfg)
        with mock.patch(
                "research.cycle.collect_all_sources",
                side_effect=lambda sources, store, now, out, pending:
                pending.append(self._pending(items))):
            payload = run_research_stage(ctx)
        return payload, model

    def test_identity_slot_selects_one_identity_item_with_zero_new(self):
        identity = source_item(
            title="About", item_id="about", source_id="about",
            source_url="about", content_class="identity", images=[])
        artist = source_item(
            title="Alpha", item_id="alpha", source_id="alpha",
            source_url="alpha", content_class="artist")
        cfg = {"roles": ["research", "analysis", "text", "image"],
               "skills": {}, "recent_topics": [],
               "wheel": WheelPlanTest.WHEEL}
        payload, model = self._run([identity, artist], cfg)
        self.assertEqual(payload["items_new"], 0)
        self.assertEqual(len(payload["items"]), 1)
        self.assertEqual(payload["items"][0]["content_class"],
                         "identity")
        self.assertEqual(payload["research"]["meta"]["wheel"]["slot"], 0)
        self.assertIn("Alpha",
                      payload["research"]["meta"]["wheel"]["known_titles"])
        self.assertEqual(len(model.items), 1)
        self.assertEqual(model.items[0].title, "About")

    def test_content_slot_selects_artist(self):
        identity = source_item(
            title="About", item_id="about", source_id="about",
            source_url="about", content_class="identity", images=[])
        artist = source_item(
            title="Alpha", item_id="alpha", source_id="alpha",
            source_url="alpha", content_class="artist")
        cfg = {"roles": ["research", "analysis", "text", "image"],
               "skills": {}, "recent_topics": [],
               "wheel": dict(WheelPlanTest.WHEEL, start_slot=2)}
        payload, model = self._run([identity, artist], cfg)
        self.assertEqual(payload["items"][0]["content_class"], "artist")
        self.assertEqual(payload["research"]["meta"]["wheel"]["bucket"],
                         "content")
        self.assertEqual(model.items[0].title, "Alpha")

    def test_missing_class_is_a_config_error(self):
        artist = source_item(
            title="Alpha", item_id="alpha", source_id="alpha",
            source_url="alpha", content_class="artist")
        cfg = {"roles": ["research", "analysis", "text", "image"],
               "skills": {}, "recent_topics": [],
               "wheel": WheelPlanTest.WHEEL}
        ctx, _ = self._ctx([artist], cfg)
        ctx["sources"] = [{"type": "api-list", "url": "alpha",
                           "class": "artist"}]
        with self.assertRaisesRegex(ValueError,
                                    "wheel-source-class-missing:identity"):
            run_research_stage(ctx)


class AnalysisClassGateTest(unittest.TestCase):
    class Adapter:
        def __init__(self, outputs):
            self.outputs = list(outputs)
            self.calls = 0

        def complete(self, system, user, **kw):
            self.calls += 1
            return self.outputs.pop(0)

    def _body(self, content_class):
        return json.dumps({
            "eligible": True, "topic": "About", "angle": "Archive angle",
            "rationale": "R", "facts": ["fact"],
            "source_item_ids": ["about"], "content_format": "post",
            "content_class": content_class, "media_intent": "none",
            "audience": "a", "language": "ar", "priority": "normal",
            "confidence": "high", "constraints": [],
        })

    def _research(self):
        finding = Finding(statement="About", kind="fact",
                          confidence="high", item_ids=["about"],
                          excerpt="About")
        return ResearchResult(
            summary="About", findings=[finding], topics=["About"],
            entities=[], item_ids=["about"], earliest_published="",
            latest_published="", model="mock", meta={})

    def _policy(self):
        return {
            "brand": {"tone": "t", "audience": "a"},
            "language": {"default": "ar"}, "pillars": [], "media": {},
            "wheel_class": "identity",
            "wheel_accept": ["identity", "capability"],
        }

    def test_capability_is_accepted_in_identity_slot(self):
        adapter = self.Adapter([self._body("capability")])
        model = OpenAIAnalysisModel(adapter=adapter)
        out = model.analyze(self._research(), self._policy(),
                            [source_item(item_id="about",
                                         source_id="about",
                                         source_url="about",
                                         content_class="identity")])
        self.assertEqual(out.content_class, "capability")
        self.assertEqual(adapter.calls, 1)

    def test_wrong_class_retries_then_identity(self):
        adapter = self.Adapter([
            self._body("artist"), self._body("identity")])
        model = OpenAIAnalysisModel(adapter=adapter)
        out = model.analyze(self._research(), self._policy(),
                            [source_item(item_id="about",
                                         source_id="about",
                                         source_url="about",
                                         content_class="identity")])
        self.assertEqual(out.content_class, "identity")
        self.assertEqual(adapter.calls, 2)


class WheelAnalysisStageTest(unittest.TestCase):
    def test_wheel_class_reaches_model_and_opportunity(self):
        class Analysis:
            def __init__(self):
                self.policy = None
                self.items = None

            def analyze(self, result, policy, items=None):
                self.policy = policy
                self.items = items
                return ContentOpportunity(
                    eligible=True, topic="About", angle="Archive angle",
                    rationale="R", facts=["fact"],
                    source_item_ids=["about"], content_format="post",
                    content_class="identity", media_intent="none",
                    audience="a", language="ar", priority="normal",
                    confidence="high", constraints=[], model="fake",
                    meta={})

        item = source_item(
            title="About", item_id="about", source_id="about",
            source_url="about", content_class="identity", images=[])
        wheel = {"slot": 0, "bucket": "identity", "class": "identity",
                 "accept": ["identity", "capability"],
                 "window": 10, "pattern": ["identity"],
                 "content_classes": ["artist"]}
        research = {
            "summary": "", "findings": [], "topics": [], "entities": [],
            "item_ids": [], "earliest_published": "",
            "latest_published": "", "model": "", "meta": {"wheel": wheel},
        }
        model = Analysis()
        ctx = {
            "client": None, "run_id": "r", "dry_run": True,
            "cfg": {"roles": ["analysis"], "skills": {}},
            "research": research, "items": [_to_jsonable(item)],
            "claimed": [], "now": NOW, "analysis_model": model,
        }
        payload = run_analysis_stage(ctx)
        self.assertEqual(model.policy["wheel_class"], "identity")
        self.assertEqual(model.policy["wheel_accept"],
                         ["identity", "capability"])
        self.assertEqual(payload["opportunity"]["meta"]["wheel"]["slot"], 0)
        self.assertEqual(payload["opportunity"]["content_class"],
                         "identity")


if __name__ == "__main__":
    unittest.main()
