"""G2 pipeline: normalize -> brand rules -> text -> hashtags -> links -> package."""
from __future__ import annotations
import re
from contracts import ContentPackage, SourceItem
from g2.generators import ImageGenerator, TextGenerator, _words


def normalize(text: str, limit: int = 1500) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()[:limit]


def apply_banned(text: str, banned: list) -> tuple[str, list]:
    hit = []
    for w in banned or []:
        if w and w in text:
            hit.append(w)
            text = text.replace(w, "")
    return re.sub(r"\s+", " ", text).strip(), hit


def make_hashtags(title: str, summary: str, preferred: list,
                  max_n: int) -> list[str]:
    tags: list[str] = []
    for p in preferred or []:
        t = "#" + re.sub(r"\s+", "_", str(p).strip().strip("#"))
        if t not in tags:
            tags.append(t)
    for w in _words(f"{title} {summary}"):
        t = "#" + re.sub(r"[^\w\u0600-\u06FF]", "", w)
        if len(t) > 2 and t not in tags:
            tags.append(t)
        if len(tags) >= max_n:
            break
    return tags[:max(0, max_n)]


def apply_link_policy(content: str, url: str, policy: str,
                      cta_style: str) -> str:
    if policy == "attach" and url:
        return f"{content}\n\n{url}".strip()
    if policy == "cta":
        cta = (cta_style or "Learn more").strip()
        return f"{content}\n\n{cta}".strip()
    return content  # hide


def build_package(item: SourceItem, brand: dict, lang_cfg: dict,
                  hs_cfg: dict, links_policy: str,
                  text_gen: TextGenerator,
                  image_gen: ImageGenerator | None,
                   platform: str = "facebook",
                   platform_settings: dict | None = None,
                   limits: dict | None = None,
                   link_fn=None,
                   policy: dict | None = None) -> ContentPackage:
    """limits (from the PlatformAdapter, optional):
      max_length: truncate body to this many chars.
    link_fn (from the PlatformAdapter, optional): replaces the
      generic apply_link_policy so each platform owns link rules
      (e.g. Instagram ignores attach). Defaults to the generic one.
    Backward compatible: omitting limits/link_fn preserves behavior."""
    summary = normalize(item.text)
    text = text_gen.generate(item.title, summary, brand, policy)
    text, banned_hit = apply_banned(text, brand.get("banned_words", []))
    max_tags = int(((hs_cfg.get("per_platform") or {}).get(
        platform) or {}).get("max", 5)) if hs_cfg.get("enabled") else 0
    preferred = ((hs_cfg.get("per_platform") or {}).get(platform) or
                {}).get("preferred", brand.get("preferred_words", []))
    tags = make_hashtags(item.title, summary, preferred, max_tags)
    # NOTE: hashtags are NOT merged into body here. pkg.content stays
    # plain generated text; pkg.hashtags carries the tags separately.
    # The PlatformAdapter (shape_text) owns final assembly, so tags
    # appear exactly once per platform rules.
    linker = link_fn or apply_link_policy
    body = linker(text, item.source_url, links_policy,
                  brand.get("cta_style", ""))
    cap = limits.get("max_length") if limits else None
    if isinstance(cap, int) and cap > 0:
        body = body[:cap]
    media: list = []
    if item.images:
        media.append({"kind": "url", "url": item.images[0],
                      "source": "extracted"})
    elif image_gen is not None:
        from channels.instructions import build as _skill
        from channels.instructions import build_visual as _visual
        _iprompt = f"{item.title} :: {summary[:200]}"
        _skill_block = _skill(policy)
        if _skill_block:
            _iprompt += "\n" + _skill_block
        _visual_block = _visual(policy)
        if _visual_block:
            _iprompt += "\n" + _visual_block
        media.append({"kind": "generator", "generator": image_gen,
                      "prompt": _iprompt})
    settings = {"__type": platform}
    if platform_settings:
        for k, v in platform_settings.items():
            if k != "__type":
                settings[k] = v
    return ContentPackage(
        title=item.title[:200], content=body, description=summary[:300],
        hashtags=tags, links=[item.source_url] if links_policy == "attach"
        and item.source_url else [],
        media=media, platform=platform,
        settings=settings,
        source_ref=item.source_id,
        )
