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


CONCEPT_FIELDS = ("meaning", "subject", "relationship")

# Zone contract: the single owner of the composed-ad geometry. The
# same numbers are rendered into the image prompt (so the model keeps
# the reserved areas quiet) and shipped to Tuzzina (so the compositor
# places headline, logo and icon inside exactly those areas). Nothing
# downstream re-derives a coordinate.
AD_LAYOUT = {
    "width": 896,
    "height": 1152,
    "margin": 72,
    "logo": {"x": 72, "y": 84, "maxWidth": 240, "maxHeight": 120},
    "icon": {"x": 624, "y": 120, "maxWidth": 200, "maxHeight": 200},
    "text": {"x": 72, "y": 520, "maxWidth": 752, "maxHeight": 300},
    "subject": {"y": 0, "maxHeight": 507},
    "scrim": {"fadeStart": 0.38, "solidStart": 0.44, "opacity": 0.97},
    "contrast": {"minRatio": 2.2, "plateOpacity": 0.55},
    "type": {"maxFont": 56, "minFont": 30, "lineGap": 1.22, "maxLines": 4},
}

# Icon slots are safety bounds, not placements: the decision of which
# slot (or none at all) belongs to the visual concept.
AD_ICON_SLOTS = {
    "top-left": {"x": 72, "y": 84, "maxWidth": 200, "maxHeight": 200},
    "top-right": {"x": 624, "y": 84, "maxWidth": 200, "maxHeight": 200},
    "top-center": {"x": 348, "y": 84, "maxWidth": 200, "maxHeight": 200},
}

# No palette, no color list and no taste live here: the active IMAGE
# SKILL owns the visual policy (which colors, whether icons are part of
# the composition, what the image must contain). This module only owns
# the mechanism that carries a declared list to the current slot, plus
# the structural check that a declared color is a color.

_HEX_RE = __import__("re").compile(r"^#[0-9A-Fa-f]{6}$")


def is_color(value: object) -> bool:
    """Structural check only: is this a #RRGGBB literal?"""
    return bool(_HEX_RE.match(str(value or "").strip()))


def slot_value(values, when: str | None, slots_per_day: int = 8) -> str:
    """Pick this run's entry from a list the SKILL declared.

    Pure and policy-free: the caller (an image Skill's config) owns the
    list; this only derives WHICH entry belongs to the current slot, so
    a declared rotation cannot repeat itself inside one cycle and
    starts a new cycle the next day. Nothing here knows what the values
    mean, and a value that is not a color literal is a structural
    error, never a silent fallback.
    """
    items = [str(v).strip() for v in (values or []) if str(v).strip()]
    if not items:
        raise ValueError("slot-value: empty list")
    for value in items:
        if not is_color(value):
            raise ValueError("slot-value: not-a-color:" + value)
    import datetime as _dt
    text = str(when or "").strip()
    try:
        moment = (_dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
                  if text else _dt.datetime.now(_dt.timezone.utc))
    except ValueError:
        moment = _dt.datetime.now(_dt.timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=_dt.timezone.utc)
    slots = max(1, int(slots_per_day))
    day = int(moment.timestamp() // 86400)
    slot = (moment.hour * 60 + moment.minute) // (1440 // slots)
    slot = max(0, min(slots - 1, slot))
    return items[(day * 3 + slot) % len(items)]


def ad_layout_block(layout: dict | None = None) -> str:
    """Human-readable zone contract for the image model: the same
    numbers the compositor will use, so the model treats the reserved
    areas as reserved instead of guessing from prose."""
    lay = layout if isinstance(layout, dict) else AD_LAYOUT
    return (
        "Reserved areas (these exact zones are overlaid afterwards; "
        "keep every subject, face, object, symbol and high-contrast "
        "detail out of them):\n"
        "- subject zone: y 0 to %(subjectH)d (top of the frame)\n"
        "- headline zone: x %(tx)d to %(tr)d, y %(ty)d to %(tbottom)d\n"
        "- logo zone: top-left, x %(lx)d to %(lright)d, y %(ly)d to "
        "%(lbottom)d\n"
        "- icon zone: top-right, x %(ix)d to %(ir)d, y %(iy)d to "
        "%%(ibottom)d\n"
        "Nothing readable, no text, no logo, no symbol inside those "
        "zones."
        % {
            "subjectH": int(lay["subject"]["maxHeight"]),
            "tx": int(lay["text"]["x"]),
            "tr": int(lay["text"]["x"] + lay["text"]["maxWidth"]),
            "ty": int(lay["text"]["y"]),
            "tbottom": int(lay["text"]["y"] + lay["text"]["maxHeight"]),
            "lx": int(lay["logo"]["x"]),
            "lright": int(lay["logo"]["x"] + lay["logo"]["maxWidth"]),
            "ly": int(lay["logo"]["y"]),
            "lbottom": int(lay["logo"]["y"] + lay["logo"]["maxHeight"]),
            "ix": int(lay["icon"]["x"]),
            "ir": int(lay["icon"]["x"] + lay["icon"]["maxWidth"]),
            "iy": int(lay["icon"]["y"]),
            "ibottom": int(lay["icon"]["y"] + lay["icon"]["maxHeight"]),
        }
    )

# Minimal local normalization for the concept grounding gate: common
# words carry no visual claim, so they never count as grounding.
CONCEPT_STOPWORDS = frozenset({
    "and", "are", "but", "can", "each", "for", "from", "has", "have",
    "her", "him", "his", "into", "its", "let", "may", "more", "most",
    "must", "not", "now", "one", "only", "other", "our", "out", "own",
    "same", "she", "should", "some", "such", "than", "that", "the",
    "their", "them", "then", "they", "this", "those", "through", "too",
    "two", "very", "was", "were", "what", "when", "which", "while",
    "will", "with", "would", "you", "your", "yours", "into", "just",
    "also", "both", "each", "every", "more", "most", "much", "many",
})


def _stem(word: str) -> str:
    """Small morphological normalization so compare/comparison/
    compares/compared share one stem. Iterative suffix stripping,
    longest suffix first; no external library or synonym table."""
    w = word.lower()
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"
    # Strip one longer suffix per pass, then single-letter endings.
    for suf in ("ation", "ison", "tion", "ing", "ed", "es"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            w = w[:-len(suf)]
            break
    if w.endswith("s") and len(w) > 3:
        w = w[:-1]
    if w.endswith("e") and len(w) > 3:
        w = w[:-1]
    return w


def _content_tokens(text: str) -> set:
    """Local normalization: lowercase, split on non-alphanumerics,
    morphologically normalize, drop short and common words."""
    import re as _re
    words = _re.sub(r"[^a-z0-9]+", " ",
                    str(text or "").lower()).split()
    stems = set()
    for w in words:
        if w in CONCEPT_STOPWORDS or len(w) < 3:
            continue
        s = _stem(w)
        if len(s) >= 3 and s not in CONCEPT_STOPWORDS:
            stems.add(s)
        elif len(s) >= 3:
            stems.add(s)
    return stems


def _payload_vocab(post: dict | None) -> tuple[set, set]:
    """(prioritized, full) content vocabulary of the semantic post
    payload. Angle and facts carry the concrete claim, so they are the
    prioritized set; topic/audience/media intent only widen the match
    pool. An empty claim set falls back to the full pool."""
    p = post if isinstance(post, dict) else {}
    facts = " ".join(str(f or "") for f in list(p.get("facts") or []))
    claim = _content_tokens("%s %s" % (p.get("angle") or "", facts))
    full = _content_tokens(" ".join(
        str(p.get(k) or "") for k in
        ("topic", "angle", "audience", "media_intent")) + " " + facts)
    if not claim:
        claim = full
    return claim, full



def _post_block(post: dict | None) -> str:
    """One semantic-post block shared by the concept decision and the
    prompt writer, so both read the same meaning (topic label,
    angle, audience, media intent, key facts) and neither can work
    from the label alone."""
    p = post if isinstance(post, dict) else {}
    lines = []
    for key, label in (("topic", "Topic"), ("angle", "Angle"),
                       ("audience", "Audience"),
                       ("media_intent", "Media intent")):
        value = str(p.get(key) or "").strip()
        if value:
            lines.append(f"- {label}: {value[:300]}")
    facts = [str(f).strip() for f in list(p.get("facts") or [])[:6]
             if str(f).strip()]
    if facts:
        lines.append("- Key facts: " + "; ".join(f[:200] for f in facts))
    return "\n".join(lines)


def _concept_block(concept: dict) -> str:
    lines = ["- What the viewer must understand: " +
             str(concept.get("meaning") or "")[:400]]
    lines.append("- Subject or scene: " +
                 str(concept.get("subject") or "")[:400])
    lines.append("- Relationship to the post: " +
                 str(concept.get("relationship") or "")[:400])
    constraints = [str(c).strip()[:200]
                   for c in list(concept.get("brief_constraints") or [])
                   if str(c).strip()][:8]
    if constraints:
        lines.append("- Brief constraints to respect: " +
                     "; ".join(constraints))
    return "Visual concept (already decided from the post meaning; " \
        "render it, do not replace it):\n" + "\n".join(lines)


class MockImagePromptGenerator:
    """Deterministic image prompt for tests: topic -> prompt."""
    def generate_concept(self, post: dict | None,
                         policy: dict | None = None) -> dict:
        p = post if isinstance(post, dict) else {}
        return {
            "meaning": str(p.get("angle") or p.get("topic") or ""),
            "subject": str(p.get("topic") or ""),
            "relationship": "",
            "brief_constraints": [],
        }

    def generate_prompt(self, topic: str, brand: dict,
                        policy: dict | None = None,
                        post: dict | None = None,
                        concept: dict | None = None) -> str:
        return f"{topic} — studio scene, clean background, soft light"

    def select_icon(self, topic: str, assets: dict | None) -> str | None:
        """No LLM in mock/test: never selects (fail-closed)."""
        return None


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

    def __init__(self, prompt: str, concept: dict | None = None):
        self._prompt = prompt or ""
        self._concept = concept

    def generate_concept(self, post: dict | None,
                         policy: dict | None = None) -> dict:
        return dict(self._concept or {})

    def generate_prompt(self, topic: str, brand: dict,
                        policy: dict | None = None,
                        post: dict | None = None,
                        concept: dict | None = None) -> str:
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

    def generate_concept(self, post: dict | None,
                         policy: dict | None = None) -> dict:
        """Explicit visual-concept decision, domain-neutral.

        Input: the semantic post payload (topic, angle, facts,
        audience, media intent) plus the channel's rendered Visual
        Brief. Output: one small structure naming what the viewer
        must understand, the subject/scene that carries it, the
        relationship that ties subject to post meaning, and the brief
        constraints the concept must respect. A concept missing any
        required field never reaches prompt rendering.
        """
        from llm.adapters import (LLMError, complete_with_retry,
                                  unwrap_model_json)
        from channels.instructions import build_visual as _visual
        import json as _json
        visual = _visual(policy, video_rules=False)
        payload = _post_block(post)
        claim_vocab, full_vocab = _payload_vocab(post)

        def parse(raw: str) -> dict:
            try:
                data = _json.loads(unwrap_model_json(raw))
            except Exception:
                # Diagnostic only: the rejection code head is
                # unchanged; the suffix exposes what the model
                # actually returned so the cause is inspectable.
                raise LLMError("invalid-output:unparsed=%s" % (
                    " ".join(str(raw or "").split())[:160],))
            if not isinstance(data, dict):
                raise LLMError("invalid-output:not-a-mapping")
            out: dict = {}
            for key in ("meaning", "subject", "relationship"):
                value = str(data.get(key) or "").strip()
                if not value:
                    raise LLMError("invalid-output")
                out[key] = value[:400]
            raw_list = data.get("brief_constraints")
            if raw_list is None:
                raw_list = []
            if not isinstance(raw_list, list):
                raise LLMError("invalid-output")
            out["brief_constraints"] = [
                str(c).strip()[:200] for c in raw_list if str(c).strip()
            ][:8]
            art = data.get("art_direction")
            if art is None:
                # Art direction is optional. A concept that decides no
                # icon is the conservative default, never a forced
                # layer. Present-but-malformed still fails closed.
                out["art_direction"] = {
                    "icon": "none", "slot": "", "palette": [],
                    "text_align": "left",
                }
            else:
                if not isinstance(art, dict):
                    raise LLMError("invalid-output")
                icon = str(art.get("icon") or "").strip().lower()
                if icon not in ("use", "none"):
                    raise LLMError("invalid-output")
                slot = str(art.get("slot") or "").strip().lower()
                if icon == "use" and slot not in AD_ICON_SLOTS:
                    raise LLMError("invalid-output")
                if icon == "none":
                    slot = ""
                palette = art.get("palette")
                if palette is None:
                    palette = []
                if not isinstance(palette, list):
                    raise LLMError("invalid-output")
                align = str(art.get("text_align") or "left").strip().lower()
                if align not in ("left", "center"):
                    raise LLMError("invalid-output")
                out["art_direction"] = {
                    "icon": icon,
                    "slot": slot,
                    "palette": [str(c).strip()[:60] for c in palette
                                if str(c).strip()][:6],
                    "text_align": align,
                }
            # Grounding gate: a structurally valid concept is still
            # unusable if its meaning/subject share no vocabulary with
            # the post it claims to illustrate. Structure alone once
            # let a stock metaphor through and the prompt renderer
            # faithfully drew it. Only meaningful content words count;
            # at least one must come from the claim vocabulary
            # (angle + facts), one more from the payload overall.
            # No payload vocabulary means no claim to ground in, so
            # the concept is refused: fail closed, never invent.
            if not full_vocab:
                raise LLMError("invalid-output")
            used = _content_tokens(out["meaning"] + " " + out["subject"])
            if not (used & claim_vocab) or not (used & full_vocab):
                # Diagnostic only: the rejection code head stays
                # "invalid-output" (unchanged classification and
                # decision); the suffix only makes the missed words
                # inspectable through the existing stage envelope.
                raise LLMError(
                    "invalid-output:grounding-miss used=%s meaning=%s "
                    "subject=%s" % (
                        ",".join(sorted(used))[:120],
                        out["meaning"][:90], out["subject"][:90]))
            return out

        system = (
            "You decide the visual concept for one social image, in "
            "any domain. Work from the post meaning and the visual "
            "brief below; the topic label alone is never enough and "
            "must not become the picture.\n"
            "Answer exactly four questions:\n"
            "1. What must the viewer understand from this image? State "
            "the post's own communication goal, using its own subject "
            "matter.\n"
            "2. What subject or scene communicates that "
            "understanding? It must be a concrete visual representation "
            "of what this post is about.\n"
            "3. What relationship or metaphor ties the subject to the "
            "post meaning? A metaphor is allowed, but it must preserve "
            "the post's actual claim.\n"
            "4. Which of the visual brief constraints must the concept "
            "respect?\n"
            "Rules for meaning and subject: use the words of the post "
            "payload (its angle, facts, topic). meaning and subject "
            "must reuse the same content words from angle + facts as "
            "much as possible, while preserving natural meaning. Never "
            "replace the post with a generic motivational, "
            "inspirational, lifestyle, or stock metaphor such as "
            "overcoming adversity, persistence, a journey, or nature "
            "imagery, unless the post itself is about that. Do not "
            "invent a product, brand, or detail the post does not "
            "support.\n"
            "Also decide the art direction for the layers added later: "
            "whether an icon helps this post at all (\"none\" is a valid, "
            "often correct answer), which icon slot it would use "
            "(one of the listed slots), a color direction for the "
            "background, and whether the headline reads left-aligned or "
            "centered.\n"
            "Return JSON ONLY: {\"meaning\": str, \"subject\": str, "
            "\"relationship\": str, \"brief_constraints\": [str], "
            "\"art_direction\": {\"icon\": \"use\"|\"none\", \"slot\": "
            "\"top-left\"|\"top-right\"|\"top-center\", \"palette\": [str], "
            "\"text_align\": \"left\"|\"center\"}}."
        )
        system += "\n\nIcon slots (keep the chosen one clear): " + \
            ", ".join("%s x %d to %d, y %d to %d" % (
                name, box["x"], box["x"] + box["maxWidth"],
                box["y"], box["y"] + box["maxHeight"])
                for name, box in AD_ICON_SLOTS.items())
        if visual:
            system += "\n\n" + visual
        raw = complete_with_retry(
            self._adapter, system,
            "Post to illustrate:\n" + (payload or "(none provided)"),
            # Room for the full concept JSON: a truncated answer is
            # unparseable, not a grounding failure, and the concept
            # is longer than a single sentence.
            temperature=0.3, max_tokens=900, timeout=60,
            validate=lambda r: parse(r), task="visual_concept",
            max_attempts=2)
        return parse(raw)

    def generate_prompt(self, topic: str, brand: dict,
                        policy: dict | None = None,
                        post: dict | None = None,
                        concept: dict | None = None) -> str:
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
        parts.append(ad_layout_block())
        # Visual concept: decided from the post meaning in its own
        # step, now the authority for the visual idea. The prompt
        # writer renders this; it never re-decides the subject.
        if isinstance(concept, dict) and concept:
            parts.append(_concept_block(concept))
            art = concept.get("art_direction")
            if isinstance(art, dict) and art:
                bits = []
                if art.get("palette"):
                    bits.append("background palette: " +
                                ", ".join(str(c) for c in art["palette"]))
                if art.get("icon") == "use" and art.get("slot"):
                    box = AD_ICON_SLOTS.get(str(art["slot"]))
                    if box:
                        bits.append(
                            "keep the %s icon slot clear (x %d to %d, "
                            "y %d to %d)" % (
                                art["slot"], box["x"],
                                box["x"] + box["maxWidth"], box["y"],
                                box["y"] + box["maxHeight"]))
                if bits:
                    parts.append("Art direction for this image: " +
                                 "; ".join(bits) + ".")
        # Semantic post payload: what the post actually says, who it
        # is for, and what the analysis asked for visually. The
        # generator never sees a bare label when the workflow can
        # supply this, so the concept cannot be invented from a single
        # keyword.
        p = post if isinstance(post, dict) else {}
        payload = _post_block(p)
        if payload:
            parts.append("Post to illustrate:\n" + payload)
        # keep prompt request short; campaign context already in skill
        parts.append(
            f"Post topic: {topic[:200]}. Brand: {brand.get('name','')}. "
            + ("Render the visual concept above exactly: keep its "
               "subject and relationship, obey the visual instructions, "
               "and do not invent a different visual idea. "
               if isinstance(concept, dict) and concept else
               "Derive the visual from the post meaning and the visual "
               "instructions above; never substitute a generic look from a "
               "familiar keyword. ")
            + "Return one image prompt only, no explanation.")
        sys = "\n\n".join(parts)
        from llm.adapters import complete_with_retry
        # Same generic retry as every other role: a reasoning-only
        # or empty prompt reply repeats the task; only a validated
        # prompt may reach the Tuzzina image execution path.
        raw = complete_with_retry(
            self._adapter, sys, f"Topic: {topic}",
            temperature=0.7, max_tokens=200, timeout=60,
            task="image_prompt").strip()
        # The Image Skill documents the two-step icon channel, so
        # models sometimes echo its scaffolding (ICON SELECTION /
        # Category: / Icon: / icon:name / [icon:...]) around the real
        # prompt, sometimes markdown-bolded. That is template
        # metadata, never image content: it must not reach the
        # generator, and the real key still travels as the trailing
        # [icon:key] tag the stage appends. Emphasisation markers are
        # stripped before matching so the format cannot slip through.
        import re as _re
        keep = []
        for ln in raw.splitlines():
            plain = ln.replace("*", "").replace("#", "").strip()
            if _re.match(
                    r"^(?:icon\s+selection|category|icon)(?:\s*:.*)?$",
                    plain, _re.I):
                continue
            if _re.match(
                    r"^\[icon:[A-Za-z0-9][A-Za-z0-9._/-]{0,120}\]$",
                    plain, _re.I):
                continue
            keep.append(ln)
        return "\n".join(keep).strip()

    def select_icon(self, topic: str,
                    assets: dict | None) -> str | None:
        """Two-step icon pick from the synced identity manifest
        (tree first, then names of the chosen category). The agent
        sees category counts plus one category's names — never the
        whole library — so any library size fits the prompt. Any
        unknown answer drops to no-icon (the post never dies for
        an icon). Returns the full manifest key
        (identity/<cat>/<name>.<ext>) or None."""
        from llm.adapters import (LLMError, complete_with_retry,
                                  unwrap_model_json)
        import json as _json
        if not isinstance(assets, dict):
            return None
        tree = assets.get("tree") or {}
        files = assets.get("files") or {}
        if not isinstance(tree, dict) or not isinstance(files, dict):
            return None
        cats = sorted(k for k, v in tree.items()
                      if isinstance(v, dict) and
                      (v.get("count") or 0) > 0)
        if not cats:
            return None
        docs = assets.get("docs") or []
        guide = "\n".join(str(d.get("text") or "")[:800]
                          for d in docs[:2]
                          if isinstance(d, dict)).strip()

        def check_cat(raw: str) -> None:
            try:
                cat = _json.loads(unwrap_model_json(raw)) \
                    .get("category")
            except Exception:
                raise LLMError("invalid-output")
            if cat not in cats:
                raise LLMError("invalid-output")

        def check_icon(names):
            def _check(raw: str) -> None:
                try:
                    icon = _json.loads(unwrap_model_json(raw)) \
                        .get("icon")
                except Exception:
                    raise LLMError("invalid-output")
                if icon not in names:
                    raise LLMError("invalid-output")
            return _check

        tree_lines = "\n".join(
            "%s (%d)" % (c, (tree[c] or {}).get("count", 0))
            for c in cats)
        cat_sys = ("Pick one icon category for the post topic. "
                   "Reply JSON ONLY: {\"category\": name}.")
        cat_user = "Topic: %s\nCategories:\n%s%s" % (
            (topic or "")[:200], tree_lines,
            ("\nGuide:\n" + guide) if guide else "")
        try:
            raw = complete_with_retry(
                self._adapter, cat_sys, cat_user,
                temperature=0.2, max_tokens=60, timeout=30,
                validate=check_cat, task="icon_category",
                max_attempts=2)
            cat = _json.loads(unwrap_model_json(raw))["category"]
        except Exception:
            return None
        names = sorted(
            "%s/%s" % (f.get("category"), f.get("name"))
            for f in files.values()
            if isinstance(f, dict) and
            f.get("category") == cat)[:120]
        if not names:
            return None
        icon_sys = ("Pick one icon for the post topic. Reply JSON "
                    "ONLY: {\"icon\": \"category/name\"}.")
        icon_user = "Topic: %s\nIcons:\n%s" % (
            (topic or "")[:200], "\n".join(names))
        try:
            raw = complete_with_retry(
                self._adapter, icon_sys, icon_user,
                temperature=0.2, max_tokens=60, timeout=30,
                validate=check_icon(names), task="icon_pick",
                max_attempts=2)
            picked = _json.loads(unwrap_model_json(raw))["icon"]
        except Exception:
            return None
        for key, f in files.items():
            if isinstance(f, dict) and \
                    "%s/%s" % (f.get("category"),
                               f.get("name")) == picked:
                return key
        return None


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
