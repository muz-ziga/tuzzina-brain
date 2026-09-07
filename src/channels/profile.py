"""Channel profile contracts (v2). A ChannelProfile is Brain's
*strategy attached to* a Tuzzina integration. It does NOT define
the channel: Tuzzina owns integration identity, platform, OAuth, and
provider DTOs. Brain only contributes:
  - brand voice, audience, tone
  - hashtag policy
  - link policy
  - generation provider policy
  - source strategy, competitor set
  - planning preferences
  - any future per-channel strategy (extensible via extras)

The integration_id is the only bridge: it ties the strategy to a
specific Tuzzina integration (channel) discovered at runtime via
GET /public/v1/integrations.

Resolution: project (campaign) defaults are merged with the channel
profile keyed by integration_id. Channel wins on every leaf it sets;
absent fields fall back to the project.

Sentinel pattern: dataclass defaults are sentinel objects so the
resolver can distinguish 'channel did not set this' from 'channel
explicitly set it to the dataclass default literal'. Empty string IS
a legitimate override value.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


_MISSING = object()


@dataclass
class Brand:
    name: Any = _MISSING
    description: Any = _MISSING
    tone: Any = _MISSING
    audience: Any = _MISSING
    banned_words: Any = _MISSING
    preferred_words: Any = _MISSING
    cta_style: Any = _MISSING
    colors: Any = _MISSING
    logo_url: Any = _MISSING


@dataclass
class HashtagPolicy:
    enabled: Any = _MISSING
    max: Any = _MISSING
    preferred: Any = _MISSING


@dataclass
class ChannelProfile:
    """Brain strategy attached to ONE Tuzzina integration.
    identity (integration_id) is the only required field; everything
    else has a documented default that can come from the project
    (campaign) or stay unset for Tuzzina's own defaults.

    `provider_overrides` is a plain dict for values that go straight
    into the Tuzzina post `settings` payload. We do NOT name it
    after a specific platform DTO and we do NOT validate its keys
    here: Tuzzina is the owner of DTO shape and validation.

    `extras` is forward-compat metadata (content_pillars, image_policy,
    video_policy, competitor_set, posting_frequency_preference, ...).
    G2 may read these in future steps; for now they are advisory.
    """
    integration_id: str = ""
    brand: Brand = field(default_factory=Brand)
    hashtags: HashtagPolicy = field(default_factory=HashtagPolicy)
    links_policy: Any = _MISSING
    provider_overrides: dict = field(default_factory=dict)
    extras: dict = field(default_factory=dict)


def _is_set(v: Any) -> bool:
    return v is not _MISSING


def resolve(project_cfg: dict, channel: ChannelProfile,
            channel_meta: dict | None = None) -> dict:
    """Deep-merge project (campaign) defaults with the channel
    profile. Channel wins on every leaf it sets; absent fields fall
    back to the project. Empty string IS a legitimate override.

    `channel_meta` is an OPTIONAL hint dict (e.g. from Tuzzina's
    integration record: {platform, name, picture, ...}) for callers
    that want it surfaced in the resolved config for logging/
    auditing. The brain never assumes channel_meta is present and
    never uses it to fabricate identity.
    """
    proj_brand = project_cfg.get("brand") or {}
    proj_lang = project_cfg.get("language") or {}
    proj_hs = project_cfg.get("hashtags") or {}
    proj_lp = project_cfg.get("links_policy", "hide")

    def _b(getter, proj_default):
        v = getter(channel.brand)
        return v if _is_set(v) else proj_default

    brand_merged = {
        "name": _b(lambda b: b.name, proj_brand.get("name", "")),
        "description": _b(lambda b: b.description,
                          proj_brand.get("description", "")),
        "tone": _b(lambda b: b.tone, proj_brand.get("tone", "neutral")),
        "audience": _b(lambda b: b.audience,
                        proj_brand.get("audience", "general")),
        "banned_words": _b(lambda b: b.banned_words,
                            list(proj_brand.get("banned_words") or [])),
        "preferred_words": _b(lambda b: b.preferred_words,
                               list(proj_brand.get("preferred_words") or [])),
        "cta_style": _b(lambda b: b.cta_style,
                        proj_brand.get("cta_style", "Learn more")),
        "colors": _b(lambda b: b.colors,
                     list(proj_brand.get("colors") or [])),
        "logo_url": _b(lambda b: b.logo_url,
                       proj_brand.get("logo_url", "")),
    }

    h = channel.hashtags
    proj_plat = ((proj_hs.get("per_platform") or {}).get(
        (channel_meta or {}).get("identifier", "default"), {}) or {})
    hashtags_merged = {
        "enabled": (bool(h.enabled) if _is_set(h.enabled)
                    else bool(proj_hs.get("enabled", True))),
        "per_platform": {
            (channel_meta or {}).get("identifier", "default"): {
                "max": (int(h.max) if _is_set(h.max)
                        else int(proj_plat.get("max", 5))),
                "preferred": (list(h.preferred) if _is_set(h.preferred)
                              else list(proj_plat.get("preferred") or [])),
            }
        },
        "rules": list(proj_hs.get("rules") or []),
    }

    return {
        "integration_id": channel.integration_id,
        "brand": brand_merged,
        "language": {
            "default": ((channel_meta or {}).get("language")
                        if (channel_meta or {}).get("language")
                        else proj_lang.get("default", "ar")),
            "output_override": proj_lang.get("output_override"),
        },
        "hashtags": hashtags_merged,
        "links_policy": (channel.links_policy
                         if _is_set(channel.links_policy) else proj_lp),
        "schedule": project_cfg.get("schedule") or {},
        "sources": project_cfg.get("sources") or [],
        "channel_meta": dict(channel_meta or {}),
        "provider_overrides": dict(channel.provider_overrides),
        "extras": dict(channel.extras),
    }
