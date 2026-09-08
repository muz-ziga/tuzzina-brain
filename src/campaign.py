"""Campaign YAML loading + validation. Only brand.name is required;
sources[] is optional (channel strategy sources win; campaign sources
are the fallback when the strategy has none). Everything else has
documented defaults."""
from __future__ import annotations
import yaml

VALID_LINK_POLICIES = ("attach", "cta", "hide")
VALID_SOURCE_TYPES = ("website",)  # rss/facebook/... later


def _err(path: str, msg: str) -> ValueError:
    return ValueError(f"campaign.{path}: {msg}")


def load_campaign(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if not isinstance(cfg, dict):
        raise _err("", "must be a mapping")
    brand = cfg.get("brand") or {}
    if not isinstance(brand, dict) or not str(brand.get("name", "")).strip():
        raise _err("brand.name", "required, non-empty")
    sources = cfg.get("sources") or []
    if not isinstance(sources, list):
        raise _err("sources", "must be a list")
    for i, s in enumerate(sources):
        if not isinstance(s, dict):
            raise _err(f"sources[{i}]", "must be a mapping")
        if s.get("type") not in VALID_SOURCE_TYPES:
            raise _err(f"sources[{i}].type",
                       f"must be one of {VALID_SOURCE_TYPES}")
        if not str(s.get("url", "")).strip():
            raise _err(f"sources[{i}].url", "required")
        n = s.get("n", 3)
        if not isinstance(n, int) or n < 1:
            raise _err(f"sources[{i}].n", "must be int >= 1")
    lp = cfg.get("links_policy", "hide")
    if lp not in VALID_LINK_POLICIES:
        raise _err("links_policy", f"must be one of {VALID_LINK_POLICIES}")
    lang = cfg.get("language") or {}
    hs = cfg.get("hashtags") or {}
    sched = cfg.get("schedule") or {}
    return {
        "brand": {
            "name": str(brand["name"]).strip(),
            "description": str(brand.get("description", "")),
            "tone": str(brand.get("tone", "neutral")),
            "audience": str(brand.get("audience", "general")),
            "banned_words": list(brand.get("banned_words") or []),
            "preferred_words": list(brand.get("preferred_words") or []),
            "cta_style": str(brand.get("cta_style", "Learn more")),
            "colors": list(brand.get("colors") or []),
            "logo_url": str(brand.get("logo_url", "")),
        },
        "language": {"default": str(lang.get("default", "ar")),
                     "output_override": lang.get("output_override")},
        "hashtags": {"enabled": bool(hs.get("enabled", True)),
                     "per_platform": hs.get("per_platform")
                     or {"facebook": {"max": 5}},
                     "rules": list(hs.get("rules") or [])},
        "links_policy": lp,
        "sources": [{"type": s["type"], "url": str(s["url"]).strip(),
                     "n": int(s.get("n", 3))} for s in sources],
        "schedule": {"timezone": str(sched.get("timezone", "UTC")),
                     "days": list(sched.get("days") or []),
                     "times": list(sched.get("times") or [])},
    }
