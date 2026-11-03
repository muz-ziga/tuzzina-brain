"""Story add-on tests for the execute stage.

Proves, against the real stage code:
- with the campaign doc enabling story, the SAME generated image is
  injected as a story on the primary channel AND every distribution
  target, after the posts;
- a text-only run ships no story (stories need media);
- a story failure on one channel is logged and never fails the stage
  (the posts have already shipped);
- with story absent (default), posts ship byte-identically and no
  story call is ever made.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from stage_run import run_execute_stage

ALL = {"text": 1, "text+image": 1, "text+video": 1, "link+text": 1}
NO_IMAGE_ROLE = ["research", "analysis", "text"]
IMAGE_ROLE = NO_IMAGE_ROLE + ["image"]


def _opportunity(media_intent):
    return {"eligible": True, "topic": "T", "angle": "A",
            "rationale": "R", "facts": ["a truthful fact"],
            "source_item_ids": [], "content_format": "post",
            "media_intent": media_intent, "audience": "a",
            "language": "en", "priority": "normal",
            "confidence": "medium", "constraints": []}


def _ctx(media_intent, roles=None, campaign=None, client=None,
         dry_run=False, targets=None):
    ctx = {
        "client": client, "run_id": "r-story",
        "cfg": {"brand": {"tone": "direct", "audience": "a"},
                "language": {"default": "en"},
                "channel_meta": {"identifier": "facebook"},
                "pillars": [], "generation": {}, "media": {},
                "hashtags": {"enabled": True, "max": 3},
                "links_policy": "hide", "schedule": {},
                "roles": list(roles),
                "publish_targets": list(targets or []),
                "distribution": {"formats": dict(ALL), "enabled": True}},
        "research": None, "items": [], "claimed": [],
        "dry_run": dry_run, "mode": "draft",
        "integration_id": "int-1", "schedule": {}, "now": "t",
        "text": "Post words here.", "prompt": None,
        "opportunity": _opportunity(media_intent),
        "campaign": campaign or {},
    }
    return ctx


class _LiveClient:
    def __init__(self, image_ref=None):
        self.calls = []
        self.posts_calls = []
        self._image_ref = image_ref

    def campaign_content_exists(self, run_id, content, integration_id="",
                                value_id=""):
        return {"exists": False, "sameValue": False}

    def release_claim(self, key, run_id):
        pass

    def get_research_state(self, key):
        return None

    def save_research_state(self, key, value):
        return value

    def get_integration(self, iid):
        return {"id": iid, "name": "X", "identifier": "facebook",
                "picture": None}

    def generate_image(self, prompt, **kw):
        self.calls.append("generate_image")
        return self._image_ref

    def create_post(self, posts, publish_at, post_type=None):
        self.calls.append("create_post")
        self.posts_calls.append(posts)
        return [{"postId": "p-%d" % len(self.posts_calls),
                 "status": "success"}]


def _kind_of(call_posts):
    return (call_posts[0].get("settings") or {}).get("post_type")


def _targets_of(client):
    return [c[0]["integration"]["id"] for c in client.posts_calls]


def _story_images(client):
    return [c[0]["value"][0]["image"] for c in client.posts_calls
            if _kind_of(c) == "story"]


class ImageStoryCase(unittest.TestCase):
    def test_story_publishes_same_image_on_every_channel(self):
        client = _LiveClient(image_ref={"id": "img-1", "path": "ai/a.png"})
        ctx = _ctx("none", roles=IMAGE_ROLE,
                   campaign={"story": {"enabled": True}},
                   client=client, targets=["int-2"])
        out = run_execute_stage(ctx)
        self.assertEqual(out["outcome"], "done")
        # main(int-1) + mirror(int-2) + story(int-1) + story(int-2)
        self.assertEqual(len(client.posts_calls), 4)
        self.assertEqual([_kind_of(c) for c in client.posts_calls],
                         ["post", "post", "story", "story"])
        self.assertEqual(_targets_of(client),
                         ["int-1", "int-2", "int-1", "int-2"])
        story_imgs = _story_images(client)
        self.assertEqual(story_imgs[0],
                         [{"id": "img-1", "path": "ai/a.png"}])
        self.assertEqual(story_imgs[0], story_imgs[1])
        self.assertEqual(out["post_ids"], ["p-1", "p-2", "p-3", "p-4"])

    def test_text_only_story_config_publishes_no_story(self):
        client = _LiveClient()
        ctx = _ctx("none", roles=NO_IMAGE_ROLE,
                   campaign={"story": {"enabled": True}},
                   client=client, targets=["int-2"])
        out = run_execute_stage(ctx)
        self.assertEqual(out["outcome"], "done")
        self.assertEqual(len(client.posts_calls), 2)
        self.assertEqual([_kind_of(c) for c in client.posts_calls],
                         ["post", "post"])

    def test_story_failure_never_fails_the_stage(self):
        class _StoryFails(_LiveClient):
            def create_post(self, posts, publish_at, post_type=None):
                if (posts[0].get("settings")
                        or {}).get("post_type") == "story":
                    raise RuntimeError("story refused")
                return super().create_post(posts, publish_at,
                                           post_type=post_type)

        client = _StoryFails(image_ref={"id": "img-1", "path": "ai/a.png"})
        ctx = _ctx("none", roles=IMAGE_ROLE,
                   campaign={"story": {"enabled": True}},
                   client=client, targets=["int-2"])
        out = run_execute_stage(ctx)
        self.assertEqual(out["outcome"], "done")
        # The main post + its mirror shipped; both story attempts are
        # skipped without turning the stage into a failure.
        self.assertEqual(len(client.posts_calls), 2)
        self.assertEqual(out["post_ids"], ["p-1", "p-2"])

    def test_story_absent_is_byte_identical_default(self):
        client = _LiveClient(image_ref={"id": "img-1", "path": "ai/a.png"})
        ctx = _ctx("none", roles=IMAGE_ROLE, client=client,
                   targets=["int-2"])
        out = run_execute_stage(ctx)
        self.assertEqual(out["outcome"], "done")
        self.assertEqual(len(client.posts_calls), 2)
        self.assertEqual([_kind_of(c) for c in client.posts_calls],
                         ["post", "post"])


class StoryDelayStandDownCase(unittest.TestCase):
    """afterMinutes (even the smallest, 1) switches the cycle to
    the AFTER-POST mode: the same-moment story stands down so one
    run never stories the same image twice; Immediate (absent/0)
    keeps today's behavior byte-identically (ImageStoryCase)."""

    def test_one_minute_delay_keeps_immediate_story_down(self):
        client = _LiveClient(image_ref={"id": "img-1", "path": "ai/a.png"})
        ctx = _ctx("none", roles=IMAGE_ROLE,
                   campaign={"story": {"enabled": True, "afterMinutes": 1,
                                       "linkEnabled": True}},
                   client=client, targets=["int-2"])
        out = run_execute_stage(ctx)
        self.assertEqual(out["outcome"], "done")
        self.assertEqual(len(client.posts_calls), 2)
        self.assertEqual([_kind_of(c) for c in client.posts_calls],
                         ["post", "post"])
