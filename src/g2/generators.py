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
        sys = ("Write one Arabic social post, max 500 chars. "
               f"Tone: {brand.get('tone', 'neutral')}. "
               f"Audience: {brand.get('audience', 'general')}. No hashtags.")
        from channels.instructions import build as _skill
        skill = _skill(policy)
        if skill:
            sys += "\n\n" + skill
        return self._adapter.complete(
            sys, f"Title: {title}\nSummary: {summary}",
            temperature=0.7, max_tokens=400,
            timeout=60).strip()


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
