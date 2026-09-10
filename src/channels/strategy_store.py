"""Strategy file store. One YAML per Tuzzina integration, kept in
`brain-strategies/<integration_id>.yaml`. Brain-owned policy only;
the integration_id is the only bridge to Tuzzina.

The store is reusable: select.py and run.py both call into it.
When a Brain API/Frontend replaces the CLI, the store is the only
Brain-side file-logic that needs to stay the same.

Path safety: integration_id is validated to a strict regex
([a-zA-Z0-9_-]+) BEFORE it touches the filesystem. No user
input is ever passed to open()/Path() without going through the
validator first.
"""
from __future__ import annotations
import re
import tempfile
import yaml
from pathlib import Path
from typing import Any

from channels.strategy import ChannelStrategy, _is_set


VALID_ID = re.compile(r"^[A-Za-z0-9_\-]{1,128}$")


def _err(msg: str) -> ValueError:
    return ValueError(f"strategy: {msg}")


def validate_integration_id(integration_id: Any) -> str:
    if not isinstance(integration_id, str):
        raise _err(f"integration_id must be a string, got "
                   f"{type(integration_id).__name__}")
    if not integration_id:
        raise _err("integration_id is required")
    if not VALID_ID.match(integration_id):
        raise _err(f"integration_id {integration_id!r} does not match "
                   f"the allowed pattern (alnum + _-)")
    return integration_id


def path_for(integration_id: str, base_dir: str | None = None) -> Path:
    """Resolve the YAML path for a strategy. The integration_id is
    the filename, so two strategies can never collide, and the
    filename cannot be a relative path that escapes the base dir."""
    safe_id = validate_integration_id(integration_id)
    base = Path(base_dir) if base_dir else Path("brain-strategies")
    return base / f"{safe_id}.yaml"


def save(strategy: ChannelStrategy, base_dir: str | None = None) -> Path:
    """Persist a strategy. Atomic write via temp file + rename so a
    crash mid-write cannot corrupt the file. The integration_id is
    re-validated to defend against programmatic construction."""
    safe_id = validate_integration_id(strategy.integration_id)
    if safe_id != strategy.integration_id:
        raise _err("internal: integration_id roundtrip mismatch")
    target = path_for(safe_id, base_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = _to_yaml_dict(strategy)
    fd, tmp = tempfile.mkstemp(prefix=f".{safe_id}.",
                               suffix=".tmp",
                               dir=str(target.parent))
    try:
        with open(fd, "w", encoding="utf-8") as f:
            yaml.safe_dump(payload, f, allow_unicode=True,
                            sort_keys=False)
        Path(tmp).replace(target)
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise
    return target


def load(integration_id: str, base_dir: str | None = None) -> ChannelStrategy:
    """Read a strategy from disk. Raises FileNotFoundError if the
    file does not exist. The integration_id is validated before the
    filesystem is touched."""
    target = path_for(integration_id, base_dir)
    with open(target, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if not isinstance(cfg, dict):
        raise _err(f"{target}: must be a mapping")
    if cfg.get("integration_id") != integration_id:
        raise _err(f"{target}: integration_id in file does not match "
                   f"the requested id")
    return _from_yaml_dict(cfg)


def exists(integration_id: str, base_dir: str | None = None) -> bool:
    try:
        return path_for(integration_id, base_dir).is_file()
    except ValueError:
        return False


def list_stored(base_dir: str | None = None) -> list[str]:
    """List strategy file names (= integration ids) currently stored.
    The directory itself must already exist; the list reflects files,
    not a separate registry."""
    base = Path(base_dir) if base_dir else Path("brain-strategies")
    if not base.is_dir():
        return []
    ids = []
    for p in base.glob("*.yaml"):
        name = p.stem
        if VALID_ID.match(name):
            ids.append(name)
    return sorted(ids)


# ---------- (de)serialization ----------

def _to_yaml_dict(s: ChannelStrategy) -> dict:
    """Brain-owned strategy only. The integration_id is the only
    field that references Tuzzina-owned identity, and it is kept
    here as a reference, not as a source of truth."""
    out: dict = {"integration_id": s.integration_id, "strategy": {}}
    strat = out["strategy"]
    strat["brand"] = _brand_to(s.brand)
    if _is_set(s.language.default) or _is_set(s.language.output_override):
        strat["language"] = _language_to(s.language)
    if _is_set(s.hashtags.enabled) or _is_set(s.hashtags.max) or \
            _is_set(s.hashtags.preferred):
        strat["hashtags"] = _hash_to(s.hashtags)
    if _is_set(s.links_policy):
        strat["links_policy"] = s.links_policy
    if _is_set(s.mentions.style):
        strat["mentions"] = {"style": s.mentions.style}
    if _is_set(s.media.min_items) or _is_set(s.media.max_items):
        strat["media"] = _media_to(s.media)
    if _is_set(s.visual.visual_rules) or _is_set(s.visual.video_rules):
        strat["visual"] = _visual_to(s.visual)
    if _is_set(s.sources):
        strat["sources"] = [dict(x) for x in s.sources]
    if _is_set(s.content.content_pillars) or \
            _is_set(s.content.sources_policy):
        strat["content"] = _content_to(s.content)
    if _is_set(s.generation.provider) or \
            _is_set(s.generation.prompt_style):
        strat["generation"] = _generation_to(s.generation)
    if _is_set(s.planning.cadence) or _is_set(s.planning.days) or \
            _is_set(s.planning.times) or _is_set(s.planning.daily_count) or \
            _is_set(s.planning.weekly_count) or \
            _is_set(s.planning.monthly_count) or \
            _is_set(s.planning.start_time) or \
            _is_set(s.planning.end_time) or \
            _is_set(s.planning.spacing_minutes):
        strat["planning"] = _planning_to(s.planning)
    if s.extras:
        strat["extras"] = dict(s.extras)
    return out


def _brand_to(b) -> dict:
    out = {}
    for k in ("tone", "audience", "banned_words", "preferred_words",
              "cta_style", "voice", "logo_url"):
        v = getattr(b, k)
        if _is_set(v):
            out[k] = list(v) if k in ("banned_words", "preferred_words") else v
    if _is_set(b.colors):
        out["colors"] = list(b.colors)
    return out


def _language_to(l) -> dict:
    out = {}
    if _is_set(l.default):
        out["default"] = l.default
    if _is_set(l.output_override):
        out["output_override"] = l.output_override
    return out


def _hash_to(h) -> dict:
    out = {}
    for k in ("enabled", "max", "preferred"):
        v = getattr(h, k)
        if _is_set(v):
            out[k] = v
    return out


def _content_to(c) -> dict:
    out = {}
    if _is_set(c.content_pillars):
        out["content_pillars"] = list(c.content_pillars)
    if _is_set(c.sources_policy):
        out["sources_policy"] = c.sources_policy
    return out


def _generation_to(g) -> dict:
    out = {}
    for k in ("provider", "prompt_style"):
        v = getattr(g, k)
        if _is_set(v):
            out[k] = v
    return out


def _media_to(m) -> dict:
    out = {}
    if _is_set(m.min_items):
        out["min_items"] = int(m.min_items)
    if _is_set(m.max_items):
        out["max_items"] = int(m.max_items)
    return out


def _visual_to(v) -> dict:
    out = {}
    if _is_set(v.visual_rules):
        out["visual_rules"] = list(v.visual_rules)
    if _is_set(v.video_rules):
        out["video_rules"] = list(v.video_rules)
    return out


def _planning_to(p) -> dict:
    out = {}
    for k in ("cadence", "days", "times", "daily_count", "weekly_count",
              "monthly_count", "start_time", "end_time", "spacing_minutes"):
        v = getattr(p, k)
        if _is_set(v):
            out[k] = v
    return out


def _from_yaml_dict(cfg: dict) -> ChannelStrategy:
    from channels.strategy import (Brand, ChannelStrategy, ContentPolicy,
                                    GenerationPolicy, HashtagPolicy,
                                    LanguagePolicy, MediaPolicy,
                                    MentionPolicy, PlanningPolicy,
                                    VisualPolicy, _MISSING)

    integ = cfg.get("integration_id", "")
    strat_cfg = cfg.get("strategy") or {}
    if not isinstance(strat_cfg, dict):
        raise _err("'strategy' must be a mapping")

    def _opt_list(v):
        return list(v or []) if v is not None else _MISSING

    def _opt_str(v):
        return v if v is not None else _MISSING

    def _opt_int(v):
        return int(v) if v is not None else _MISSING

    def _opt_bool(v):
        return bool(v) if v is not None else _MISSING

    def _opt_sources(v):
        if v is None:
            return _MISSING
        if not isinstance(v, list):
            raise _err("'strategy.sources' must be a list")
        out = []
        for i, s in enumerate(v):
            if not isinstance(s, dict):
                raise _err(f"'strategy.sources[{i}]' must be a mapping")
            if s.get("type") not in ("website",):
                raise _err(f"'strategy.sources[{i}].type' unsupported")
            if not str(s.get("url", "")).strip():
                raise _err(f"'strategy.sources[{i}].url' required")
            n = s.get("n", 3)
            if not isinstance(n, int) or n < 1:
                raise _err(f"'strategy.sources[{i}].n' must be int >= 1")
            out.append({"type": s["type"], "url": str(s["url"]).strip(),
                        "n": int(n)})
        return out

    b = strat_cfg.get("brand") or {}
    _colors = b.get("colors")
    if _colors is not None and (not isinstance(_colors, list) or
                                not all(isinstance(x, str)
                                        for x in _colors)):
        raise _err("'strategy.brand.colors' must be a list of strings")
    _logo = b.get("logo_url")
    if _logo is not None and not isinstance(_logo, str):
        raise _err("'strategy.brand.logo_url' must be a string")
    brand = Brand(
        tone=_opt_str(b.get("tone")),
        audience=_opt_str(b.get("audience")),
        banned_words=_opt_list(b.get("banned_words")),
        preferred_words=_opt_list(b.get("preferred_words")),
        cta_style=_opt_str(b.get("cta_style")),
        voice=_opt_str(b.get("voice")),
        colors=_opt_list(_colors),
        logo_url=_opt_str(_logo),
    )

    lg = strat_cfg.get("language") or {}
    if not isinstance(lg, dict):
        raise _err("'strategy.language' must be a mapping")
    for _lk in ("default", "output_override"):
        _lv = lg.get(_lk)
        if _lv is not None and not isinstance(_lv, str):
            raise _err(f"'strategy.language.{_lk}' must be a string")
    language = LanguagePolicy(
        default=_opt_str(lg.get("default")),
        output_override=_opt_str(lg.get("output_override")),
    )

    h = strat_cfg.get("hashtags") or {}
    hashtags = HashtagPolicy(
        enabled=_opt_bool(h.get("enabled")),
        max=_opt_int(h.get("max")),
        preferred=_opt_list(h.get("preferred")),
    )

    c = strat_cfg.get("content") or {}
    content = ContentPolicy(
        content_pillars=_opt_list(c.get("content_pillars")),
        sources_policy=c.get("sources_policy", _MISSING)
            if c.get("sources_policy") is not None else _MISSING,
    )

    g = strat_cfg.get("generation") or {}
    generation = GenerationPolicy(
        provider=_opt_str(g.get("provider")),
        prompt_style=_opt_str(g.get("prompt_style")),
    )

    p = strat_cfg.get("planning") or {}
    planning = PlanningPolicy(
        cadence=_opt_str(p.get("cadence")),
        days=_opt_list(p.get("days")),
        times=_opt_list(p.get("times")),
        daily_count=_opt_int(p.get("daily_count")),
        weekly_count=_opt_int(p.get("weekly_count")),
        monthly_count=_opt_int(p.get("monthly_count")),
        start_time=_opt_str(p.get("start_time")),
        end_time=_opt_str(p.get("end_time")),
        spacing_minutes=_opt_int(p.get("spacing_minutes")),
    )

    mn = strat_cfg.get("mentions") or {}
    if not isinstance(mn, dict):
        raise _err("'strategy.mentions' must be a mapping")
    mentions = MentionPolicy(style=_opt_str(mn.get("style")))

    md = strat_cfg.get("media") or {}
    if not isinstance(md, dict):
        raise _err("'strategy.media' must be a mapping")
    media = MediaPolicy(
        min_items=_opt_int(md.get("min_items")),
        max_items=_opt_int(md.get("max_items")),
    )

    vi = strat_cfg.get("visual") or {}
    if not isinstance(vi, dict):
        raise _err("'strategy.visual' must be a mapping")
    for _vk in ("visual_rules", "video_rules"):
        _vv = vi.get(_vk)
        if _vv is not None and (not isinstance(_vv, list) or
                                not all(isinstance(x, str) for x in _vv)):
            raise _err(f"'strategy.visual.{_vk}' must be a list of strings")
    visual = VisualPolicy(
        visual_rules=_opt_list(vi.get("visual_rules")),
        video_rules=_opt_list(vi.get("video_rules")),
    )

    if "provider_overrides" in strat_cfg:
        raise _err("'strategy.provider_overrides' was removed: provider "
                   "settings belong to Tuzzina, not to Brain strategy")
    extras = strat_cfg.get("extras") or {}
    if not isinstance(extras, dict):
        raise _err("'strategy.extras' must be a mapping")

    return ChannelStrategy(
        integration_id=integ,
        brand=brand,
        language=language,
        hashtags=hashtags,
        links_policy=strat_cfg.get("links_policy", _MISSING),
        mentions=mentions,
        media=media,
        sources=_opt_sources(strat_cfg.get("sources")),
        content=content,
        generation=generation,
        planning=planning,
        visual=visual,
        extras=dict(extras),
    )
