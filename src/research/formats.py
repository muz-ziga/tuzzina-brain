"""Content format vocabulary + allowlist gate (Phase 8).

Executable formats, derived 1:1 from CURRENT Tuzzina post
primitives (content string + image[] + settings.url) — never
invented:

  text        content only, no media, no link attach
  text+image  content + >=1 image ref
  text+video  content + video ref
  link+text   content + settings.url attach

Excluded deliberately: `story` (cycle builds post_kind=post
only; provider story support varies — future work), bare
`image` (server emptyContent rules make media-only unreliable).

`select_format()` maps (media_intent, links_policy) -> format
and checks it against the integration's allowlist. A disallowed
format is a clean stop with a reason — never a silent downgrade
(no policy permits downgrades in Phase 8). Provider-level
executability (e.g. settings.url support) stays Tuzzina-owned:
its validation fails loud server-side.
"""
from __future__ import annotations

FORMATS = ("text", "text+image", "text+video", "link+text")


def format_for(media_intent: str, links_policy: str) -> str:
    """Pure mapping, no policy input. Unknown media falls back to
    text only when links are off; link attach always wins the
    link+text shape (mirror of build_intent link handling)."""
    media = (media_intent or "none").strip().lower()
    links = (links_policy or "hide").strip().lower()
    if media == "image":
        return "text+image"
    if media == "video":
        return "text+video"
    if links == "attach":
        return "link+text"
    return "text"


def select_format(media_intent: str, links_policy: str,
                  allowed=None) -> tuple:
    """(format | None, reason). allowed=None means unconstrained
    (no distribution configured: pre-Phase-8 behavior)."""
    fmt = format_for(media_intent, links_policy)
    if allowed is None:
        return fmt, "unconstrained"
    if fmt in list(allowed):
        return fmt, "allowed"
    return None, f"format-not-allowed:{fmt}"


def allowed_from_distribution(distribution) -> object:
    """Allowlist from a distribution config ({formats: {name:
    count}}). Keys with count > 0 are allowed; an explicitly
    empty formats dict denies everything. Missing/non-dict
    config -> None (unconstrained: no row configured). Counts
    are validated where the config is written (Tuzzina); here
    they only gate."""
    if not isinstance(distribution, dict):
        return None
    formats = distribution.get("formats")
    if not isinstance(formats, dict):
        return None
    allowed = [f for f, c in formats.items()
               if f in FORMATS and isinstance(c, (int, float))
               and c > 0]
    return allowed
