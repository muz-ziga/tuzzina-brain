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

# Use the strategy module's _MISSING identity so all dataclass
# sentinels in channels.* are comparable across modules.
from channels.strategy import _MISSING as _MISSING, _is_set as _is_set


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

    return _finalize(channel.integration_id, channel_meta or {},
                     brand_merged, hashtags_merged, _is_set(
                         channel.links_policy) and channel.links_policy
                     or proj_lp, project_cfg,
                     dict(channel.provider_overrides), dict(channel.extras),
                     None)


def _finalize(integration_id, channel_meta, brand, hashtags, links_policy,
              project_cfg, provider_overrides, extras,
              extra_policies) -> dict:
    """Common finalizer for both resolvers. `extra_policies` is an
    optional dict with content/generation/planning policy from
    ChannelStrategy; None means it is not provided (ChannelProfile)."""
    proj_lang = project_cfg.get("language") or {}
    return {
        "integration_id": integration_id,
        "brand": brand,
        "language": {
            "default": (channel_meta.get("language")
                        if channel_meta.get("language")
                        else proj_lang.get("default", "ar")),
            "output_override": proj_lang.get("output_override"),
        },
        "hashtags": hashtags,
        "links_policy": links_policy,
        "schedule": project_cfg.get("schedule") or {},
        "sources": project_cfg.get("sources") or [],
        "channel_meta": dict(channel_meta or {}),
        "provider_overrides": dict(provider_overrides),
        "extras": dict(extras),
        "extra_policies": extra_policies or {},
    }


def resolve_strategy(project_cfg: dict, strategy,
                     channel_meta: dict | None = None) -> dict:
    """Same as resolve() but accepts a ChannelStrategy (the new
    richer schema). The ChannelProfile-shaped fields (brand,
    hashtags, links_policy, provider_overrides, extras) are mapped
    through the same merge logic, and the new content/generation/
    planning policies are surfaced in `extra_policies` for future
    G2/G3 steps to consume. None of these fields duplicate channel
    identity; that comes from Tuzzina via channel_meta.
    """
    from channels.strategy import (Brand, ChannelStrategy, ContentPolicy,
                                    GenerationPolicy, HashtagPolicy,
                                    PlanningPolicy)
    _is_set_s = _is_set  # shared sentinel identity (see module import)
    channel_meta = channel_meta or {}
    proj_brand = project_cfg.get("brand") or {}
    proj_lang = project_cfg.get("language") or {}
    proj_hs = project_cfg.get("hashtags") or {}
    proj_lp = project_cfg.get("links_policy", "hide")

    def _b(getter, proj_default):
        v = getter(strategy.brand)
        return v if _is_set_s(v) else proj_default

    brand_merged = {
        "name": _b(lambda b: b.name, proj_brand.get("name", "")),
        "description": "",
        "tone": _b(lambda b: b.tone, proj_brand.get("tone", "neutral")),
        "audience": _b(lambda b: b.audience,
                        proj_brand.get("audience", "general")),
        "banned_words": _b(lambda b: b.banned_words,
                            list(proj_brand.get("banned_words") or [])),
        "preferred_words": _b(lambda b: b.preferred_words,
                               list(proj_brand.get("preferred_words") or [])),
        "cta_style": _b(lambda b: b.cta_style,
                        proj_brand.get("cta_style", "Learn more")),
        "colors": [],
        "logo_url": "",
    }

    h = strategy.hashtags
    proj_plat = ((proj_hs.get("per_platform") or {}).get(
        channel_meta.get("identifier", "default"), {}) or {})
    hashtags_merged = {
        "enabled": (bool(h.enabled) if _is_set_s(h.enabled)
                    else bool(proj_hs.get("enabled", True))),
        "per_platform": {
            channel_meta.get("identifier", "default"): {
                "max": (int(h.max) if _is_set_s(h.max)
                        else int(proj_plat.get("max", 5))),
                "preferred": (list(h.preferred) if _is_set_s(h.preferred)
                              else list(proj_plat.get("preferred") or [])),
            }
        },
        "rules": list(proj_hs.get("rules") or []),
    }

    links = (strategy.links_policy
              if _is_set_s(strategy.links_policy) else proj_lp)

    extra_policies = {
        "content": _content_p(strategy.content),
        "generation": _generation_p(strategy.generation),
        "planning": _planning_p(strategy.planning),
    }

    return _finalize(strategy.integration_id, channel_meta, brand_merged,
                     hashtags_merged, links, project_cfg,
                     dict(strategy.provider_overrides),
                     dict(strategy.extras), extra_policies)


def _content_p(c) -> dict:
    out = {}
    if _is_set(c.content_pillars):
        out["content_pillars"] = list(c.content_pillars)
    if _is_set(c.sources_policy):
        out["sources_policy"] = c.sources_policy
    return out


def _generation_p(g) -> dict:
    out = {}
    for k in ("provider", "prompt_style"):
        v = getattr(g, k)
        if _is_set(v):
            out[k] = v
    return out


def _planning_p(p) -> dict:
    out = {}
    for k in ("cadence", "days", "times"):
        v = getattr(p, k)
        if _is_set(v):
            out[k] = v
    return out
