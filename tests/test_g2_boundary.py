"""G2 intelligence-boundary guards (Step 5). G2 owns decisions and
policy only. The single TEMPORARY exception (OpenAITextGenerator)
is fenced here: documented as temporary, isolated from execution
duties, fed Brain context only. No network, no production."""
import inspect
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from g2 import generators as G


def _code(rel):
    import pathlib
    src = (pathlib.Path(__file__).parent.parent / "src" / rel).read_text(
        encoding="utf-8")
    out, in_doc = [], False
    for ln in src.splitlines():
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


class TemporaryEngineTest(unittest.TestCase):
    def test_temporary_status_documented(self):
        self.assertIn("TEMPORARY", (G.__doc__ or ""))
        self.assertIn("TEMPORARY",
                      (G.OpenAITextGenerator.__doc__ or ""))

    def test_text_engine_isolated_from_execution(self):
        # The temporary engine must never acquire scheduling, media,
        # OAuth/integration, publishing, or provider-truth duties.
        code = (_code("g2/generators.py") + _code("g2/pipeline.py")).lower()
        for bad in ("upload_bytes", "savefile", "temporal", "sched",
                    "oauth", "get_integration", "create_post",
                    "post_type", "r2.", "boto", "redis", "sqlite",
                    "publish", "maxLength", "capabilities("):
            self.assertNotIn(bad, code, bad)

    def test_text_context_is_brain_only(self):
        # Text generation receives Brain context, never provider data.
        sig = inspect.signature(G.OpenAITextGenerator.generate)
        self.assertEqual(list(sig.parameters),
                         ["self", "title", "summary", "brand", "policy"])
        out = G.MockTextGenerator().generate(
            "Title here", "Some summary body.", {
                "tone": "bold", "maxLength": 63206,
                "post_types": ["post"], "provider": "x"})
        self.assertIn("Title here", out)
        self.assertNotIn("63206", out)
        self.assertNotIn("post_types", out)


class ProductionWiringTest(unittest.TestCase):
    def test_openai_mode_has_no_bytes_capable_image_engine(self):
        import run as run_mod
        from unittest import mock
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "k"}):
            text_gen, image_gen = run_mod._build_generators("--openai")
        self.assertIsInstance(text_gen, G.OpenAITextGenerator)
        self.assertIsInstance(image_gen, G.TuzzinaImageGenerator)
        with self.assertRaises(RuntimeError):
            image_gen.generate_png("x")

    def test_mock_mode_uses_deterministic_generators(self):
        import run as run_mod
        text_gen, image_gen = run_mod._build_generators("--mock")
        self.assertIsInstance(text_gen, G.MockTextGenerator)
        self.assertIsInstance(image_gen, G.MockImageGenerator)
        self.assertEqual(
            text_gen.generate("T", "S", {"tone": "b"}),
            text_gen.generate("T", "S", {"tone": "b"}))

    def test_no_video_execution_in_g2(self):
        code = (_code("g2/generators.py") + _code("g2/pipeline.py")).lower()
        for bad in ("generate_video", "videomanager", "ffmpeg",
                    "moviepy", "stable_diffusion", "text_to_video"):
            self.assertNotIn(bad, code, bad)

    def test_media_intent_without_execution(self):
        # Intent stays representable when no execution exists at all:
        # no extracted images, no image engine -> valid package,
        # empty media, content/hashtags intact.
        from g2.pipeline import build_package
        from contracts import SourceItem
        item = SourceItem(source_id="s1", source_type="website",
                          source_url="https://demo.test/x",
                          title="T", text="Body text here.", images=[])
        pkg = build_package(
            item, {"tone": "neutral"}, {"default": "ar"},
            {"enabled": False}, "hide", G.MockTextGenerator(), None,
            platform="facebook")
        self.assertEqual(pkg.media, [])
        self.assertTrue(pkg.content)
        self.assertEqual(pkg.settings, {"__type": "facebook"})


if __name__ == "__main__":
    unittest.main()
