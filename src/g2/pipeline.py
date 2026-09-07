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
                  image_gen: ImageGenerator | None) -> ContentPackage:
    summary = normalize(item.text)
    text = text_gen.generate(item.title, summary, brand)
    text, banned_hit = apply_banned(text, brand.get("banned_words", []))
    max_tags = int(((hs_cfg.get("per_platform") or {}).get(
        "facebook") or {}).get("max", 5)) if hs_cfg.get("enabled") else 0
    tags = make_hashtags(item.title, summary,
                         brand.get("preferred_words", []), max_tags)
    body = text
    if tags:
        body = f"{body}\n\n{' '.join(tags)}"
    body = apply_link_policy(body, item.source_url, links_policy,
                             brand.get("cta_style", ""))
    media: list = []
    if item.images:
        media.append({"kind": "url", "url": item.images[0],
                      "source": "extracted"})
    elif image_gen is not None:
        media.append({"kind": "generator", "generator": image_gen,
                      "prompt": f"{item.title} :: {summary[:200]}"})
    return ContentPackage(
        title=item.title[:200], content=body, description=summary[:300],
        hashtags=tags, links=[item.source_url] if links_policy == "attach"
        and item.source_url else [],
        media=media, platform="facebook",
        settings={"__type": "facebook", "post_type": "post"},
        source_ref=item.source_id,
        )
