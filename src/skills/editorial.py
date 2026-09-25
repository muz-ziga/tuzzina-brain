"""Deterministic editorial pass over model-generated text.

Mechanical hygiene the pipeline owns, so skills and prompts do
not have to be perfect and a second model call is never needed
for what code can prove:

- markdown link/image syntax is flattened (social destinations
  take plain text);
- repeated identical URLs collapse to their first occurrence;
- policy-owned links never duplicate (the publishing step
  appends the call to action / attachment itself);
- hashtag tokens are stripped (hashtag assembly is owned
  downstream by the pipeline, never by the model);
- consecutive duplicate lines collapse;
- whitespace is normalized.

Pure functions, no I/O, no secrets. Model meaning is never
rewritten: only formatting duplicates and markup are removed.
"""
from __future__ import annotations
import re

_MD_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_MD_LINK = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")
_BARE_URL = re.compile(r"https?://[^\s)\"'<>]+")
_HASHTAG = re.compile(r"#[A-Za-z_\u0600-\u06FF][\w\u0600-\u06FF]*")


def _strip_trailing_punct(url: str) -> str:
    return url.rstrip(".,!?:;)\"'")


# Script blocks that must never leak into Arabic output (the
# observed failure mixed Chinese into Arabic). Latin is always
# allowed (brand names, URLs already stripped upstream).
_NON_ARABIC_SCRIPTS = (
    ("Han", "\u4e00", "\u9fff"),
    ("Hiragana", "\u3040", "\u309f"),
    ("Katakana", "\u30a0", "\u30ff"),
    ("Hangul", "\uac00", "\ud7af"),
    ("Cyrillic", "\u0400", "\u04ff"),
)


def script_consistent(text: str, lang: str = "") -> bool:
    """True unless Arabic output carries third-script tokens.
    Detection only (no cleaning: deleting words would mangle
    sentences). Narrow by design: only the demonstrated
    Arabic-mixing failure is gated; all other languages pass.
    Never raises."""
    try:
        if (lang or "").strip().lower() != "ar":
            return True
        for _, lo, hi in _NON_ARABIC_SCRIPTS:
            for ch in text or "":
                if lo <= ch <= hi:
                    return False
        return True
    except Exception:
        return True


def assert_script(text: str, lang: str = "") -> None:
    """Fail closed on third-script noise in Arabic output. Raises
    ValueError (a cycle-level error, never silent bad content)."""
    if not script_consistent(text, lang):
        raise ValueError("script-mismatch:ar")


def append_once(content: str, addition: str) -> str:
    """Append a publisher-owned block exactly once.

    Ownership rule: link/CTA assembly happens in exactly one
    stage, but the pipeline stage and the injection stage both
    route through adapter link handling for legacy reasons. This
    makes the second application a no-op so the composition can
    never duplicate, regardless of call order. Empty additions
    are ignored; comparison ignores trailing whitespace.
    """
    body = content or ""
    block = (addition or "").strip()
    if not block:
        return body.strip()
    if body.rstrip().endswith(block):
        return body.strip()
    return f"{body.strip()}\n\n{block}".strip()


def clean_model_text(text: str, *, links_policy: str = "hide",
                     policy_urls: tuple = (),
                     policy_phrases: tuple = (),
                     strip_hashtags: bool = True) -> str:
    """Clean model output before pipeline assembly.

    links_policy/policy_urls mirror the adapter link handling:
    the publishing step owns link assembly, so the model must
    not smuggle its own copies in. policy_urls holds every URL
    the publisher may append (article link, CTA-embedded link).
    policy_phrases holds publisher-owned text leads (the call to
    action with its URL blanked): a model line that IS such a
    lead is an echoed CTA and is dropped, so the publisher's
    single appended copy stands alone.
    """
    text = text or ""
    # Images never survive: alt text without the file is noise.
    text = _MD_IMAGE.sub("", text)

    def _link_to_text(match: re.Match) -> str:
        label, url = match.group(1), match.group(2)
        url = _strip_trailing_punct((url or "").strip())
        if url:
            seen_urls.add(url.rstrip("/"))
        return (label or "").strip()

    seen_urls: set = set()
    text = _MD_LINK.sub(_link_to_text, text)

    seen: set = set(seen_urls)
    # Policy-owned URLs never come from the model: the publisher
    # appends them exactly once downstream.
    for owned in policy_urls or ():
        owned = _strip_trailing_punct(str(owned or "").strip())
        if owned:
            seen.add(owned.rstrip("/"))

    def _bare_repl(match: re.Match) -> str:
        url = _strip_trailing_punct(match.group(0))
        key = url.rstrip("/")
        if key in seen:
            return ""
        seen.add(key)
        return url

    if (links_policy or "hide") == "hide":
        # Links are hidden: drop every URL the model emitted.
        text = _BARE_URL.sub("", text)
    else:
        text = _BARE_URL.sub(_bare_repl, text)

    # Hashtags: whoever owns them decides. When the campaign's
    # hashtags configuration has them enabled, the pipeline appends
    # them downstream, so a model-authored tag would print twice and
    # is stripped. When that configuration does not enable them, the
    # Text Skill is the only source of hashtags and its output stands.
    if strip_hashtags:
        text = _HASHTAG.sub("", text)

    # Collapse consecutive duplicate lines (the classic echoed
    # call-to-action) and normalize whitespace.
    leads = set()
    for phrase in policy_phrases or ():
        norm = _strip_trailing_punct(
            re.sub(r"\s+", " ", str(phrase or "")).strip())
        norm = norm.rstrip("—–-:").strip().lower()
        if norm:
            leads.add(norm)
    lines: list = []
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped and lines and lines[-1] == stripped:
            continue
        if stripped:
            flat = _strip_trailing_punct(
                re.sub(r"\s+", " ", stripped)).rstrip(
                    "—–-:").strip().lower()
            if flat and flat in leads:
                continue
        lines.append(line)
    text = "\n".join(lines)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


_UNTRUSTED_OPEN = "[UNTRUSTED SOURCE CONTENT BEGINS]"
_UNTRUSTED_CLOSE = "[UNTRUSTED SOURCE CONTENT ENDS]"


def quote_untrusted(text: str) -> str:
    """Wrap external source text in explicit delimiters before it
    enters a model prompt. Trust order (highest wins):

    SYSTEM POLICY > TOOL POLICY > SKILL > TASK > UNTRUSTED
    SOURCE CONTENT.

    Delimiters are a boundary marker, not sanitization: quoted
    text is NEVER cleaned, rewritten, or executed, and quoting
    grants no tool authority (agents expose no tools at all).
    Strict JSON validators at each model boundary remain the
    output gate. Pure function, no I/O."""
    return "%s\n%s\n%s" % (_UNTRUSTED_OPEN, text or "",
                           _UNTRUSTED_CLOSE)
