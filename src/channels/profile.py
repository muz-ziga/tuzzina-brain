"""Channel strategy resolution. Merges project (campaign) defaults
with a ChannelStrategy keyed by integration_id. Channel wins on
every leaf it sets; absent fields fall back to the project.

Tuzzina owns integration identity, platform, OAuth, and provider
DTOs. This module only merges Brain-owned policy. Empty string IS
a legitimate override value.
"""
from __future__ import annotations
from typing import Any

# Use the strategy module's _MISSING identity so all dataclass
# sentinels in channels.* are comparable across modules.
from channels.strategy import _MISSING as _MISSING, _is_set as _is_set


def _finalize(integration_id, channel_meta, brand, hashtags, links_policy,
              project_cfg, extras) -> dict:
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
        "extras": dict(extras),
    }


def resolve_strategy(project_cfg: dict, strategy,
                     channel_meta: dict | None = None) -> dict:
    """Merge project (campaign) defaults with a ChannelStrategy.
    Channel wins on every leaf it sets; absent fields fall back to
    the project. None of these fields duplicate channel identity;
    that comes from Tuzzina via channel_meta.
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
        "voice": _b(lambda b: b.voice, proj_brand.get("voice", "")),
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

    # Sources precedence: channel strategy sources win; campaign
    # default sources are the fallback. An explicitly EMPTY channel
    # list also falls back (empty = "no sources here"). Documented.
    if _is_set_s(strategy.sources) and list(strategy.sources or []):
        sources = [dict(s) for s in strategy.sources]
        sources_from = "channel"
    else:
        sources = list(project_cfg.get("sources") or [])
        sources_from = "campaign"

    mentions = {"style": (strategy.mentions.style
                          if _is_set_s(strategy.mentions.style)
                          else "inline")}
    media = {}
    if _is_set_s(strategy.media.min_items):
        media["min_items"] = int(strategy.media.min_items)
    if _is_set_s(strategy.media.max_items):
        media["max_items"] = int(strategy.media.max_items)

    # Content/generation/visual policy: channel strategy is the
    # source (campaign carries none today); absent stays default.
    content = {
        "content_pillars": list(strategy.content.content_pillars)
        if _is_set_s(strategy.content.content_pillars) else [],
        "sources_policy": strategy.content.sources_policy
        if _is_set_s(strategy.content.sources_policy) else "",
    }
    generation = {
        "provider": strategy.generation.provider
        if _is_set_s(strategy.generation.provider) else "auto",
        "prompt_style": strategy.generation.prompt_style
        if _is_set_s(strategy.generation.prompt_style) else "",
    }
    visual = {
        "visual_rules": list(strategy.visual.visual_rules)
        if _is_set_s(strategy.visual.visual_rules) else [],
        "video_rules": list(strategy.visual.video_rules)
        if _is_set_s(strategy.visual.video_rules) else [],
    }

    out = _finalize(strategy.integration_id, channel_meta, brand_merged,
                    hashtags_merged, links, project_cfg,
                    dict(strategy.extras))
    out["sources"] = sources
    out["sources_from"] = sources_from
    out["mentions"] = mentions
    out["media_policy"] = media
    out["content"] = content
    out["generation"] = generation
    out["visual"] = visual
    # Strategy planning merges over campaign schedule (channel wins
    # per leaf; G3 reads only this merged schedule dict).
    sched = dict(out.get("schedule") or {})
    sp = strategy.planning
    for k in ("days", "times", "daily_count", "weekly_count",
              "monthly_count", "start_time", "end_time",
              "spacing_minutes", "cadence"):
        v = getattr(sp, k, None)
        try:
            is_set = _is_set_s(v)
        except Exception:
            is_set = v is not None
        if is_set:
            sched[k] = v
    out["schedule"] = sched
    return out
