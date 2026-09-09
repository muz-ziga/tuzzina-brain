"""Generation orchestration (R6): opportunity -> explicit requests.

Decision ONLY: which generation roles run, with what inputs. No
engine runs here, no bytes, no uploads, no Tuzzina calls, no
scheduling. Execution stays where it is (text engine inside G2,
media resolution in the run.py execution path).

- GenerationPlan: text request (always, when eligible), image
  request (iff opportunity.media_intent == "image"), video
  request (iff == "video"). Video is INTENT ONLY: no executor
  exists, so carriers must record it as deferred, never attempt
  it and never silently drop it.
- plan_generation(): pure decision from opportunity (+items for
  fallback titles only).
- shape_item_for_g2(): SourceItem copy with plan-shaped title /
  text so opportunity facts/angle actually reach the text engine
  (trace ids, images, refs, hashes preserved untouched).
- image_gen_for(): gates the pipeline's generator entry — media
  "none" yields None so text-only stays text-only (the pipeline
  adds a generator entry whenever image_gen is not None).

Formats stay at the Analysis vocabulary ("post" + none|image|
video). story/reel/carousel do not exist in the domain; adding
them is future work, not silent invention here.
"""
from __future__ import annotations
from dataclasses import dataclass, field, replace


class GenerationError(Exception):
    """Plan misuse (no opportunity, no eligible decision). Domain
    programmer error: loud, never a silent downgrade."""


@dataclass
class TextRequest:
    title: str
    summary: str


@dataclass
class ImageRequest:
    prompt: str


@dataclass
class VideoRequest:
    prompt: str
    # Tuzzina template selector + output shape + opaque template
    # params. All three are caller-supplied strings/dicts that
    # Tuzzina validates: `video_type` is a TEMPLATE identifier
    # (never a provider/model catalog — Brain stores no such
    # table), `output` is vertical|horizontal, `params` rides
    # into Tuzzina customParams untouched.
    video_type: str = ""
    output: str = ""
    params: dict = field(default_factory=dict)


@dataclass
class GenerationPlan:
    text: object = None  # TextRequest | None
    image: object = None  # ImageRequest | None
    video: object = None  # VideoRequest | None
    media_intent: str = "none"
    # Executable format token (research.formats vocabulary),
    # assigned by the caller after the allowlist gate.
    format: str = ""
    meta: dict = field(default_factory=dict)


def _prompt_for(topic: str, facts: list,
                policy: dict | None = None) -> str:
    first = (facts[0] if facts else "")[:200]
    base = f"{topic} :: {first}".strip(" :")
    if policy:
        from channels.instructions import build as _skill
        skill = _skill(policy)
        if skill:
            return base + "\n" + skill
    return base


def plan_generation(opportunity, items=None,
                    video_config=None,
                    policy: dict | None = None) -> GenerationPlan:
    """Decide generation requests. Ineligible/no opportunity ->
    empty plan (all None); the caller stops before G2.
    `video_config` is an optional plain dict carrying Tuzzina
    template selection ({video_type, output, video_params});
    absent/empty keeps the previous intent-only behavior.
    `policy` is the optional merged Channel Strategy profile;
    its Content Skill block is appended to image/video prompts
    (text prompts are shaped downstream in G2)."""
    if opportunity is None or not getattr(opportunity, "eligible",
                                          False):
        return GenerationPlan()
    topic = (getattr(opportunity, "topic", "") or "").strip()
    facts = [f for f in (getattr(opportunity, "facts", []) or [])
             if isinstance(f, str) and f.strip()]
    if not topic:
        titles = [(getattr(it, "title", "") or "").strip()
                  for it in (items or [])]
        titles = [t for t in titles if t]
        topic = titles[0][:120] if titles else ""
    summary = "\n".join(facts)[:1500]
    media = (getattr(opportunity, "media_intent", "none") or "none")
    if media not in ("none", "image", "video"):
        media = "none"
    image = None
    video = None
    if media == "image":
        image = ImageRequest(
            prompt=_prompt_for(topic, facts, policy))
    elif media == "video":
        cfg = video_config if isinstance(video_config, dict) else {}
        params = cfg.get("video_params")
        video = VideoRequest(
            prompt=_prompt_for(topic, facts, policy),
            video_type=str(cfg.get("video_type") or ""),
            output=str(cfg.get("output") or ""),
            params=dict(params) if isinstance(params, dict) else {})
    return GenerationPlan(
        text=TextRequest(title=topic, summary=summary),
        image=image, video=video, media_intent=media,
        meta={"fact_count": len(facts),
              "reason": "opportunity-driven"})


def shape_item_for_g2(item, plan: GenerationPlan):
    """Copy with plan-shaped title/text for the text engine.
    Identity, images, refs, hashes pass through untouched, so
    traceability and media presence survive shaping."""
    if plan is None or plan.text is None:
        raise GenerationError("no-text-request")
    return replace(item, title=plan.text.title[:200],
                   text=plan.text.summary[:4000])


def image_gen_for(plan: GenerationPlan, image_gen):
    """Select the image engine for the pipeline: pass-through iff
    the plan carries an image request, else None (which keeps the
    pipeline from inventing generator entries)."""
    if plan is not None and plan.image is not None:
        return image_gen
    return None
