"""Channel profile contracts. A ChannelProfile is the per-channel
strategy/policy: language, audience, tone, hashtag policy, link policy,
platform settings, generation provider policy. It is NOT a copy of
G2. G2 stays one pipeline; the channel profile is the policy that
the pipeline consumes.

Resolution: project (campaign) defaults merged with channel override;
channel wins on every leaf. This lets a project set "all channels
Arabic" while a single channel overrides to "English for this one".

Sentinel pattern: dataclass defaults are sentinel objects. This lets
the resolver distinguish "channel did not set this field" from
"channel set it to the dataclass default literal". A plain empty
string can't be used because some fields have meaningful non-empty
defaults (audience='general', cta_style='Learn more', ...).
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
class PlatformSettings:
    """Final settings dict that will reach Tuzzina. Must contain
    __type matching the platform; other fields follow Tuzzina's DTOs.
    Channel profiles fill only what they need; Tuzzina DTOs are
    NOT redefined here."""
    platform: str = "facebook"
    payload: dict = field(default_factory=dict)


@dataclass
class ChannelProfile:
    """Per-channel strategy. Extensible: any new field is just a
    dataclass attribute; the resolver copies it. We do NOT enforce
    every field — unknown keys are preserved in extras for forward
    compatibility with future policies (image_policy, video_policy,
    content_pillars, competitor_set, posting_frequency_preference, ...)."""
    name: str = ""
    platform: Any = _MISSING
    language: Any = _MISSING
    brand: Brand = field(default_factory=Brand)
    hashtags: HashtagPolicy = field(default_factory=HashtagPolicy)
    links_policy: Any = _MISSING
    platform_settings: PlatformSettings = field(default_factory=PlatformSettings)
    extras: dict = field(default_factory=dict)


def _merge_dict(base: dict, override: dict) -> dict:
    out = dict(base or {})
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge_dict(out[k], v)
        else:
            out[k] = v
    return out


def _is_set(v: Any) -> bool:
    return v is not _MISSING


def resolve(project_cfg: dict, channel: ChannelProfile) -> dict:
    """Deep-merge project (campaign) defaults with channel profile.
    Channel wins on every leaf, but a channel field is only
    considered 'set' if it differs from the sentinel
    (i.e. the channel author actually set it). Empty string IS a
    legitimate value ('override to empty'). Unknown future fields
    live in ChannelProfile.extras.

    The shape of the returned dict is identical to the current
    load_campaign() output PLUS channel-specific overrides, so
    pipeline.py and planner.py need no change to the keys they read."""
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
    proj_fb = ((proj_hs.get("per_platform") or {})
               .get("facebook", {}) or {})
    hashtags_merged = {
        "enabled": (bool(h.enabled) if _is_set(h.enabled)
                    else bool(proj_hs.get("enabled", True))),
        "per_platform": {"facebook": {
            "max": (int(h.max) if _is_set(h.max) else int(
                proj_fb.get("max", 5))),
            "preferred": (list(h.preferred) if _is_set(h.preferred)
                          else list(proj_fb.get("preferred") or [])),
        }},
        "rules": list(proj_hs.get("rules") or []),
    }

    return {
        "brand": brand_merged,
        "language": {
            "default": (channel.language
                        if _is_set(channel.language)
                        else proj_lang.get("default", "ar")),
            "output_override": proj_lang.get("output_override"),
        },
        "hashtags": hashtags_merged,
        "links_policy": (channel.links_policy
                         if _is_set(channel.links_policy) else proj_lp),
        "schedule": project_cfg.get("schedule") or {},
        "sources": project_cfg.get("sources") or [],
        "platform": (channel.platform
                     if _is_set(channel.platform) else "facebook"),
        "platform_settings": dict(channel.platform_settings.payload),
        "extras": dict(channel.extras),
    }
