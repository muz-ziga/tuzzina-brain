"""Brain-owned strategy schema (v3). A Strategy is Brain's per-channel
policy attached to ONE Tuzzina integration. It does NOT define the
channel: Tuzzina owns integration identity, platform, OAuth, and
provider DTOs. The only bridge is `integration_id`.

Fields grouped semantically so the YAML reads as a Brain-side policy
document. Unknown future fields go in `extras` and pass through
unchanged (forward-compat with image_policy, video_policy, etc.).
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


_MISSING = object()


@dataclass
class Brand:
    name: Any = _MISSING
    tone: Any = _MISSING
    audience: Any = _MISSING
    banned_words: Any = _MISSING
    preferred_words: Any = _MISSING
    cta_style: Any = _MISSING


@dataclass
class HashtagPolicy:
    enabled: Any = _MISSING
    max: Any = _MISSING
    preferred: Any = _MISSING


@dataclass
class ContentPolicy:
    content_pillars: Any = _MISSING
    sources_policy: Any = _MISSING


@dataclass
class GenerationPolicy:
    provider: Any = _MISSING
    prompt_style: Any = _MISSING


@dataclass
class MentionPolicy:
    # Proven: facebook + instagram mentions are inline text
    # (no mentionFormat in either provider). Kept as policy for
    # future platforms where mentions need API handling.
    style: Any = _MISSING


@dataclass
class MediaPolicy:
    min_items: Any = _MISSING
    max_items: Any = _MISSING


@dataclass
class PlanningPolicy:
    cadence: Any = _MISSING
    days: Any = _MISSING
    times: Any = _MISSING
    daily_count: Any = _MISSING
    weekly_count: Any = _MISSING
    monthly_count: Any = _MISSING
    start_time: Any = _MISSING
    end_time: Any = _MISSING
    spacing_minutes: Any = _MISSING


@dataclass
class ChannelStrategy:
    """Brain strategy attached to ONE Tuzzina integration.
    `integration_id` is the only identity field. Everything else
    is Brain-owned policy and has documented defaults that may come
    from the project (campaign) when unset here.

    Provider settings are NOT part of the strategy. Values that go
    into the Tuzzina post `settings` payload (post types, DTO keys,
    limits) belong to Tuzzina: the injection layer assembles them
    from adapter translation, and live provider truth is fetched
    via TuzzinaClient.get_integration_settings when needed.

    `extras` is forward-compat metadata not consumed by G2 today.
    """
    integration_id: str = ""
    brand: Brand = field(default_factory=Brand)
    hashtags: HashtagPolicy = field(default_factory=HashtagPolicy)
    links_policy: Any = _MISSING
    mentions: MentionPolicy = field(default_factory=MentionPolicy)
    media: MediaPolicy = field(default_factory=MediaPolicy)
    sources: Any = _MISSING
    content: ContentPolicy = field(default_factory=ContentPolicy)
    generation: GenerationPolicy = field(default_factory=GenerationPolicy)
    planning: PlanningPolicy = field(default_factory=PlanningPolicy)
    extras: dict = field(default_factory=dict)


def _is_set(v: Any) -> bool:
    return v is not _MISSING
