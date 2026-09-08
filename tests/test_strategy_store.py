import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from channels.strategy import (Brand, ChannelStrategy, ContentPolicy,
                                GenerationPolicy, HashtagPolicy,
                                PlanningPolicy)
from channels.strategy_store import (exists, list_stored, load, path_for,
                                       save, validate_integration_id)


def _tmpdir():
    return tempfile.mkdtemp(prefix="tbra_store_")


def _strategy(iid="integ-abc", tone="bold"):
    return ChannelStrategy(
        integration_id=iid,
        brand=Brand(tone=tone, audience="devs", cta_style="Try it",
                    preferred_words=["x"], banned_words=["spam"]),
        hashtags=HashtagPolicy(enabled=True, max=5, preferred=["a", "b"]),
        content=ContentPolicy(content_pillars=["how-to"]),
        generation=GenerationPolicy(provider="auto", prompt_style="brief"),
        planning=PlanningPolicy(cadence="weekly", days=["monday"],
                                 times=["10:00"]),
        links_policy="hide",
        extras={"custom_flag": 1},
    )


class IdValidationTest(unittest.TestCase):
    def test_accepts_alnum_dash_underscore(self):
        self.assertEqual(validate_integration_id("ABC_123-x"),
                         "ABC_123-x")

    def test_rejects_empty(self):
        with self.assertRaises(ValueError):
            validate_integration_id("")

    def test_rejects_path_traversal(self):
        for bad in ("../foo", "..", "a/b", "a\\b", "a b", "a.b",
                    "a" * 200):
            with self.assertRaises(ValueError):
                validate_integration_id(bad)

    def test_rejects_non_string(self):
        with self.assertRaises(ValueError):
            validate_integration_id(None)  # type: ignore
        with self.assertRaises(ValueError):
            validate_integration_id(123)  # type: ignore

    def test_path_for_rejects_traversal_at_construction(self):
        with self.assertRaises(ValueError):
            path_for("../escape", base_dir=_tmpdir())


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = _tmpdir()

    def test_save_load_roundtrip(self):
        s = _strategy()
        target = save(s, base_dir=self.tmp)
        self.assertTrue(target.is_file())
        loaded = load(s.integration_id, base_dir=self.tmp)
        self.assertEqual(loaded.integration_id, s.integration_id)
        self.assertEqual(loaded.brand.tone, "bold")
        self.assertEqual(loaded.brand.audience, "devs")
        self.assertEqual(loaded.brand.cta_style, "Try it")
        self.assertEqual(loaded.brand.preferred_words, ["x"])
        self.assertEqual(loaded.brand.banned_words, ["spam"])
        self.assertEqual(loaded.hashtags.enabled, True)
        self.assertEqual(loaded.hashtags.max, 5)
        self.assertEqual(loaded.hashtags.preferred, ["a", "b"])
        self.assertEqual(loaded.content.content_pillars, ["how-to"])
        self.assertEqual(loaded.generation.provider, "auto")
        self.assertEqual(loaded.generation.prompt_style, "brief")
        self.assertEqual(loaded.planning.cadence, "weekly")
        self.assertEqual(loaded.planning.days, ["monday"])
        self.assertEqual(loaded.planning.times, ["10:00"])
        self.assertEqual(loaded.links_policy, "hide")
        self.assertFalse(hasattr(loaded, "provider_overrides"))
        self.assertEqual(loaded.extras, {"custom_flag": 1})

    def test_save_overwrites(self):
        save(_strategy(tone="first"), base_dir=self.tmp)
        save(_strategy(tone="second"), base_dir=self.tmp)
        self.assertEqual(load("integ-abc", base_dir=self.tmp)
                         .brand.tone, "second")

    def test_load_missing_raises(self):
        with self.assertRaises(FileNotFoundError):
            load("nope", base_dir=self.tmp)

    def test_load_mismatched_id_raises(self):
        import yaml
        target = path_for("integ-abc", base_dir=self.tmp)
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            yaml.safe_dump({"integration_id": "integ-other",
                            "strategy": {}}, f)
        with self.assertRaises(ValueError):
            load("integ-abc", base_dir=self.tmp)

    def test_load_rejects_provider_overrides(self):
        # Fail-closed: a strategy file smuggling provider DTO data is
        # rejected at load, never silently accepted or stored.
        import yaml
        target = path_for("integ-abc", base_dir=self.tmp)
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            yaml.safe_dump({"integration_id": "integ-abc",
                            "strategy": {
                                "provider_overrides": {
                                    "post_type": "post"}}}, f)
        with self.assertRaises(ValueError):
            load("integ-abc", base_dir=self.tmp)

    def test_invalid_yaml_handled(self):
        target = path_for("integ-abc", base_dir=self.tmp)
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            f.write("not: valid: yaml: at: all\n  - : :\n")
        # PyYAML will raise a YAMLError; we accept anything that fails
        with self.assertRaises(Exception):
            load("integ-abc", base_dir=self.tmp)

    def test_filenames_are_integration_ids(self):
        save(_strategy(iid="integ-A"), base_dir=self.tmp)
        save(_strategy(iid="integ-B"), base_dir=self.tmp)
        self.assertEqual(set(list_stored(base_dir=self.tmp)),
                         {"integ-A", "integ-B"})

    def test_path_for_safe_under_traversal(self):
        # Even when base_dir is the cwd, an invalid id cannot
        # escape because validate_integration_id runs first.
        target = path_for("safe_id", base_dir=self.tmp)
        self.assertTrue(str(target).endswith("safe_id.yaml"))


class BrainStratDoesNotCarryIdentityTest(unittest.TestCase):
    def test_strategy_has_no_platform_or_name_field(self):
        s = _strategy()
        # The dataclass field names are the only source of truth
        # for what the brain may set. There must be no 'platform',
        # 'name', or 'provider' identity field on ChannelStrategy.
        forbidden = {"platform", "name", "provider", "oauth",
                      "channel_name", "facebook", "instagram",
                      "provider_overrides", "dto", "capabilities",
                      "maxLength", "post_type"}
        field_names = {f for f in s.__dataclass_fields__}
        self.assertTrue(forbidden.isdisjoint(field_names),
                        f"forbidden identity fields leaked: "
                        f"{forbidden & field_names}")

    def test_yaml_does_not_contain_social_platform_identity(self):
        s = _strategy()
        import yaml
        tmp = _tmpdir()
        save(s, base_dir=tmp)
        with open(path_for(s.integration_id, base_dir=tmp),
                  encoding="utf-8") as f:
            data = yaml.safe_load(f)
        # Walk the entire structure looking for SOCIAL platform
        # identity fields that would duplicate Tuzzina's job.
        # `generation.provider` is allowed (it means the AI
        # generation provider: openai/mock/etc.), so we only flag
        # the *social* platform names.
        social = {"facebook", "instagram", "tiktok", "x",
                  "twitter", "linkedin", "linkedin-page", "youtube",
                  "gmb", "threads", "pinterest", "reddit", "mastodon",
                  "bluesky", "discord", "slack", "twitch", "vk",
                  "devto", "medium", "wordpress", "hashnode",
                  "listmonk", "whop", "skool", "mewe", "tumblr",
                  "dribbble", "nostr", "farcaster", "telegram",
                  "kick", "wrapcast", "moltbook", "telegram-bot",
                  "oauth", "providerIdentifier"}
        def walk(o, path=""):
            if isinstance(o, dict):
                for k, v in o.items():
                    if k in social:
                        self.fail(f"social platform field {k!r} leaked at "
                                  f"{path}/{k}")
                    walk(v, f"{path}/{k}")
            elif isinstance(o, list):
                for i, v in enumerate(o):
                    walk(v, f"{path}[{i}]")
        walk(data)


if __name__ == "__main__":
    unittest.main()
