"""Generator interfaces. OpenAITextGenerator needs OPENAI_API_KEY;
MockTextGenerator/MockImageGenerator build deterministic content for
tests/dry-runs. Image bytes are never generated locally in production:
TuzzinaImageGenerator is a delegation marker routed by run.py to
TuzzinaClient.generate_image (existing Tuzzina image tool owns the
engine, credits, storage, and media reference). MetaAIImageGenerator:
interface reserved, not implemented (see README).

INTELLIGENCE BOUNDARY (Step 5): G2 owns decisions and policy only.
The single TEMPORARY exception is OpenAITextGenerator, kept solely
because no machine-facing Tuzzina text endpoint accepts full Brain
context (no Public API text route, no MCP text tool, internal
/generator is session-authed and brand-unaware, Agent chat has no
deterministic contract). The temporary engine owns just the LLM
call: no scheduling, media, OAuth, publishing, or provider truth.
"""
from __future__ import annotations
import struct
import zlib
from abc import ABC, abstractmethod


class TextGenerator(ABC):
    @abstractmethod
    def generate(self, title: str, summary: str, brand: dict,
                 policy: dict | None = None) -> str:
        """Brain-context text; live engines may render policy skill."""


class ImageGenerator(ABC):
    @abstractmethod
    def generate_png(self, prompt: str) -> tuple[bytes, str]:
        """Returns (png_bytes, suggested_filename)."""


def _words(text: str, limit: int = 40) -> list[str]:
    toks = [t.strip(".,!?:;()\"'").lower() for t in text.split()]
    stop = {"the", "and", "for", "with", "this", "that", "from", "into",
            "your", "you", "are", "was", "were", "has", "have", "will",
            "على", "من", "في", "إلى", "أن", "ما", "هذا", "هذه", "التي",
            "الذي", "مع", "بين", "بعد", "قبل", "عند", "كل", "غير"}
    out = [t for t in toks if len(t) > 3 and t not in stop]
    return out[:limit]


class MockTextGenerator(TextGenerator):
    def generate(self, title: str, summary: str, brand: dict,
                 policy: dict | None = None) -> str:
        tone = (brand.get("tone") or "").strip()
        head = f"{title}".strip()
        body = summary.strip()[:220]
        parts = [p for p in [head, body] if p]
        text = "\n\n".join(parts)
        if tone:
            text += f"\n\n({tone})"
        return text[:600]


class OpenAITextGenerator(TextGenerator):
    """TEMPORARY execution engine (see module boundary note).

    Owns ONLY the LLM call. Never acquires: scheduling, media
    handling/storage/upload, OAuth/integration state, publishing,
    provider capabilities, or any Tuzzina execution duty. If a
    machine-facing Tuzzina text endpoint accepting full Brain
    context appears, this class is deleted and the decision layer
    (title/summary/brand/prompt) delegates to it unchanged.

    Transport runs through an injected llm adapter (default:
    OpenAI). The `adapter` carries its own credential+model; an
    explicitly passed adapter wins entirely (role-level api_key /
    model are used only to build the default).
    """

    def __init__(self, api_key: str = "", model: str = "gpt-4.1",
                 adapter=None):
        if adapter is None:
            if not api_key:
                raise RuntimeError("OPENAI_API_KEY is not set")
            from llm.adapters import OpenAIAdapter
            adapter = OpenAIAdapter(api_key, model)
        self._adapter = adapter

    def generate(self, title: str, summary: str, brand: dict,
                 policy: dict | None = None) -> str:
        from skills.loader import COMPANION_MARKER, get_skill
        campaign_skills = policy.get("skills") if isinstance(
            policy, dict) else None
        skill = get_skill(
            "text", (campaign_skills or {}).get("text"))
        max_chars = 500
        try:
            max_chars = int((skill.get("config") or {}).get(
                "max_chars", max_chars))
        except (TypeError, ValueError):
            max_chars = 500
        # Opt-in per skill config: only a skill that declares a
        # companion contract may emit the two-part output. Every
        # other skill keeps the exact historical instruction.
        companion_on = isinstance(skill.get("config"), dict) and bool(
            skill.get("config").get("companion"))
        parts = [skill["instructions"]]
        from channels.instructions import build as _strategy_block
        strategy = _strategy_block(policy)
        if strategy:
            parts.append(strategy)
        elif isinstance(brand, dict) and \
                (brand.get("tone") or brand.get("audience")):
            # Legacy fallback: no strategy profile available, keep
            # the caller-supplied voice instead of defaulting.
            parts.append(
                f"Brand voice: {brand.get('tone') or 'neutral'}; "
                f"audience: {brand.get('audience') or 'general'}.")
        if companion_on:
            parts.append(
                f"Write the social post plus its companion comment "
                f"after a {COMPANION_MARKER} line, max {max_chars} "
                f"chars total.")
        else:
            parts.append(
                f"Write one social post, max {max_chars} chars.")
        sys = "\n\n".join(parts)
        from llm.adapters import complete_with_retry
        return complete_with_retry(
            self._adapter, sys, f"Title: {title}\nSummary: {summary}",
            temperature=0.7, max_tokens=400, timeout=60,
            task="text").strip()


def _chunk(tag: bytes, data: bytes) -> bytes:
    return (struct.pack(">I", len(data)) + tag + data +
            struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


class MockImageGenerator(ImageGenerator):
    """Deterministic 64x64 PNG (brand color or gray). Valid PNG bytes so
    upload sniffing accepts it. Test/dry-run use only."""

    def generate_png(self, prompt: str) -> tuple[bytes, str]:
        raw = b"".join(b"\x00" + b"\x80\x80\x80" * 64 for _ in range(64))
        png = (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", struct.pack(
            ">IIBBBBB", 64, 64, 8, 2, 0, 0, 0)) + _chunk(b"IDAT",
            zlib.compress(raw)) + _chunk(b"IEND", b""))
        return png, "mock-64x64.png"


class MockImagePromptGenerator:
    """Deterministic image prompt for tests: topic -> prompt."""
    def generate_prompt(self, topic: str, brand: dict,
                        policy: dict | None = None) -> str:
        return f"{topic} — studio scene, clean background, soft light"


class FixedTextGenerator(TextGenerator):
    """Validated-output replay: returns a text string produced and
    validated by an earlier stage instead of calling an LLM. Pure
    deterministic passthrough; used by the execute stage so the
    text role is never re-invoked after its SUCCESS."""

    def __init__(self, text: str):
        self._text = text or ""

    def generate(self, title: str, summary: str, brand: dict,
                 policy: dict | None = None) -> str:
        return self._text


class FixedPromptGenerator:
    """Validated-output replay for the image prompt: returns the
    prompt string produced by the Image Agent stage. Same
    deterministic role as FixedTextGenerator."""

    def __init__(self, prompt: str):
        self._prompt = prompt or ""

    def generate_prompt(self, topic: str, brand: dict,
                        policy: dict | None = None) -> str:
        return self._prompt


class OpenAIImagePromptGenerator:
    """Image Agent LLM — prompt only, no bytes.

    Campaign context + Image Skill + post topic → single image
    prompt string. Transport identical to Text Agent (adapter with
    credential+model, session_id, base_url). Image bytes remain
    Tuzzina-owned via TuzzinaClient.generate_image.
    """
    def __init__(self, api_key: str = "", model: str = "gpt-4.1",
                 adapter=None):
        if adapter is None:
            if not api_key:
                raise RuntimeError("OPENAI_API_KEY is not set")
            from llm.adapters import OpenAIAdapter
            adapter = OpenAIAdapter(api_key, model)
        self._adapter = adapter

    def generate_prompt(self, topic: str, brand: dict,
                        policy: dict | None = None) -> str:
        from skills.loader import get_skill
        campaign_skills = policy.get("skills") if isinstance(
            policy, dict) else None
        skill = get_skill(
            "image", (campaign_skills or {}).get("image"))
        parts = [skill["instructions"]]
        from channels.instructions import build_visual as _visual
        visual = _visual(policy, video_rules=False)
        if visual:
            parts.append(visual)
        # keep prompt request short; campaign context already in skill
        parts.append(
            f"Post topic: {topic[:200]}. Brand: {brand.get('name','')}. "
            "Return one image prompt only, under 60 words, no explanation.")
        sys = "\n\n".join(parts)
        from llm.adapters import complete_with_retry
        # Same generic retry as every other role: a reasoning-only
        # or empty prompt reply repeats the task; only a validated
        # prompt may reach the Tuzzina image execution path.
        return complete_with_retry(
            self._adapter, sys, f"Topic: {topic}",
            temperature=0.7, max_tokens=200, timeout=60,
            task="image_prompt").strip()


class TuzzinaImageGenerator(ImageGenerator):
    """Delegation marker: image prompt decisions are executed by
    Tuzzina's existing image tool, never locally.

    Carries no credentials, calls no API, generates no bytes.
    run.py dispatches it to TuzzinaClient.generate_image, which
    returns Tuzzina's stored {id, path} reference directly.
    generate_png raises fail-closed so image bytes can never leak
    back into Brain's upload path."""

    def generate_png(self, prompt: str) -> tuple[bytes, str]:
        raise RuntimeError(
            "delegated image path: use TuzzinaClient.generate_image; "
            "local image bytes are unavailable by design")
