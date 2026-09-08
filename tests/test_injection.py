"""Injection layer tests. All Tuzzina contact goes through a fake
client (in-memory); no network, no production, no R2, no DB."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from adapters.base import UnsupportedPlatform
from injection.errors import InvalidInjectionIntent
from injection.intent import CanonicalIntent, build_intent
from injection.service import InjectionService


FB_RECORD = {"id": "int-fb-1", "name": "Juzzir",
             "identifier": "facebook", "picture": "p"}
IG_RECORD = {"id": "int-ig-1", "name": "Juzzir IG",
             "identifier": "instagram", "picture": "p"}
TT_RECORD = {"id": "int-tt-1", "name": "Juzzir TT",
             "identifier": "tiktok", "picture": "p"}


class FakeClient:
    """Duck-typed TuzzinaClient. Records calls; upload methods raise
    because the service must never upload (upload precedes intent)."""

    def __init__(self, records):
        self._records = {r["id"]: r for r in records}
        self.posts_calls = []

    def get_integration(self, integration_id):
        try:
            return self._records[integration_id]
        except KeyError:
            from tuzzina.client import TuzzinaError
            raise TuzzinaError(
                f"integration {integration_id!r} not found in Tuzzina")

    def integrations(self):
        return list(self._records.values())

    def create_post(self, posts, date, post_type="draft"):
        self.posts_calls.append({"posts": posts, "date": date,
                                 "type": post_type})
        return [{"postId": "p1"}]

    def create_draft(self, posts, date):
        return self.create_post(posts, date, post_type="draft")

    def upload_from_url(self, url):
        raise AssertionError("service must never upload")

    def upload_bytes(self, data, filename, mime):
        raise AssertionError("service must never upload")


def fb_intent(**kw):
    d = dict(integration_id="int-fb-1", content="Hello world",
             media=[{"id": "m1", "path": "https://cdn.test/a.jpg"}],
             publish_at="2026-09-08T09:00:00+00:00", mode="draft")
    d.update(kw)
    return build_intent(**d)


class SelectionTest(unittest.TestCase):
    def test_integration_selection_by_id(self):
        c = FakeClient([FB_RECORD, IG_RECORD])
        out = InjectionService().inject(fb_intent(), c)
        self.assertEqual(out["integration_id"], "int-fb-1")
        self.assertEqual(len(c.posts_calls), 1)
        self.assertEqual(c.posts_calls[0]["posts"][0]["integration"],
                         {"id": "int-fb-1"})

    def test_correct_adapter_selection_fb(self):
        c = FakeClient([FB_RECORD])
        out = InjectionService().inject(fb_intent(), c)
        self.assertEqual(out["identifier"], "facebook")
        settings = c.posts_calls[0]["posts"][0]["settings"]
        self.assertEqual(settings["__type__"], "facebook")

    def test_correct_adapter_selection_ig(self):
        c = FakeClient([IG_RECORD])
        intent = fb_intent(integration_id="int-ig-1")
        out = InjectionService().inject(intent, c)
        self.assertEqual(out["identifier"], "instagram")
        settings = c.posts_calls[0]["posts"][0]["settings"]
        self.assertEqual(settings["__type__"], "instagram")

    def test_stale_integration_protection(self):
        c = FakeClient([FB_RECORD])
        from tuzzina.client import TuzzinaError
        with self.assertRaises(TuzzinaError):
            InjectionService().inject(
                fb_intent(integration_id="dead-id"), c)
        self.assertEqual(c.posts_calls, [])


class MappingTest(unittest.TestCase):
    def test_facebook_mapping(self):
        c = FakeClient([FB_RECORD])
        InjectionService().inject(
            fb_intent(hashtags=["#a", "#b"], mentions=["page"],
                      link="https://src.test/x", links_policy="attach",
                      post_kind="post", mode="schedule",
                      settings={"post_type": "post"}), c)
        post = c.posts_calls[0]["posts"][0]
        self.assertEqual(post["settings"]["__type__"], "facebook")
        self.assertEqual(post["settings"]["post_type"], "post")
        self.assertEqual(post["settings"]["url"], "https://src.test/x")
        self.assertIn("@page", post["value"][0]["content"])
        self.assertEqual(post["value"][0]["image"],
                         [{"id": "m1", "path": "https://cdn.test/a.jpg"}])
        self.assertEqual(c.posts_calls[0]["type"], "schedule")

    def test_instagram_mapping(self):
        c = FakeClient([IG_RECORD])
        InjectionService().inject(
            fb_intent(integration_id="int-ig-1",
                      hashtags=["#a"], post_kind="post"), c)
        post = c.posts_calls[0]["posts"][0]
        self.assertEqual(post["settings"]["__type__"], "instagram")
        self.assertEqual(post["settings"]["post_type"], "post")
        self.assertLessEqual(len(post["value"][0]["content"]), 2200)

    def test_instagram_links_omitted(self):
        c = FakeClient([IG_RECORD])
        InjectionService().inject(
            fb_intent(integration_id="int-ig-1",
                      link="https://src.test/x",
                      links_policy="attach"), c)
        post = c.posts_calls[0]["posts"][0]
        self.assertNotIn("url", post["settings"])
        self.assertNotIn("https://src.test/x",
                         post["value"][0]["content"])

    def test_facebook_links(self):
        c = FakeClient([FB_RECORD])
        InjectionService().inject(
            fb_intent(link="https://src.test/x",
                      links_policy="attach"), c)
        post = c.posts_calls[0]["posts"][0]
        self.assertEqual(post["settings"]["url"], "https://src.test/x")

    def test_provider_settings_merged_identity_forced(self):
        c = FakeClient([FB_RECORD])
        InjectionService().inject(
            fb_intent(settings={"post_type": "post",
                                "__type": "EVIL",
                                "custom": "1"}), c)
        settings = c.posts_calls[0]["posts"][0]["settings"]
        self.assertEqual(settings["__type__"], "facebook")
        self.assertEqual(settings["custom"], "1")

    def test_scheduled_intent(self):
        c = FakeClient([FB_RECORD])
        out = InjectionService().inject(
            fb_intent(mode="schedule",
                      publish_at="2026-09-08T09:00:00+00:00"), c)
        self.assertEqual(out["mode"], "schedule")
        self.assertEqual(c.posts_calls[0]["type"], "schedule")
        self.assertEqual(c.posts_calls[0]["date"],
                         "2026-09-08T09:00:00+00:00")

    def test_immediate_intent(self):
        c = FakeClient([FB_RECORD])
        out = InjectionService().inject(fb_intent(mode="now"), c)
        self.assertEqual(out["mode"], "now")
        self.assertEqual(c.posts_calls[0]["type"], "now")

    def test_story(self):
        c = FakeClient([FB_RECORD])
        InjectionService().inject(
            fb_intent(post_kind="story",
                      media=[{"id": "m1",
                              "path": "https://cdn.test/a.jpg"}]), c)
        settings = c.posts_calls[0]["posts"][0]["settings"]
        self.assertEqual(settings["post_type"], "story")

    def test_media_refs_preserved(self):
        c = FakeClient([FB_RECORD])
        media = [{"id": "a", "path": "https://cdn.test/a.jpg"},
                 {"id": "b", "path": "https://cdn.test/b.png"}]
        InjectionService().inject(fb_intent(media=media), c)
        self.assertEqual(c.posts_calls[0]["posts"][0]["value"][0]["image"],
                         media)


class RefusalTest(unittest.TestCase):
    def test_unsupported_platform(self):
        c = FakeClient([TT_RECORD])
        with self.assertRaises(UnsupportedPlatform):
            InjectionService().inject(
                fb_intent(integration_id="int-tt-1"), c)
        self.assertEqual(c.posts_calls, [])

    def test_story_without_media_flows_to_tuzzina(self):
        # STEP 4: Brain performs no provider refusal. A text-only
        # story is translated and sent; Tuzzina's POST validation
        # owns the refusal and its error propagates from the client.
        c = FakeClient([FB_RECORD])
        out = InjectionService().inject(
            fb_intent(post_kind="story", media=[]), c)
        self.assertEqual(out["post_kind"], "story")
        settings = c.posts_calls[0]["posts"][0]["settings"]
        self.assertEqual(settings["post_type"], "story")

    def test_invalid_intent_empty_content(self):
        c = FakeClient([FB_RECORD])
        with self.assertRaises(InvalidInjectionIntent):
            InjectionService().inject(fb_intent(content="   "), c)

    def test_invalid_intent_bad_mode(self):
        c = FakeClient([FB_RECORD])
        with self.assertRaises(InvalidInjectionIntent):
            InjectionService().inject(fb_intent(mode="later"), c)

    def test_invalid_intent_bad_post_kind(self):
        c = FakeClient([FB_RECORD])
        with self.assertRaises(InvalidInjectionIntent):
            InjectionService().inject(fb_intent(post_kind="reel"), c)

    def test_invalid_intent_bad_media(self):
        c = FakeClient([FB_RECORD])
        with self.assertRaises(InvalidInjectionIntent):
            InjectionService().inject(
                fb_intent(media=[{"noid": 1}]), c)

    def test_link_setting_translation(self):
        # The adapter (not a capability table) decides the link
        # payload shape: FB carries settings.url on attach.
        from adapters.facebook import FacebookAdapter
        from adapters.instagram import InstagramAdapter
        self.assertEqual(
            FacebookAdapter().link_setting("attach", "https://x"),
            {"url": "https://x"})
        self.assertEqual(
            InstagramAdapter().link_setting("attach", "https://x"), {})


class OwnershipTest(unittest.TestCase):
    def test_hashtags_verbatim_exactly_once(self):
        c = FakeClient([FB_RECORD])
        InjectionService().inject(
            fb_intent(hashtags=["#one", "#two"]), c)
        text = c.posts_calls[0]["posts"][0]["value"][0]["content"]
        for tag in ("#one", "#two"):
            self.assertEqual(text.count(tag), 1, tag)

    def test_mentions_inline(self):
        c = FakeClient([FB_RECORD])
        InjectionService().inject(
            fb_intent(mentions=["page", "@other"]), c)
        text = c.posts_calls[0]["posts"][0]["value"][0]["content"]
        self.assertIn("@page", text)
        self.assertIn("@other", text)
        self.assertNotIn("@@", text)

    def test_no_duplicate_hashtags(self):
        c = FakeClient([IG_RECORD])
        InjectionService().inject(
            fb_intent(integration_id="int-ig-1",
                      hashtags=["#x", "#y"]), c)
        text = c.posts_calls[0]["posts"][0]["value"][0]["content"]
        for tag in ("#x", "#y"):
            self.assertEqual(text.count(tag), 1, tag)


class GuardTest(unittest.TestCase):
    def _src(self, rel):
        import pathlib
        base = pathlib.Path(__file__).parent.parent / "src" / "injection"
        return (base / rel).read_text(encoding="utf-8")

    def _code_lines(self, rel):
        # Code only: drop full-line comments and module/function
        # docstring lines so documentation words ("never touches
        # R2") don't trip the guards. Guards target constructs.
        out = []
        in_doc = False
        for ln in self._src(rel).splitlines():
            s = ln.strip()
            if s.startswith('"""') or s.startswith("'''"):
                if s.count('"""') == 2 or s.count("'''") == 2:
                    continue
                in_doc = not in_doc
                continue
            if in_doc or s.startswith("#"):
                continue
            out.append(ln)
        return "\n".join(out)

    def _imports_of(self, rel):
        lines = self._code_lines(rel).splitlines()
        return "\n".join(
            ln for ln in lines
            if ln.strip().startswith(("import ", "from ")))

    def test_no_tuzzina_postiz_imports(self):
        for rel in ("intent.py", "errors.py",
                    "service.py", "__init__.py"):
            blob = self._imports_of(rel)
            for bad in ("gitroom", "postiz", "tuzzina", "nestjs"):
                self.assertNotIn(bad, blob, rel)

    def test_no_db_r2_access(self):
        for rel in ("intent.py", "errors.py",
                    "service.py"):
            src = self._code_lines(rel).lower()
            for bad in ("psycopg", "sqlalchemy", "sqlite", "boto3",
                        "cloudflarestorage", "r2.", "redis",
                        "put_object", "get_object"):
                self.assertNotIn(bad, src, rel)

    def test_no_scheduler_publisher(self):
        for rel in ("intent.py", "errors.py",
                    "service.py"):
            src = self._src(rel)
            for bad in ("apscheduler", "celery", "Temporal", "temporal",
                        "threading", "time.sleep", "import sched",
                        "from sched", "queue.Queue", "def publish",
                        "class Publisher", "class Scheduler"):
                self.assertNotIn(bad, src, rel)

    def test_no_name_based_selection(self):
        src = self._src("service.py")
        for bad in ("find_integration", "name_contains", "first(",
                    "match_name", "match by name", "fuzzy"):
            self.assertNotIn(bad, src)

    def test_adapters_do_no_http(self):
        import pathlib
        base = pathlib.Path(__file__).parent.parent / "src" / "adapters"
        for name in ("base.py", "facebook.py", "instagram.py",
                     "__init__.py"):
            lines = (base / name).read_text(encoding="utf-8").splitlines()
            blob = "\n".join(
                ln for ln in lines
                if ln.strip().startswith(("import ", "from ")))
            for bad in ("urllib", "requests", "httpx", "http.client",
                        "urlopen"):
                self.assertNotIn(bad, blob, name)


if __name__ == "__main__":
    unittest.main()
