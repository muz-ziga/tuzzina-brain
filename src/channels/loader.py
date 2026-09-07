"""ChannelProfile loader (v2). Reads strategies/<integration_id>.yaml.

The YAML is a Brain strategy, NOT a channel definition. The only
identity field is `integration_id` which must already exist in
Tuzzina (verified at runtime, not at YAML-load time).

Only Brain-owned fields are normalised. The Tuzzina-owned DTO shape
is NOT mirrored here; we accept an opaque `provider_overrides` dict
that goes straight into the Tuzzina post `settings` payload.
"""
from __future__ import annotations
import yaml

from channels.profile import (Brand, ChannelProfile, HashtagPolicy,
                              _MISSING)


def _err(path: str, msg: str) -> ValueError:
    return ValueError(f"channel.{path}: {msg}")


def _opt_str(v):
    return v if v is not None else _MISSING


def _opt_int(v):
    return int(v) if v is not None else _MISSING


def _opt_bool(v):
    return bool(v) if v is not None else _MISSING


def _opt_list(v):
    return list(v or []) if v is not None else _MISSING


def load_channel_profile(path: str) -> ChannelProfile:
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if not isinstance(cfg, dict):
        raise _err("", "must be a mapping")
    integration_id = str(cfg.get("integration_id", "")).strip()
    if not integration_id:
        raise _err("integration_id", "required, non-empty (Tuzzina integration id)")
    links_policy = cfg.get("links_policy", _MISSING)

    b = cfg.get("brand") or {}
    if not isinstance(b, dict):
        raise _err("brand", "must be a mapping")
    brand = Brand(
        name=_opt_str(b.get("name")),
        description=_opt_str(b.get("description")),
        tone=_opt_str(b.get("tone")),
        audience=_opt_str(b.get("audience")),
        banned_words=_opt_list(b.get("banned_words")),
        preferred_words=_opt_list(b.get("preferred_words")),
        cta_style=_opt_str(b.get("cta_style")),
        colors=_opt_list(b.get("colors")),
        logo_url=_opt_str(b.get("logo_url")),
    )

    h = cfg.get("hashtags") or {}
    if not isinstance(h, dict):
        raise _err("hashtags", "must be a mapping")
    hashtags = HashtagPolicy(
        enabled=_opt_bool(h.get("enabled")),
        max=_opt_int(h.get("max")),
        preferred=_opt_list(h.get("preferred")),
    )

    provider_overrides = dict(cfg.get("provider_overrides") or {})
    if not isinstance(provider_overrides, dict):
        raise _err("provider_overrides", "must be a mapping")

    known = {"integration_id", "brand", "hashtags", "links_policy",
             "provider_overrides"}
    extras = {k: v for k, v in cfg.items() if k not in known}

    return ChannelProfile(
        integration_id=integration_id,
        brand=brand,
        hashtags=hashtags,
        links_policy=links_policy,
        provider_overrides=provider_overrides,
        extras=extras,
    )
