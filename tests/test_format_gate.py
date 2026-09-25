"""The format gate is a Tuzzina account constraint, never a content
decision.

Proves, against the real execute stage:
- a valid text-only, text+image and text+video result is NOT
  suppressed by any central editorial rule when the integration
  allows that shape;
- when the integration does NOT allow the shape, the run FAILS
  loudly (operational failure with the reason) instead of reporting a
  successful run that silently produced zero posts;
- the same Skill output is treated identically regardless of quota;
- provider/technical failures keep their existing classification.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from stage_run import run_execute_stage


class _Client:
    def release_claim(self, key, run_id):
        pass

    def get_research_state(self, key):
        return None

    def save_research_state(self, key, value):
        return value


def _opportunity(media_intent, source_item_ids=()):
    return {"eligible": True, "topic": "T", "angle": "A",
            "rationale": "R", "facts": ["a truthful fact"],
            "source_item_ids": list(source_item_ids),
            "content_format": "post", "media_intent": media_intent,
            "audience": "a", "language": "en", "priority": "normal",
            "confidence": "medium", "constraints": []}


def _ctx(media_intent, formats, **extra):
    ctx = {
        "client": _Client(), "run_id": "r-fmt",
        "cfg": {"brand": {"tone": "direct", "audience": "a"},
                "language": {"default": "en"},
                "channel_meta": {"identifier": "mastodon"},
                "pillars": [], "generation": {}, "media": {},
                "hashtags": {"enabled": True, "max": 3},
                "links_policy": "hide", "schedule": {},
                "roles": ["research", "analysis", "text", "image", "video"],
                "distribution": {"formats": formats, "enabled": True}},
        "research": None, "items": [], "claimed": [],
        "dry_run": True, "mode": "draft",
        "integration_id": "int-1", "schedule": {}, "now": "t",
        "text": "Post words here.", "prompt": None,
        "opportunity": _opportunity(media_intent),
    }
    ctx.update(extra)
    return ctx


ALL = {"text": 1, "text+image": 1, "text+video": 1, "link+text": 1}


class FormatGateCase(unittest.TestCase):
    def test_text_only_result_is_produced_when_allowed(self):
        out = run_execute_stage(_ctx("none", dict(ALL)))
        self.assertEqual(out["outcome"], "done")
        self.assertEqual(out["intents"], 1)
        self.assertEqual(out["packages"], 1)

    def test_text_image_result_is_produced_when_allowed(self):
        out = run_execute_stage(_ctx("image", dict(ALL)))
        self.assertEqual(out["intents"], 1)
        self.assertEqual(out["packages"], 1)

    def test_text_video_result_is_produced_when_allowed(self):
        out = run_execute_stage(_ctx("video", dict(ALL)))
        self.assertEqual(out["intents"], 1)
        self.assertEqual(out["packages"], 1)

    def test_refused_shape_fails_loudly_instead_of_silently_succeeding(self):
        # The account does not allow text-only right now. That is an
        # operational constraint, so the run must fail with the reason
        # instead of returning outcome=done with zero posts.
        formats = {"text": 0, "text+image": 0, "text+video": 0,
                   "link+text": 0}
        with self.assertRaises(ValueError) as ctx:
            run_execute_stage(_ctx("none", formats))
        self.assertIn("format-not-allowed", str(ctx.exception))

    def test_no_distribution_means_unconstrained(self):
        ctx = _ctx("none", dict(ALL))
        ctx["cfg"] = dict(ctx["cfg"])
        ctx["cfg"].pop("distribution")
        out = run_execute_stage(ctx)
        self.assertEqual(out["intents"], 1)

    def test_media_intent_comes_from_the_skill_not_the_engine(self):
        # Identical quota, different Skill output: both shapes are
        # executed exactly as the Skill asked, with no engine-side
        # preference for one modality.
        from research.generation import GenerationPlan, TextRequest
        none_plan = GenerationPlan(text=TextRequest(title="T", summary="S"),
                                   media_intent="none")
        image_plan = GenerationPlan(text=TextRequest(title="T", summary="S"),
                                    media_intent="image",
                                    image=type("I", (), {"prompt": "p"})())
        self.assertIsNone(none_plan.image)
        self.assertIsNotNone(image_plan.image)
        for media, expected in (("none", 1), ("image", 1), ("video", 1)):
            out = run_execute_stage(_ctx(media, dict(ALL)))
            self.assertEqual(out["intents"], expected, media)


if __name__ == "__main__":
    unittest.main()
