"""CanonicalIntent: the Brain's publishing decision, in Brain terms.

This is deliberately NOT a copy of Tuzzina's CreatePostDto (or of
facebook.dto.ts / instagram.dto.ts). It carries what the Brain
decided; the PlatformAdapter translates it into the Tuzzina payload
at inject time. Anything Tuzzina does not know is never invented
here -- unknown capabilities surface as UnsupportedCapability
instead.
"""
from __future__ import annotations
from dataclasses import dataclass, field

from skills.loader import COMPANION_MARKER

MODES = ("draft", "schedule", "now")
POST_KINDS = ("post", "story")


def split_companion(text: str) -> tuple:
    """Split rendered text into (main, companion) on the first
    COMPANION_MARKER line. Padding whitespace is tolerated; an
    empty companion collapses to post-only. No marker returns
    the text untouched with an empty companion, so every
    existing skill behaves exactly as before."""
    if not isinstance(text, str):
        return "", ""
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if line.strip() == COMPANION_MARKER:
            return ("\n".join(lines[:i]).strip(),
                    "\n".join(lines[i + 1:]).strip())
    return text, ""


@dataclass
class CanonicalIntent:
    # Tuzzina integration id. The ONLY channel reference. Resolved
    # against live Tuzzina at inject time; never matched by name.
    integration_id: str = ""
    # Plain generated text. Hashtags and mentions travel separately
    # and are assembled once, inside the adapter (see 0.5.1 fix).
    content: str = ""
    # Companion (first) comment text. Split out of content on the
    # COMPANION_MARKER line by build_intent (explicit value wins).
    # Rides as the second value item; Tuzzina publishes it natively
    # on commentable providers and posts the parent alone elsewhere.
    companion: str = ""
    # Already-uploaded media refs: [{"id": ..., "path": ...}].
    # Upload happens BEFORE intent creation, through Tuzzina's
    # upload endpoints. The brain never touches R2.
    media: list = field(default_factory=list)
    # Optional source URL + policy (attach|cta|hide). The adapter
    # decides what the platform supports (FB: settings.url,
    # IG: omitted).
    link: str = ""
    links_policy: str = "hide"
    # Generated tags, used verbatim. The service never regenerates.
    hashtags: list = field(default_factory=list)
    # Handles without "@" (service normalizes). Inline for FB+IG.
    mentions: list = field(default_factory=list)
    # Platform shape: "post" | "story". Brain-vocabulary enum
    # enforced at inject time; whether a platform accepts the kind
    # is validated authoritatively by Tuzzina.
    post_kind: str = "post"
    # ISO datetime string. Required always (Tuzzina requires date
    # even for drafts). For mode="now" it SHOULD be ~now, but
    # Tuzzina owns execution timing, not the brain.
    publish_at: str = ""
    # Tuzzina post type: "draft" | "schedule" | "now".
    mode: str = "draft"
    # Opaque extra settings merged over adapter defaults. Keys are
    # passed through untouched; Tuzzina validates shape.
    settings: dict = field(default_factory=dict)
    # CTA text used when links_policy == "cta".
    cta_style: str = "Learn more"
    # Optional deterministic row ids for the Tuzzina value
    # items, set only by the idempotent publish path
    # (slot_publish). When present with exactly one id per
    # value item, InjectionService attaches them so a replay
    # upserts the same rows instead of creating duplicates.
    # None (default) preserves the legacy random-uuid path.
    value_ids: list | None = None


def build_intent(*, integration_id: str, content: str,
                 media: list | None = None, publish_at: str,
                 mode: str = "draft", hashtags: list | None = None,
                 mentions: list | None = None, link: str = "",
                 links_policy: str = "hide", post_kind: str = "post",
                 settings: dict | None = None,
                 cta_style: str = "Learn more",
                 companion: str = "") -> CanonicalIntent:
    """Build an intent from explicit Brain decisions. Thin helper so
    Strategy wiring (and tests) don't hand-assemble the dataclass.
    No validation here beyond type-safe defaults; the
    InjectionService validates at inject time. An empty companion
    is split out of content on the marker line; an explicit
    non-empty companion wins over the split."""
    content = content or ""
    companion = (companion or "").strip()
    if not companion:
        content, companion = split_companion(content)
    return CanonicalIntent(
        integration_id=integration_id or "",
        content=content,
        companion=companion,
        media=list(media or []),
        link=link or "",
        links_policy=links_policy or "hide",
        hashtags=list(hashtags or []),
        mentions=list(mentions or []),
        post_kind=post_kind or "post",
        publish_at=publish_at or "",
        mode=mode or "draft",
        settings=dict(settings or {}),
        cta_style=cta_style or "Learn more",
    )
