"""End-to-end research cycle (R5): one deterministic pass over
configured sources through the already-built layers.

  collect (rss/youtube via monitor, website via shared classify)
  -> NEW gate (UPDATED never auto-new)
  -> research (ResearchModel over NEW SourceItems)
  -> analysis (AnalysisModel + Brain policy)
  -> eligible ? G2 (existing policy) -> G3 (existing planner)
     -> CanonicalIntent (media unresolved, execution later)
     : clean stop, no G2, no Tuzzina call

STOPS at CanonicalIntent: media refs are unresolved (generator
objects / source URLs, never {id,path}) and no client exists
here, so nothing can publish. Media resolution + injection stay
in the execution phase (run.py path, unchanged).

STATE (commit rule): per-source monitor results commit ONLY on
clean cycle end — full path success OR clean ineligible stop OR
no-NEW stop. ANY downstream raise (research/analysis/G2/G3/
intent) commits NOTHING, so the next cycle redelivers the same
NEW items (at-least-once). Collector errors record per-source
{ok, error}; a failed source never blocks healthy ones, and its
state stays uncommitted for retry.

No cron, no persistence backend, no network beyond collectors,
no LLM except the injected model roles (tests use Mocks).
"""
from __future__ import annotations
from dataclasses import dataclass, field

from contracts import SourceItem
from g1.rss import to_source_item
from g1.rss import canonical_url as _canonical_url
from g1.rss import content_hash as _content_hash
from g1.website import WebsiteAdapter
from g1.youtube import YouTubeFeedAdapter, youtube_feed_url
from research.monitor import (NEW, UPDATED, CollectionResult,
                              ClassifiedItem, MemoryStateStore,
                              classify_candidates, collect,
                              stable_identity)
from g2.generators import MockImageGenerator, MockTextGenerator
from g2.pipeline import build_package
from g3.planner import plan
from injection.intent import build_intent
from research.analysis import MockAnalysisModel
from research.generation import (image_gen_for, plan_generation,
                                 primary_item_for, shape_item_for_g2)
from research.models import MockResearchModel


@dataclass
class SourceReport:
    type: str
    ref: str  # url | channel_id | page url
    ok: bool = False
    error: str = ""
    collected: int = 0
    new: int = 0
    # Stage B discovery evidence (all optional, default ""):
    # capability = semantic capability that produced the
    # candidates ("", rss/website/youtube leave empty: the
    # source type IS the capability there); tool = concrete
    # tool/endpoint name when a capability layer was used;
    # discovered_at = ISO time the poll ran (mirrors now).
    capability: str = ""
    tool: str = ""
    discovered_at: str = ""


@dataclass
class CycleResult:
    sources: list = field(default_factory=list)  # [SourceReport]
    items: list = field(default_factory=list)  # NEW SourceItems
    research: object = None  # ResearchResult | None
    opportunity: object = None  # ContentOpportunity | None
    packages: list = field(default_factory=list)  # [ContentPackage]
    planned: list = field(default_factory=list)  # [PlannedPost]
    intents: list = field(default_factory=list)  # [CanonicalIntent]
    # Video prompts with no executor: recorded, never attempted,
    # never silently dropped (state still commits; the execution
    # phase resolves them when a video_resolve callable is given).
    video_deferred: list = field(default_factory=list)  # [str]
    # Resolved video references [{id, path}] from an executed
    # VideoRequest. Empty unless video_resolve succeeded.
    video_media: list = field(default_factory=list)
    # Selected executable format (research.formats vocabulary) +
    # gate reason. Empty when no eligible opportunity ran.
    format: str = ""
    format_reason: str = ""
    # Active campaign roles for this cycle (canonical stage order)
    # plus machine-readable skip reasons (e.g. "analysis:research-off",
    # "text:text-role-off"). Evidence surfaces both.
    roles: list = field(default_factory=list)  # [str]
    skipped: list = field(default_factory=list)  # [str]
    # Candidate ids this cycle holds via the atomic claim
    # mechanism (stable_identity keys). The execution phase
    # releases them on every terminal outcome; expiry covers
    # crash paths. Empty when no claimer was wired (legacy
    # callers, tests) or no items were claimed.
    claimed: list = field(default_factory=list)  # [str]
    committed: bool = False
    error: str = ""


def _collect_rss(source: dict, store, now: str,
                 max_items: int) -> tuple[list, CollectionResult]:
    res = collect(
        {"url": source["url"],
         "source_id": source.get("source_id") or source["url"]},
        store=store, now=now,
        horizon_days=source.get("horizon_days"),
        max_items=int(source.get("max_items") or source.get("n") or
                      max_items))
    items = [to_source_item(c.item, "rss", "rss", source["url"])
             for c in res.items if c.kind == NEW]
    return items, res


def _collect_youtube(source: dict, store, now: str,
                     max_items: int) -> tuple[list, CollectionResult]:
    url = youtube_feed_url(source["channel_id"])
    res = collect(
        {"url": url,
         "source_id": source.get("source_id") or
         f"youtube:{source['channel_id'].strip()}"},
        store=store, now=now,
        horizon_days=source.get("horizon_days"),
        max_items=int(source.get("max_items") or source.get("n") or
                      max_items))
    items = [to_source_item(c.item, "youtube", "youtube", url)
             for c in res.items if c.kind == NEW]
    return items, res


def _collect_website(source: dict, n: int, *, store,
                   now: str):
    """Website pages through the SHARED classify step (same
    NEW/UNCHANGED/UPDATED rules as feeds). Identity is the
    canonical page URL; the hash covers title+text. UPDATED pages
    are classified distinctly and never returned as NEW."""
    url = (source.get("url") or "").strip()
    source_id = (source.get("source_id") or "").strip() or url
    res = WebsiteAdapter().extract(url, n)
    state = store.load(source_id)
    candidates = []
    for it in res.items:
        ident = _canonical_url(it.source_id or "") or \
            it.source_id or url
        candidates.append({
            "carrier": it, "item_id": ident,
            "content_hash": _content_hash(it.title or "",
                                          it.text or ""),
            "published_at": it.published_at or ""})
    classified, pending = classify_candidates(source_id, candidates,
                                              state, now)
    items = [carrier for carrier, kind in classified if kind == NEW]
    out = [ClassifiedItem(carrier, kind)
           for carrier, kind in classified]
    return items, CollectionResult(source_id, out, now, "", pending)


def _collect_search(source: dict, n: int, *, store,
                    now: str):
    """Web/news queries through the SAME classify step as feeds
    (shared NEW/UNCHANGED/UPDATED rules). Identity is the
    cross-source stable key (canonical URL, else content
    hash), so an article seen via RSS and via search is ONE
    candidate. Discovery failure raises SearchError to the
    dispatcher, which records it per-source without blocking
    healthy sources."""
    from g1.search import SearchAdapter, SearchError
    stype = (source.get("type") or "").strip().lower()
    query = (source.get("query") or "").strip()
    if not query:
        raise ValueError(f"{stype}-source-missing-query")
    source_id = (source.get("source_id") or "").strip() or \
        f"{stype}:{query}"
    adapter = SearchAdapter(stype)
    items, meta = adapter.discover_items(
        query, n, max_age_days=source.get("max_age_days"),
        now=now)
    state = store.load(source_id)
    candidates = []
    for it in items:
        # Shared stable_identity(): platform:external_id wins
        # when the row carries a native id (social objects),
        # else canonical URL, else content hash — the same key
        # the claimer later uses, so classify, dedup, and claim
        # can never disagree on one candidate.
        ident, chash = stable_identity(
            getattr(it, "platform", ""),
            getattr(it, "external_id", ""),
            getattr(it, "source_url", "") or
            getattr(it, "source_id", ""),
            getattr(it, "title", ""), getattr(it, "text", ""))
        candidates.append({
            "carrier": it, "item_id": ident,
            "content_hash": chash or _content_hash(
                it.title or "", it.text or ""),
            "published_at": it.published_at or ""})
    classified, pending = classify_candidates(source_id, candidates,
                                              state, now)
    fresh = [carrier for carrier, kind in classified
             if kind == NEW]
    out = [ClassifiedItem(carrier, kind)
           for carrier, kind in classified]
    rep = SourceReport(stype, query, True, "", len(out),
                       len(fresh),
                       capability=meta.get("capability", ""),
                       tool=meta.get("tool", ""),
                       discovered_at=now)
    return fresh, CollectionResult(source_id, out, now, "",
                                   pending), rep


class ClaimFailed(Exception):
    """Claimer transport failure. Carries the exact legacy message
    ("<Type>: claim-failed"); callers fail the cycle closed with
    NOTHING committed (existing at-least-once rule)."""


def collect_all_sources(sources, store, now: str, out,
                        pending: list) -> None:
    """Shared collection loop: poll every configured source,
    appending reports to out.sources, NEW items to out.items, and
    staged monitor results to pending. Unknown source types raise
    ValueError with partial state already appended (fail-closed,
    same as the inlined loop this replaces). Search failures stay
    per-source evidence and never fail the collection."""
    for source in sources or []:
        if source.get("enabled") is False:
            continue
        stype = (source.get("type") or "").strip().lower()
        n = max(1, int(source.get("n") or 3))
        if stype == "rss":
            if not (source.get("url") or "").strip():
                raise ValueError("rss-source-missing-url")
            items, res = _collect_rss(source, store, now, 50)
            rep = SourceReport("rss", source["url"].strip(),
                               True, "", len(res.items),
                               len(items))
            pending.append(res)
        elif stype == "youtube":
            if not (source.get("channel_id") or "").strip():
                raise ValueError("youtube-source-missing-channel")
            items, res = _collect_youtube(source, store, now, 50)
            rep = SourceReport(
                "youtube", source["channel_id"].strip(),
                True, "", len(res.items), len(items))
            pending.append(res)
        elif stype == "website":
            if not (source.get("url") or "").strip():
                raise ValueError("website-source-missing-url")
            items, res = _collect_website(source, n, store=store,
                                          now=now)
            rep = SourceReport("website", source["url"].strip(),
                               True, "", len(res.items), len(items))
            pending.append(res)
        elif stype in ("web", "news", "social.facebook",
                         "social.instagram", "social.x"):
            try:
                items, res, rep = _collect_search(
                    source, n, store=store, now=now)
            except Exception as e:
                # Search failure is per-source evidence, never
                # a cycle failure: record the reason and
                # continue with the healthy sources (no
                # fabrication, no state staged, so a later
                # retry re-polls cleanly).
                name = type(e).__name__
                rep = SourceReport(
                    stype, (source.get("query") or "").strip(),
                    False, f"{name}: {str(e)[:200]}", 0, 0)
                items = []
                out.sources.append(rep)
                continue
            pending.append(res)
        else:
            raise ValueError(f"unknown-source-type:{stype or '?'}")
        out.sources.append(rep)
        out.items.extend(items)


def claim_collected_items(out, claimer) -> None:
    """Atomic cross-slot claim (optional, injected like the
    model roles): each NEW item is claimed by stable
    identity before any LLM spends budget. Losers drop out
    (treated like already-seen: another slot owns them).
    A claimer transport failure raises ClaimFailed with
    NOTHING committed, so the next cycle redelivers
    the same NEW items (at-least-once preserved). No
    claimer wired (legacy callers, unit tests) means no
    claiming: behavior is byte-identical to before."""
    if claimer is None or not out.items:
        return
    kept = []
    try:
        for item in out.items:
            key, _ = stable_identity(
                getattr(item, "platform", ""),
                getattr(item, "external_id", ""),
                getattr(item, "source_url", "") or
                getattr(item, "source_id", ""),
                getattr(item, "title", ""),
                getattr(item, "text", ""))
            if claimer(key):
                out.claimed.append(key)
                kept.append(item)
            else:
                out.skipped.append(f"claimed-by-other:{key}")
    except Exception as e:
        raise ClaimFailed(f"{type(e).__name__}: claim-failed")
    out.items = kept


def run_cycle(sources: list, *, store=None, policy: dict | None = None,
              schedule: dict | None = None, now: str = "",
              text_gen=None, image_gen=None, research_model=None,
              analysis_model=None, mode: str = "draft",
              integration_id: str = "",
              video_resolve=None, claimer=None) -> CycleResult:
    """Run ONE research cycle. All roles injectable; Mocks are the
    default so the cycle is deterministic and fully offline.
    `video_resolve`, when given, is a caller-supplied callable
    (VideoRequest) -> {id, path} executed for a video plan (the
    execution phase passes a TuzzinaClient-bound one); without it
    video stays deferred and the cycle commits normally."""
    out = CycleResult()
    pending: list[CollectionResult] = []
    try:
        store = store or MemoryStateStore()
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

        # Shared collection + claim (module helpers above):
        # identical behavior, reused by stage-scoped runs.
        collect_all_sources(sources, store, now, out, pending)
        try:
            claim_collected_items(out, claimer)
        except ClaimFailed as e:
            out.error = str(e)
            return out

        # Role participation (campaign-owned): resolved up front so
        # every return path below reports which stages this cycle
        # runs — including the empty-collection stop.
        from research.formats import gate_media_for_roles
        raw_roles = policy.get("roles") if isinstance(policy, dict) \
            else None
        if isinstance(raw_roles, list) and raw_roles:
            from skills.loader import SKILL_TYPES
            roles = [r for r in SKILL_TYPES if r in raw_roles]
        else:
            from skills.loader import SKILL_TYPES
            roles = list(SKILL_TYPES)
        out.roles = roles

        if not out.items:
            for res in pending:
                res.commit(store)
            out.committed = len(pending) > 0
            return out

        out.research = None
        out.opportunity = None
        # Analysis consumes research findings, so research-off
        # forces analysis-off — an architectural dependency, not
        # a choice. Text without analysis runs per-item text-only
        # (no invented opportunity: an explicit ineligible verdict
        # still stops).
        research_active = "research" in roles
        analysis_active = "analysis" in roles
        text_active = "text" in roles
        if research_active:
            from stages import run_stage
            out.research = run_stage(
                "research",
                lambda: research_model.research(out.items, policy))
        if not research_active:
            analysis_active = False
            out.skipped.append("analysis:research-off")
        if analysis_active:
            from stages import run_stage
            out.opportunity = run_stage(
                "analysis",
                lambda: analysis_model.analyze(
                    out.research, policy, list(out.items)))
            if not out.opportunity.eligible:
                for res in pending:
                    res.commit(store)
                out.committed = len(pending) > 0
                return out
        else:
            out.opportunity = None
            if research_active:
                out.skipped.append("analysis:role-off")

        # Format gate (Phase 8): the opportunity's executable
        # shape must be allowed by the integration distribution.
        # Disallowed => clean stop with reason (commits, like
        # ineligible). No silent downgrade exists in this phase.
        # Provider-level support stays Tuzzina-validated. Without
        # an opportunity (analysis skipped, never an ineligible
        # verdict) the text-only path below still passes this gate
        # with media "none", so distribution stays enforced.
        from research.formats import (allowed_from_distribution,
                                      select_format)
        allowed = allowed_from_distribution(policy.get("distribution"))
        if out.opportunity is None:
            if not text_active:
                for res in pending:
                    res.commit(store)
                out.committed = len(pending) > 0
                out.skipped.append("text:role-off")
                return out
            fmt, freason = select_format("none", links_policy, allowed)
            if fmt is None:
                for res in pending:
                    res.commit(store)
                out.committed = len(pending) > 0
                out.error = ""
                out.format_reason = freason
                return out
            out.format = fmt
            out.format_reason = freason
            from research.generation import (GenerationPlan,
                                              TextRequest)
            gen_image = None
            _one = primary_item_for(None, out.items)
            if _one is not None:
                item_plan = GenerationPlan(
                    text=TextRequest(
                        title=(_one.title or "")[:200],
                        summary=(_one.text or "")[:4000]),
                    media_intent="none",
                    meta={"reason": "text-only-no-analysis"})
                shaped = shape_item_for_g2(_one, item_plan)
                from stages import run_stage
                out.packages.append(run_stage(
                    "package",
                    lambda: build_package(
                        shaped, brand, language, hs_cfg,
                        links_policy, text_gen, gen_image,
                        platform=_one.platform or
                        "facebook", limits=None, policy=policy)))
            out.planned = plan(out.packages, schedule)
            for p in out.planned:
                out.intents.append(build_intent(
                    integration_id=integration_id, content=p.content,
                    media=[], publish_at=p.planned_at, mode=mode,
                    hashtags=list(p.tags),
                    link=p.source_ref if links_policy == "attach" else "",
                    links_policy=links_policy, post_kind="post",
                    settings={}, cta_style=cta_style))
            for res in pending:
                res.commit(store)
            out.committed = len(pending) > 0
            return out
        eff_media, media_reason = gate_media_for_roles(
            out.opportunity.media_intent, roles)
        if media_reason:
            out.skipped.append(media_reason)
        fmt, freason = select_format(
            eff_media, links_policy, allowed)
        if fmt is None:
            for res in pending:
                res.commit(store)
            out.committed = len(pending) > 0
            out.error = ""
            out.format_reason = freason
            return out
        out.format = fmt
        out.format_reason = freason

        # Text role off with a live opportunity: research and
        # analysis already ran as pure intelligence (state
        # commits), but no packages are built and no LLM text
        # budget is spent. Recorded, never silent.
        if not text_active:
            for res in pending:
                res.commit(store)
            out.committed = len(pending) > 0
            out.skipped.append("text:role-off")
            return out

        # Generation orchestration (R6): opportunity facts/angle
        # shape G2 inputs; media intent gates the image engine so
        # text-only stays text-only. Video has no executor: the
        # request is recorded as deferred, explicitly, not run.
        gen_plan = plan_generation(
            out.opportunity, list(out.items),
            video_config=(policy.get("media") or {}),
            policy=policy)
        gen_plan.format = fmt
        if gen_plan.video is not None:
            out.video_deferred.append(gen_plan.video.prompt)
            if video_resolve is not None:
                # Executed video: failure fails the cycle (no state
                # advance, no fake ref). Success records {id, path}.
                try:
                    ref = video_resolve(gen_plan.video)
                except Exception as e:
                    out.error = f"{type(e).__name__}: {e}"
                    out.committed = False
                    return out
                if not isinstance(ref, dict) or not ref.get("id") \
                        or not ref.get("path"):
                    out.error = "ValueError: video resolve " \
                        "returned no media reference"
                    out.committed = False
                    return out
                out.video_media.append(
                    {"id": str(ref["id"]), "path": str(ref["path"])})
        gen_image = image_gen_for(gen_plan, image_gen)
        _one = primary_item_for(out.opportunity, out.items)
        if _one is not None:
            shaped = shape_item_for_g2(_one, gen_plan)
            from stages import run_stage
            out.packages.append(run_stage(
                "package",
                lambda: build_package(
                    shaped, brand, language, hs_cfg, links_policy,
                    text_gen, gen_image, platform=_one.platform or
                    "facebook", limits=None, policy=policy)))
        out.planned = plan(out.packages, schedule)
        for p in out.planned:
            out.intents.append(build_intent(
                integration_id=integration_id, content=p.content,
                media=[], publish_at=p.planned_at, mode=mode,
                hashtags=list(p.tags),
                link=p.source_ref if links_policy == "attach" else "",
                links_policy=links_policy, post_kind="post",
                settings={}, cta_style=cta_style))
        for res in pending:
            res.commit(store)
        out.committed = len(pending) > 0
        return out
    except Exception as e:
        out.error = f"{type(e).__name__}: {e}"
        out.committed = False
        return out
