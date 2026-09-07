"""ChannelProfile loader. Reads channels/<name>.<platform>.yaml.
The schema is extensible: only documented fields are normalized; any
other key is preserved verbatim in ChannelProfile.extras for future
fields (image_policy, video_policy, content_pillars, ...).

Only fields that appear in the YAML are set; absent fields keep the
sentinel value in the dataclass, so the resolver can distinguish
"channel did not set this" from "channel set it to the dataclass
default literal"."""
from __future__ import annotations
import yaml

from channels.profile import (Brand, ChannelProfile, HashtagPolicy,
                              PlatformSettings, _MISSING)

VALID_PLATFORMS = ("facebook",)  # others later


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
    platform_v = cfg.get("platform", "facebook")
    if platform_v not in VALID_PLATFORMS:
        raise _err("platform", f"must be one of {VALID_PLATFORMS}")
    name = str(cfg.get("name", "")).strip()
    if not name:
        raise _err("name", "required, non-empty")
    language_v = cfg.get("language", _MISSING)
    links_policy_v = cfg.get("links_policy", _MISSING)

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

    s = cfg.get("platform_settings") or {}
    if not isinstance(s, dict):
        raise _err("platform_settings", "must be a mapping")
    if "__type" not in s:
        s["__type"] = platform_v
    elif s["__type"] != platform_v:
        raise _err("platform_settings.__type",
                   f"must equal platform ({platform_v})")
    platform_settings = PlatformSettings(platform=platform_v, payload=dict(s))

    known = {"platform", "name", "language", "links_policy", "brand",
             "hashtags", "platform_settings"}
    extras = {k: v for k, v in cfg.items() if k not in known}

    return ChannelProfile(name=name,
                         platform=platform_v if platform_v else _MISSING,
                         language=language_v,
                         brand=brand, hashtags=hashtags,
                         links_policy=links_policy_v,
                         platform_settings=platform_settings,
                         extras=extras)
