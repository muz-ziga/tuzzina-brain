import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from contracts import SourceItem
from g2 import generators as G
from g2.pipeline import build_package


def item(**kw):
    d = dict(source_id="s1", source_type="website",
             source_url="https://demo.test/a", title="Big Launch Day",
             text="We launch the new platform today with fast tools.",
             images=[], links=[], platform="website")
    d.update(kw)
    return SourceItem(**d)


BRAND = {"tone": "bold", "audience": "founders", "name": "Juzzir"}


class _AgentStub:
    def __init__(self, prompt="AGENT-PROMPT", raise_on=None):
        self.prompt = prompt
        self.raise_on = raise_on
        self.calls = []

    def generate_prompt(self, topic, brand, policy=None):
        self.calls.append((topic, brand))
        if self.raise_on:
            raise self.raise_on
        return self.prompt


class MockPromptTest(unittest.TestCase):
    def test_mock_prompt_is_non_empty(self):
        p = G.MockImagePromptGenerator().generate_prompt("topic", BRAND, {})
        self.assertIn("topic", p)
        self.assertTrue(p.strip())


class PipelineAgentTest(unittest.TestCase):
    def _pkg(self, policy):
        return build_package(item(), BRAND, {}, {"enabled": False},
                             "hide", G.MockTextGenerator(),
                             G.MockImageGenerator(), policy=policy)

    def test_agent_prompt_used_when_configured(self):
        agent = _AgentStub()
        pkg = self._pkg({"image_prompt_gen": agent})
        self.assertEqual(pkg.media[0]["kind"], "generator")
        self.assertEqual(pkg.media[0]["prompt"], "AGENT-PROMPT")
        self.assertEqual(len(agent.calls), 1)

    def test_deterministic_prompt_when_agent_absent(self):
        pkg = self._pkg({})
        prompt = pkg.media[0]["prompt"]
        self.assertIn("Big Launch Day", prompt)
        self.assertNotEqual(prompt, "AGENT-PROMPT")

    def test_agent_failure_propagates(self):
        agent = _AgentStub(raise_on=RuntimeError("boom"))
        with self.assertRaises(RuntimeError):
            self._pkg({"image_prompt_gen": agent})


class BuildImagePromptGenTest(unittest.TestCase):
    KEYS = [
        "BRAIN_IMAGE_PROMPT_ADAPTER", "BRAIN_IMAGE_PROMPT_PROTOCOL",
        "BRAIN_IMAGE_PROMPT_KEY", "BRAIN_IMAGE_PROMPT_MODEL",
        "BRAIN_IMAGE_PROMPT_BASE", "BRAIN_IMAGE_PROMPT_SESSION_HEADER",
        "BRAIN_IMAGE_PROMPT_HEADERS", "OPENAI_API_KEY",
    ]

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in self.KEYS}
        for k in self.KEYS:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _build(self):
        from run_cycle import _build_image_prompt_gen
        return _build_image_prompt_gen("--openai", session_id="run-1")

    def test_none_without_any_config(self):
        self.assertIsNone(self._build())

    def test_mock_mode_returns_mock(self):
        from run_cycle import _build_image_prompt_gen
        gen = _build_image_prompt_gen("--mock")
        self.assertIsInstance(gen, G.MockImagePromptGenerator)

    def test_partial_config_fails_closed(self):
        os.environ["BRAIN_IMAGE_PROMPT_ADAPTER"] = "openai"
        with self.assertRaises(ValueError):
            self._build()

    def test_builds_when_configured(self):
        os.environ["BRAIN_IMAGE_PROMPT_ADAPTER"] = "openai"
        os.environ["BRAIN_IMAGE_PROMPT_KEY"] = "sk-agent"
        os.environ["BRAIN_IMAGE_PROMPT_MODEL"] = "m-agent"
        os.environ["BRAIN_IMAGE_PROMPT_HEADERS"] = '{"X-Tag": "a"}'
        gen = self._build()
        self.assertIsInstance(gen, G.OpenAIImagePromptGenerator)
        self.assertTrue(hasattr(gen, "generate_prompt"))


if __name__ == "__main__":
    unittest.main()
