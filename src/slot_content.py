"""Per-slot content pipeline (Stage I). SlotExecution in,
generation handoff out — never a campaign batch.

This mirrors research/cycle.py stage-for-stage for ONE
already-claimed candidate: roles gating, research,
analysis (+ineligible stop), format gate, text gating,
plan_generation, video deferral, G2 packaging, G3
planning, intent building. Every helper is the SAME
function the batch path calls with the SAME semantics;
only the collection half is absent (the slot already
owns its candidate from Stage H).

What this module NEVER does (by construction, asserted
in tests): collect, claim, release-others'-claims,
publish, schedule, upload, run schedulers, touch
credentials, or call Text/Image/Video outside the
injected generators. Release of the slot's OWN claim
happens best-effort on terminal outcomes, mirroring
run_cycle.py; expiry covers the rest.
"""
from __future__ import annotations
from dataclasses import dataclass, field

STATUS_READY = "ready"
STATUS_NO_OP = "no-op"
STATUS_RESEARCH_FAILED = "research-failed"
STATUS_ANALYSIS_FAILED = "analysis-failed"
STATUS_GENERATION_FAILED = "generation-failed"
STATUS_UNSUPPORTED = "unsupported-capability"


@dataclass
class SlotContent:
    """Terminal handoff for one slot's content. Objects (not
    summaries) mirror CycleResult: research, opportunity,
    packages, planned, intents travel intact for the later
    publish stage. Untrusted candidate text is carried, never
    interpreted."""
    status: str = ""
    slot_id: str = ""
    campaign_id: str = ""
    run_id: str = ""
    candidate_id: str = ""
    research: object = None
    opportunity: object = None
    packages: list = field(default_factory=list)
    planned: list = field(default_factory=list)
    intents: list = field(default_factory=list)
    video_deferred: list = field(default_factory=list)
    video_media: list = field(default_factory=list)
    format: str = ""
    format_reason: str = ""
    roles: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    released: bool = False
    error: str = ""


def _release_owned(release, candidate_id: str,
                   run_id: str) -> bool:
    """Best-effort release of OUR OWN claim only: needs both a
    candidate and an owner, otherwise there is nothing owned
    to release. Never raises; expiry covers whatever this
    misses."""
    if not candidate_id or not run_id or release is None:
        return False
    try:
        release(candidate_id, run_id)
        return True
    except Exception:
        return False


def _terminal(out: SlotContent, status: str, error: str,
              release, candidate_id: str,
              run_id: str) -> SlotContent:
    out.status = status
    out.error = error
    out.released = _release_owned(release, candidate_id,
                                  run_id)
    return out


def run_slot_content(exec_ctx, *, research_model=None,
                     analysis_model=None, text_gen=None,
                     image_gen=None, policy=None, schedule=None,
                     mode: str = "draft", integration_id: str = "",
                     video_resolve=None,
                     release=None) -> SlotContent:
    """Run content generation for ONE claimed slot. All
    generators/models are injected (Mocks default: fully
    offline, deterministic). ``policy`` carries brand,
    language, hashtags, links_policy, roles, distribution
    exactly as the batch path receives it."""
    from contracts import SourceItem
    from g2.generators import MockImageGenerator, MockTextGenerator
    from g2.pipeline import build_package
    from g3.planner import plan
    from injection.intent import build_intent
    from research.analysis import MockAnalysisModel
    from research.formats import (allowed_from_distribution,
                                  gate_media_for_roles,
                                  select_format)
    from research.generation import (image_gen_for,
                                     plan_generation,
                                     shape_item_for_g2)
    from research.models import MockResearchModel

    out = SlotContent()
    try:
        campaign_id = str(getattr(exec_ctx, "campaign_id", "")
                          or "")
        run_id = str(getattr(exec_ctx, "run_id", "") or "")
        candidate_id = str(getattr(exec_ctx, "candidate_id", "")
                           or "")
        out.slot_id = str(getattr(exec_ctx, "slot_id", "") or "")
        out.campaign_id = campaign_id
        out.run_id = run_id
        out.candidate_id = candidate_id
        cand = getattr(exec_ctx, "candidate", None)
        if not isinstance(cand, dict) or not candidate_id:
            return _terminal(out, STATUS_NO_OP,
                             "no-candidate-in-context", release,
                             candidate_id, run_id)
        policy = dict(policy or {})
        schedule = dict(schedule or {})
        text_gen = text_gen or MockTextGenerator()
        image_gen = image_gen or MockImageGenerator()
        research_model = research_model or MockResearchModel()
        analysis_model = analysis_model or MockAnalysisModel()

        brand = policy.get("brand") or {}
        language = policy.get("language") or {}
        hs_cfg = policy.get("hashtags") or {"enabled": True}
        links_policy = policy.get("links_policy", "hide")
        cta_style = str(brand.get("cta_style") or "Learn more")

        # Role participation: identical rule to the batch
        # path (canonical SKILL_TYPES order, all-on when the
        # campaign is silent).
        raw_roles = policy.get("roles") \
            if isinstance(policy, dict) else None
        if isinstance(raw_roles, list) and raw_roles:
            from skills.loader import SKILL_TYPES
            roles = [r for r in SKILL_TYPES if r in raw_roles]
        else:
            from skills.loader import SKILL_TYPES
            roles = list(SKILL_TYPES)
        out.roles = roles

        item = SourceItem(
            source_id=str(cand.get("source_url", "") or
                          cand.get("source_id", "") or ""),
            source_type=str(cand.get("source_type", "") or ""),
            source_url=str(cand.get("source_url", "") or ""),
            title=str(cand.get("title", "") or ""),
            text=str(cand.get("snippet", "") or
                     cand.get("text", "") or ""),
            images=list(cand.get("images", []) or []),
            links=[],
            hashtags=[],
            published_at=str(cand.get("published_at", "") or ""),
            platform=str(cand.get("platform", "") or ""),
            item_id=candidate_id,
            content_hash="",
            external_id=str(cand.get("external_id", "") or ""),
            discovered_at=str(cand.get("discovered_at", "") or ""),
            capability=str(cand.get("capability", "") or ""),
            author=str(cand.get("author", "") or ""))

        research_active = "research" in roles
        analysis_active = "analysis" in roles
        text_active = "text" in roles
        if not research_active:
            # Mirrors the batch path: analysis consumes
            # research findings, so research-off forces
            # analysis-off (architectural dependency).
            analysis_active = False
            out.skipped.append("analysis:research-off")
        if research_active:
            try:
                from stages import run_stage
                out.research = run_stage(
                    "research",
                    lambda: research_model.research([item], policy))
            except Exception as e:
                return _terminal(out, STATUS_RESEARCH_FAILED,
                                 f"{type(e).__name__}: {e}",
                                 release, candidate_id, run_id)
        if not research_active:
            analysis_active = False
            out.skipped.append("analysis:research-off")
        if analysis_active:
            try:
                from stages import run_stage
                out.opportunity = run_stage(
                    "analysis",
                    lambda: analysis_model.analyze(
                        out.research, policy, [item]))
            except Exception as e:
                return _terminal(out, STATUS_ANALYSIS_FAILED,
                                 f"{type(e).__name__}: {e}",
                                 release, candidate_id, run_id)
            if not out.opportunity.eligible:
                return _terminal(out, STATUS_NO_OP,
                                 "ineligible-opportunity",
                                 release, candidate_id, run_id)
        else:
            out.opportunity = None
            if research_active:
                out.skipped.append("analysis:role-off")

        allowed = allowed_from_distribution(
            policy.get("distribution"))
        if out.opportunity is None:
            if not text_active:
                return _terminal(out, STATUS_NO_OP,
                                 "text:role-off", release,
                                 candidate_id, run_id)
            fmt, freason = select_format(
                "none", links_policy, allowed)
            if fmt is None:
                out.format_reason = freason
                return _terminal(out, STATUS_NO_OP,
                                 freason, release,
                                 candidate_id, run_id)
            out.format = fmt
            out.format_reason = freason
            from research.generation import (GenerationPlan,
                                              TextRequest)
            gen_image = None
            item_plan = GenerationPlan(
                text=TextRequest(
                    title=(item.title or "")[:200],
                    summary=(item.text or "")[:4000]),
                media_intent="none",
                meta={"reason": "text-only-no-analysis"})
            shaped = shape_item_for_g2(item, item_plan)
            try:
                from stages import run_stage
                out.packages.append(run_stage(
                    "package",
                    lambda: build_package(
                        shaped, brand, language, hs_cfg,
                        links_policy, text_gen, gen_image,
                        platform=item.platform or "facebook",
                        limits=None, policy=policy)))
            except Exception as e:
                return _terminal(out, STATUS_GENERATION_FAILED,
                                 f"{type(e).__name__}: {e}",
                                 release, candidate_id, run_id)
            out.planned = plan(out.packages, schedule)
            for p in out.planned:
                out.intents.append(build_intent(
                    integration_id=integration_id,
                    content=p.content, media=[],
                    publish_at=p.planned_at, mode=mode,
                    hashtags=list(p.tags),
                    link=p.source_ref
                    if links_policy == "attach" else "",
                    links_policy=links_policy, post_kind="post",
                    settings={}, cta_style=cta_style))
            out.released = _release_owned(
                release, candidate_id, run_id)
            out.status = STATUS_READY
            out.error = ""
            return out

        eff_media, media_reason = gate_media_for_roles(
            out.opportunity.media_intent, roles)
        if media_reason:
            out.skipped.append(media_reason)
        fmt, freason = select_format(
            eff_media, links_policy, allowed)
        if fmt is None:
            out.format_reason = freason
            return _terminal(out, STATUS_NO_OP, freason,
                             release, candidate_id, run_id)
        out.format = fmt
        out.format_reason = freason

        if not text_active:
            return _terminal(out, STATUS_NO_OP,
                             "text:role-off", release,
                             candidate_id, run_id)

        gen_plan = plan_generation(
            out.opportunity, [item],
            video_config=(policy.get("media") or {}),
            policy=policy)
        gen_plan.format = fmt
        if gen_plan.video is not None:
            out.video_deferred.append(gen_plan.video.prompt)
            if video_resolve is not None:
                try:
                    ref = video_resolve(gen_plan.video)
                except Exception as e:
                    return _terminal(
                        out, STATUS_GENERATION_FAILED,
                        f"{type(e).__name__}: {e}", release,
                        candidate_id, run_id)
                if not isinstance(ref, dict) or not ref.get("id") \
                        or not ref.get("path"):
                    return _terminal(
                        out, STATUS_GENERATION_FAILED,
                        "ValueError: video resolve returned no "
                        "media reference", release, candidate_id,
                        run_id)
                out.video_media.append(
                    {"id": str(ref["id"]),
                     "path": str(ref["path"])})
        gen_image = image_gen_for(gen_plan, image_gen)
        shaped = shape_item_for_g2(item, gen_plan)
        try:
            from stages import run_stage
            out.packages.append(run_stage(
                "package",
                lambda: build_package(
                    shaped, brand, language, hs_cfg, links_policy,
                    text_gen, gen_image,
                    platform=item.platform or "facebook",
                    limits=None, policy=policy)))
        except Exception as e:
            return _terminal(out, STATUS_GENERATION_FAILED,
                             f"{type(e).__name__}: {e}", release,
                             candidate_id, run_id)
        out.planned = plan(out.packages, schedule)
        for p in out.planned:
            out.intents.append(build_intent(
                integration_id=integration_id,
                content=p.content, media=[],
                publish_at=p.planned_at, mode=mode,
                hashtags=list(p.tags),
                link=p.source_ref
                if links_policy == "attach" else "",
                links_policy=links_policy, post_kind="post",
                settings={}, cta_style=cta_style))
        out.released = _release_owned(release, candidate_id,
                                      run_id)
        out.status = STATUS_READY
        out.error = ""
        return out
    except Exception as e:
        return _terminal(out, STATUS_GENERATION_FAILED,
                         f"{type(e).__name__}: {e}", release,
                         getattr(out, "candidate_id", ""),
                         getattr(out, "run_id", ""))
