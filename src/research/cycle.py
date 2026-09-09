"""End-to-end research cycle (R5): one deterministic pass over
configured sources through the already-built layers.

  collect (rss/youtube via monitor, website via extract)
  -> NEW gate (UPDATED never auto-new; website has no dedup —
     pages are always NEW, documented limitation)
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
from g1.website import WebsiteAdapter
from g1.youtube import YouTubeFeedAdapter, youtube_feed_url
from g2.generators import MockImageGenerator, MockTextGenerator
from g2.pipeline import build_package
from g3.planner import plan
from injection.intent import build_intent
from research.analysis import MockAnalysisModel
from research.generation import (image_gen_for, plan_generation,
                                 shape_item_for_g2)
from research.models import MockResearchModel
from research.monitor import (MemoryStateStore, NEW, UPDATED,
                              CollectionResult, collect)


@dataclass
class SourceReport:
    type: str
    ref: str  # url | channel_id | page url
    ok: bool = False
    error: str = ""
    collected: int = 0
    new: int = 0


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
    committed: bool = False
    error: str = ""


def _collect_rss(source: dict, store, now: str,
                 max_items: int) -> tuple[list, CollectionResult]:
    res = collect(
        {"url": source["url"],
         "source_id": source.get("source_id") or source["url"]},
        store=store, now=now,
        horizon_days=source.get("horizon_days"),
        max_items=int(source.get("max_items") or max_items))
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
        max_items=int(source.get("max_items") or max_items))
    items = [to_source_item(c.item, "youtube", "youtube", url)
             for c in res.items if c.kind == NEW]
    return items, res


def _collect_website(source: dict, n: int) -> list:
    # No stable identity exists for pages, so no dedup is possible
    # here (NOT a second dedup engine — just the absence of one).
    # Every extracted page counts as NEW, like run.py does today.
    res = WebsiteAdapter().extract(source["url"], n)
    return res.items


def run_cycle(sources: list, *, store=None, policy: dict | None = None,
              schedule: dict | None = None, now: str = "",
              text_gen=None, image_gen=None, research_model=None,
              analysis_model=None, mode: str = "draft",
              integration_id: str = "",
              video_resolve=None) -> CycleResult:
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

        for source in sources or []:
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
                items = _collect_website(source, n)
                rep = SourceReport("website", source["url"].strip(),
                                   True, "", len(items), len(items))
            else:
                raise ValueError(f"unknown-source-type:{stype or '?'}")
            out.sources.append(rep)
            out.items.extend(items)

        if not out.items:
            for res in pending:
                res.commit(store)
            out.committed = len(pending) > 0
            return out

        out.research = research_model.research(out.items)
        out.opportunity = analysis_model.analyze(
            out.research, policy, list(out.items))
        if not out.opportunity.eligible:
            for res in pending:
                res.commit(store)
            out.committed = len(pending) > 0
            return out

        # Generation orchestration (R6): opportunity facts/angle
        # shape G2 inputs; media intent gates the image engine so
        # text-only stays text-only. Video has no executor: the
        # request is recorded as deferred, explicitly, not run.
        gen_plan = plan_generation(
            out.opportunity, list(out.items),
            video_config=(policy.get("media") or {}))
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
        for item in out.items:
            shaped = shape_item_for_g2(item, gen_plan)
            out.packages.append(build_package(
                shaped, brand, language, hs_cfg, links_policy,
                text_gen, gen_image, platform=item.platform or
                "facebook", limits=None))
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
