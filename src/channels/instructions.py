"""Provider-agnostic Content Skill -> prompt instructions.

Pure builder: turns the merged Channel Strategy policy dict
(`resolve_strategy()` output) into a short, human-readable
instruction block. Generators append it verbatim to their prompts,
so brand voice, pillars, CTA, image/visual/video rules and the
creation policy travel with every generation call -- text, image,
video, research analysis alike.

Pure function, no I/O, no secrets. Empty policy -> empty string
(legacy behavior preserved).
"""


def build(policy: dict | None) -> str:
    """Render policy into a prompt instruction block ("" when empty)."""
    p = policy or {}
    brand = p.get("brand") or {}
    content = p.get("content") or {}
    generation = p.get("generation") or {}
    visual = p.get("visual") or {}
    language = p.get("language") or {}
    lines: list[str] = []

    tone = brand.get("tone") or ""
    audience = brand.get("audience") or ""
    if tone or audience:
        lines.append(f"Brand voice: {tone or 'neutral'}; "
                     f"audience: {audience or 'general'}.")
    voice = brand.get("voice") or ""
    if voice:
        lines.append(f"Writing voice: {voice}.")
    banned = list(brand.get("banned_words") or [])
    if banned:
        lines.append("Never use these words: " + ", ".join(banned) + ".")
    preferred = list(brand.get("preferred_words") or [])
    if preferred:
        lines.append("Prefer these words: " + ", ".join(preferred) + ".")
    cta = brand.get("cta_style") or ""
    if cta:
        lines.append(f"Call to action style: {cta}.")

    pillars = list(content.get("content_pillars") or [])
    if pillars:
        lines.append("Stay within these content pillars: " +
                     "; ".join(pillars) + ".")
    sources_policy = content.get("sources_policy") or ""
    if sources_policy:
        lines.append(f"Source policy: {sources_policy}.")

    prompt_style = generation.get("prompt_style") or ""
    if prompt_style:
        lines.append(f"Generation style: {prompt_style}.")

    visual_rules = list(visual.get("visual_rules") or [])
    if visual_rules:
        lines.append("Image rules: " + "; ".join(visual_rules) + ".")
    video_rules = list(visual.get("video_rules") or [])
    if video_rules:
        lines.append("Video rules: " + "; ".join(video_rules) + ".")

    lang = language.get("output_override") or language.get("default") or ""
    if lang:
        lines.append(f"Write in language: {lang}.")

    if not lines:
        return ""
    return "Channel instructions:\n" + "\n".join(f"- {ln}"
                                                 for ln in lines)
