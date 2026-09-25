"""Provider-agnostic Content Skill -> prompt instructions.

Pure builder: turns the merged Channel Strategy policy dict
(`resolve_strategy()` output) into a short, human-readable
instruction block. Generators append it verbatim to their prompts,
so brand voice, pillars, CTA, image/visual/video rules and the
creation policy travel with every generation call -- text, image,
video, research analysis alike.

Pure function, no I/O, no secrets. Empty policy -> empty string
(legacy behavior preserved).

Role routing: `build()` carries brand/content/language for the
text-side roles (research, analysis, text) and NEVER visual
identity or visual/video rules. `build_visual()` carries
colors/logo/visual rules for image AND video roles, plus video
rules for video roles only. Research/Analysis/Text prompts must
not receive visual data.

The campaign subject reaches the text-side roles only. The image
and video path keeps its own `visual.brief.subject`, which is a
different concept (what the image must show) and is never fed from
the campaign subject.
"""


def build(policy: dict | None) -> str:
    """Render policy into a prompt instruction block ("" when empty)."""
    p = policy or {}
    brand = p.get("brand") or {}
    content = p.get("content") or {}
    generation = p.get("generation") or {}
    language = p.get("language") or {}
    lines: list[str] = []

    # Campaign subject/domain: DATA for the Skill that owns the role.
    # It is stated, never interpreted here — no research scope, no
    # eligibility, no media or writing rule is derived from it, and
    # the Skills decide what it means for their own stage.
    subject = str(p.get("subject") or "").strip()
    if subject:
        lines.append(f"Campaign subject: {subject[:300]}.")

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

    lang = language.get("output_override") or language.get("default") or ""
    if lang:
        lines.append(f"Write in language: {lang}.")

    if not lines:
        return ""
    return "Channel instructions:\n" + "\n".join(f"- {ln}"
                                                 for ln in lines)


def build_visual(policy: dict | None,
                 video_rules: bool = True) -> str:
    """Render visual identity into a prompt block for image/video
    roles only ("" when empty). Colors/logo come from the merged
    brand; the channel's Visual Brief (`visual.brief`: palette,
    treatment, contrast, subject, character) is rendered field by
    field and every field is optional, so a channel sets only what
    it owns. Legacy `visual_rules` still render. Video rules are
    included only when `video_rules` is true (video roles); image
    roles call with video_rules=False."""
    p = policy or {}
    brand = p.get("brand") or {}
    visual = p.get("visual") or {}
    lines: list[str] = []

    colors = [c for c in list(brand.get("colors") or []) if c]
    if colors:
        lines.append("Brand colors: " + ", ".join(colors) + ".")
    logo = brand.get("logo_url") or ""
    if logo:
        lines.append(f"Brand logo: {logo}.")

    brief = visual.get("brief") or {}
    if isinstance(brief, dict):
        palette = [str(c) for c in list(brief.get("palette") or []) if c]
        if palette:
            lines.append("Palette: " + ", ".join(palette) + ".")
        treatment = [str(t) for t in list(brief.get("treatment") or []) if t]
        if treatment:
            lines.append("Treatment: " + "; ".join(treatment) + ".")
        contrast = [str(c) for c in list(brief.get("contrast") or []) if c]
        if contrast:
            lines.append("Contrast: " + "; ".join(contrast) + ".")
        subject = [str(s) for s in list(brief.get("subject") or []) if s]
        if subject:
            lines.append("Subject and composition: " + "; ".join(subject) + ".")
        character = str(brief.get("character") or "").strip()
        if character:
            lines.append("Visual character: " + character + ".")

    visual_rules = list(visual.get("visual_rules") or [])
    if visual_rules:
        lines.append("Image rules: " + "; ".join(visual_rules) + ".")
    if video_rules:
        rules = list(visual.get("video_rules") or [])
        if rules:
            lines.append("Video rules: " + "; ".join(rules) + ".")

    if not lines:
        return ""
    return "Visual instructions:\n" + "\n".join(f"- {ln}"
                                                for ln in lines)
