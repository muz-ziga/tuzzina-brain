"""Stage-scoped Brain entry points for durable orchestration.

One Temporal workflow execution = one campaign loop; inside it,
each agent runs as its own stage invocation (separate process)
so a retryable stage failure waits and retries THAT stage
without restarting completed ones. This module implements the
stage side of that contract; the workflow owns ordering,
waiting, and advancement.

Stages (fixed vocabulary, strict order):
  research -> analysis -> text -> prompt -> execute

Each stage reads ONE JSON input file, runs existing domain
functions only (no duplicated agent logic), and prints exactly
one `BRAIN_STAGE {...}` envelope:
  {"stage": ..., "status": "SUCCESS" | "RETRYABLE_STAGE_FAILURE"
   | "TERMINAL_FAILURE", "payload": ..., "error": "..."}
Exit 0 on any well-formed envelope (even failures: the status
carries the verdict); exit 2 only when the harness itself is
broken (bad args, unreadable input, oversize output).

Payloads are plain JSON (dataclass asdict round-trips); policy
is NEVER transported (each stage rebuilds it from
campaign+strategy+channel inputs like the legacy path, so live
objects such as generators never cross a process boundary).
Outputs over 1MB fail TERMINAL (Temporal payload safety).

Deterministic identity for execute idempotency: value ids
`brainrun:{runId}:{i}:main|:comment` (mirrors the slot
`brainpub:` convention) and media keys `ai/{runId}/{i}` (R2
overwrite on replay instead of duplicate objects). The legacy
whole-run path is untouched and keeps its random-uuid behavior.
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import uuid

STAGE_ORDER = ("research", "analysis", "text", "prompt", "execute")

SUCCESS = "SUCCESS"
RETRYABLE = "RETRYABLE_STAGE_FAILURE"
TERMINAL = "TERMINAL_FAILURE"

MAX_STAGE_BYTES = 1024 * 1024


def stage_envelope(stage: str, status: str, payload=None,
                   error: str = "") -> dict:
    """Stage result envelope shape (mirrors the Tuzzina-side
    contract; both sides validate it)."""
    return {"stage": stage, "status": status,
            "payload": payload, "error": error or ""}


def _harness_error(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 2


def _load_stage_input(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        raise ValueError(f"stage-input-unreadable: {e}")
    if not isinstance(data, dict):
        raise ValueError("stage-input-must-be-mapping")
    return data


def _emit_stage(stage: str, status: str, payload=None,
                error: str = "") -> int:
    out = json.dumps(stage_envelope(stage, status, payload, error),
                     ensure_ascii=False)
    if len(out.encode("utf-8")) > MAX_STAGE_BYTES:
        out = json.dumps(stage_envelope(
            stage, TERMINAL, None,
            "stage-output-oversize"), ensure_ascii=False)
    print("BRAIN_STAGE " + out)
    return 0


def _get(d: dict, *keys: str):
    node = d
    for key in keys:
        if not isinstance(node, dict) or key not in node:
            raise ValueError(f"stage-input-missing:{'.'.join(keys)}")
        node = node[key]
    return node


def _as_source_item(raw: dict):
    from contracts import SourceItem
    if not isinstance(raw, dict):
        raise ValueError("stage-item-not-mapping")
    try:
        return SourceItem(**{k: raw.get(k, v) for k, v in
                             (("source_id", ""), ("source_type", ""),
                              ("source_url", ""), ("title", ""),
                              ("text", ""), ("images", []),
                              ("links", []), ("hashtags", []),
                              ("published_at", ""), ("platform", ""),
                              ("item_id", ""), ("content_hash", ""))})
    except Exception as e:
        raise ValueError(f"stage-item-invalid: {e}")


def _as_research_result(raw: dict):
    from research.models import Finding, ResearchResult
    if not isinstance(raw, dict):
        raise ValueError("stage-research-not-mapping")
    try:
        findings = [Finding(**f) for f in (raw.get("findings") or [])]
        return ResearchResult(
            summary=str(raw.get("summary") or ""),
            findings=findings,
            topics=list(raw.get("topics") or []),
            entities=list(raw.get("entities") or []),
            item_ids=list(raw.get("item_ids") or []),
            earliest_published=str(raw.get("earliest_published") or ""),
            latest_published=str(raw.get("latest_published") or ""),
            model=str(raw.get("model") or ""),
            meta=dict(raw.get("meta") or {}))
    except Exception as e:
        raise ValueError(f"stage-research-invalid: {e}")


def _as_opportunity(raw: dict):
    from research.analysis import ContentOpportunity
    if not isinstance(raw, dict):
        raise ValueError("stage-opportunity-not-mapping")
    try:
        return ContentOpportunity(
            eligible=bool(raw.get("eligible")),
            topic=str(raw.get("topic") or ""),
            angle=str(raw.get("angle") or ""),
            rationale=str(raw.get("rationale") or ""),
            facts=list(raw.get("facts") or []),
            source_item_ids=list(raw.get("source_item_ids") or []),
            content_format=str(raw.get("content_format") or "post"),
            media_intent=str(raw.get("media_intent") or "none"),
            audience=str(raw.get("audience") or ""),
            language=str(raw.get("language") or ""),
            priority=str(raw.get("priority") or "normal"),
            confidence=str(raw.get("confidence") or "low"),
            constraints=list(raw.get("constraints") or []),
            model=str(raw.get("model") or ""),
            meta=dict(raw.get("meta") or {}))
    except Exception as e:
        raise ValueError(f"stage-opportunity-invalid: {e}")


def _to_jsonable(value):
    """Dataclass/lists/dicts -> plain JSON-safe structure."""
    from dataclasses import asdict, is_dataclass
    if is_dataclass(value):
        return _to_jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(k): _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(v) for v in value]
    return value


def _stage_base(args, run_id: str) -> dict:
    """Shared per-stage setup. Mirrors run_cycle._main wiring
    (client, store, policy incl. distribution/usage, channel
    meta) WITHOUT running any stage: every stage invocation
    rebuilds identical context from the same files+env, so a
    retried stage resumes with fresh live reads. Raises
    ValueError on configuration problems (terminal)."""
    import run_cycle as rc
    campaign = rc.load_campaign(args.campaign)
    key = os.environ.get("TUZZINA_API_KEY", "")
    base = os.environ.get("TUZZINA_API_URL", "http://127.0.0.1:4107/api")
    if not key:
        raise ValueError("missing-api-key")
    client = rc._build_client(base, key)
    strategy = rc.load_strategy(args.strategy_by_integration,
                                base_dir=args.strategy_base_dir)
    integ = client.get_integration(args.strategy_by_integration)
    from channels.profile import resolve_strategy
    cfg = resolve_strategy(campaign, strategy, channel_meta={
        "integration_id": integ.get("id"),
        "name": integ.get("name"),
        "identifier": integ.get("identifier"),
        "picture": integ.get("picture"),
    })
    try:
        cfg["distribution"] = client.get_content_distribution(
            args.strategy_by_integration) or {}
    except Exception:
        cfg["distribution"] = {}
    dist = cfg.get("distribution") or {}
    targets = dist.get("formats") if isinstance(dist, dict) else None
    if isinstance(targets, dict) and targets:
        try:
            from datetime import datetime, timezone
            day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            used = client.get_content_usage(
                args.strategy_by_integration, day) or {}
            used = {f: int(used.get(f, 0)) for f in targets
                    if isinstance(targets.get(f), int)}
            remaining = {f: max(0, int(targets[f]) - used.get(f, 0))
                         for f in used}
            cfg["distribution"] = {"formats": remaining,
                                   "enabled": dist.get("enabled", True)}
        except Exception:
            pass
    from research.monitor import MemoryStateStore
    from tuzzina.state_store import TuzzinaStateStore
    if args.dry_run:
        store = MemoryStateStore()
    else:
        store = TuzzinaStateStore(client) \
            if args.state_store == "tuzzina" else MemoryStateStore()
    return {"campaign": campaign, "client": client, "store": store,
            "cfg": cfg, "run_id": run_id,
            "integration_id": args.strategy_by_integration}


def _echo_roles() -> list:
    """Observability echo (adapter+protocol+model, never
    credentials). Mirrors run_cycle._main exactly."""
    out = []
    for role in ("RESEARCH", "ANALYSIS", "TEXT"):
        prefix = f"BRAIN_{role}_"
        out.append({
            "role": role.lower(),
            "adapter": (os.environ.get(prefix + "ADAPTER") or
                        "openai").strip(),
            "protocol": (os.environ.get(prefix + "PROTOCOL") or ""
                         ).strip() or None,
            "model": os.environ.get(prefix + "MODEL") or "gpt-4.1",
        })
    return out


def _trace_skill(skill_type: str, cfg: dict):
    """Skill traceability fragment for one stage (mirrors
    run_cycle._main per-stage resolution)."""
    from skills.loader import get_skill
    campaign_skills = cfg.get("skills") if isinstance(
        cfg, dict) else None
    skill = get_skill(skill_type,
                      (campaign_skills or {}).get(skill_type))
    return {"stage": skill_type, "type": skill_type,
            "id": skill["id"], "version": skill["version"],
            "source": skill.get("source", "default")}


def _classify_terminal(err: object) -> bool:
    """True when a stage failure is terminal (never retried):
    configuration, structural, and programming errors. Everything
    else follows the shared retryable/terminal classifier."""
    from stages import classify_stage_error
    return classify_stage_error(err) == "terminal"


def stage_error_envelope(stage: str, err: object) -> tuple:
    """Map ANY stage exception to its (stage, status, payload,
    error) envelope tuple. The single place where exceptions
    become envelopes, so a NameError-class harness bug here is
    impossible to ship untested (see test below)."""
    if _classify_terminal(err):
        return (stage, TERMINAL, None, str(err))
    return (stage, RETRYABLE, None, str(err))


def _stage_ctx(args, run_id: str, base: dict | None = None) -> dict:
    """Assemble the stage context: shared base setup plus role
    models/generators and pass-through prior-stage outputs.
    Mirrors the legacy _main wiring piece by piece (same
    builders, same env); behavior for any single stage matches
    what the legacy run would do at that point. `base` carries
    ONLY prior-stage outputs (opportunity/items/text/prompt/
    claimed); everything else is rebuilt identically."""
    import run_cycle as rc
    from datetime import datetime, timezone
    ctx = _stage_base(args, run_id)
    client, cfg = ctx["client"], ctx["cfg"]
    ctx["text_gen"] = rc._build_text_gen(args.mode, session_id=run_id)
    ctx["prompt_gen"] = rc._build_image_prompt_gen(
        args.mode, session_id=run_id)
    cfg["image_prompt_gen"] = ctx["prompt_gen"]
    research_model, analysis_model = rc._build_models(
        args.mode, session_id=run_id)
    ctx["research_model"] = research_model
    ctx["analysis_model"] = analysis_model
    ctx["mode"] = getattr(args, "post_mode", "draft")
    ctx["dry_run"] = bool(args.dry_run)
    ctx["state_store"] = args.state_store
    ctx["integration_id"] = args.strategy_by_integration
    ctx["schedule"] = cfg.get("schedule") or {}
    ctx["sources"] = cfg.get("sources") or []
    ctx["now"] = datetime.now(timezone.utc).isoformat()
    ctx["roles_resolved"] = _echo_roles()

    def _claimer(key: str) -> bool:
        return bool(client.claim_candidate(key, run_id))

    ctx["claimer"] = None if args.dry_run else _claimer
    if isinstance(base, dict):
        for key in ("opportunity", "items", "text", "prompt",
                    "claimed"):
            if key in base:
                ctx[key] = base[key]
    return ctx


def _skill_fragment(cfg: dict, skill_type: str):
    from skills.loader import get_skill
    campaign_skills = cfg.get("skills") if isinstance(
        cfg, dict) else None
    skill = get_skill(skill_type,
                      (campaign_skills or {}).get(skill_type))
    return {"stage": skill_type, "type": skill_type,
            "id": skill["id"], "version": skill["version"],
            "source": skill.get("source", "default")}


def _stage_parser():
    """Stage CLI surface. Mirrors run_cycle's config contract
    (same names, same meanings) so stage invocations carry the
    identical configuration whole runs do; only the entry mode
    differs."""
    import argparse
    p = argparse.ArgumentParser(description="Brain stage runner")
    p.add_argument("--stage", choices=list(STAGE_ORDER),
                   required=True)
    p.add_argument("--stage-input", default="",
                   help="Path to stage input JSON ({} when empty).")
    p.add_argument("campaign", help="Path to campaign.yaml")
    p.add_argument("--strategy-by-integration", required=True)
    p.add_argument("--strategy-base-dir", default=None)
    p.add_argument("--mock", action="store_const", dest="mode",
                   const="--mock", default="--mock")
    p.add_argument("--openai", action="store_const", dest="mode",
                   const="--openai")
    p.add_argument("--post-mode", choices=["draft", "schedule", "now"],
                   default="draft")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--run-id", default="")
    p.add_argument("--state-store", choices=["memory", "tuzzina"],
                   default="memory")
    return p


def main_stage(argv=None) -> int:
    """Stage-scoped entry: --stage NAME --stage-input FILE plus
    the standard run config. Prints one BRAIN_STAGE envelope,
    exit 0. Exit 2 only when the harness itself is broken."""
    import sys as _sys
    try:
        args = _stage_parser().parse_args(argv)
    except SystemExit:
        return _harness_error("stage-args-invalid")
    return _run_stage_mode(args.stage, args.stage_input, args)


def _run_stage_mode(stage: str, input_path: str, base) -> int:
    import sys as _sys
    try:
        stage_input = _load_stage_input(input_path) if input_path \
            else {}
    except ValueError as e:
        print(f"error: {e}", file=_sys.stderr)
        return 2
    # Harness setup (files, env, client): FileNotFoundError /
    # ValueError here mean broken configuration -> exit 2.
    try:
        run_id = (getattr(base, "run_id", "") or "").strip() or \
            uuid.uuid4().hex
        if base.mode == "--mock" and not base.dry_run:
            # Same guard as the legacy path: mock bytes must never
            # reach real storage or posts.
            print("error: --mock is dry-run/test only; live runs "
                  "require --openai", file=_sys.stderr)
            return _emit_stage(stage, TERMINAL, None,
                               "mock-live-refused")
        ctx = _stage_ctx(base, run_id, stage_input)
    except (FileNotFoundError, ValueError) as e:
        print(f"error: {e}", file=_sys.stderr)
        return 2
    # Stage execution: EVERY domain failure (including ValueError
    # from stage input validation) maps to a structured
    # envelope, never a bare exit code — the workflow needs the
    # stage identity and retry/terminal verdict, not a code.
    try:
        if stage == "research":
            payload = run_research_stage(ctx)
        elif stage == "analysis":
            payload = run_analysis_stage(ctx)
        elif stage == "text":
            payload = run_text_stage(ctx)
        elif stage == "prompt":
            payload = run_prompt_stage(ctx)
        elif stage == "execute":
            payload = run_execute_stage(ctx)
        else:
            return _harness_error(f"unknown-stage:{stage}")
    except Exception as e:
        return _emit_stage(*stage_error_envelope(stage, e))
    return _emit_stage(stage, SUCCESS, payload)


def _release_keys(client, run_id: str, keys) -> None:
    """Best-effort claim release (mirrors run_cycle._release_all):
    never fails the run; expiry covers crash paths."""
    for key in list(keys or []):
        try:
            client.release_claim(key, run_id)
        except Exception:
            pass


def run_analysis_stage(ctx: dict) -> dict:
    """Analysis LLM over a validated research result. Pure read
    (no store writes); carried claims released only on this
    stage's own terminal failure."""
    client, run_id = ctx["client"], ctx["run_id"]
    claimed = ctx.get("claimed") or []
    cfg = ctx["cfg"]
    try:
        # Rehydrate: stage inputs cross processes as plain JSON;
        # the model needs attribute access (same objects the
        # legacy path passes in-process).
        research = _as_research_result(ctx.get("research") or {})
        items = [_as_source_item(r) for r in (ctx.get("items") or [])]
        opportunity = ctx["analysis_model"].analyze(
            research, cfg, items)
    except Exception:
        _release_keys(client, run_id, claimed)
        raise
    return {"opportunity": _to_jsonable(opportunity),
            "claimed": list(claimed),
            "skills_used": [_trace_skill("analysis", cfg)]}


def run_text_stage(ctx: dict) -> dict:
    """Text LLM via the exact shaping the pipeline uses
    (plan_generation -> shape_item_for_g2 -> generate). Pure
    read; carried claims released only on terminal failure."""
    from research.generation import (plan_generation,
                                     primary_item_for,
                                     shape_item_for_g2)
    client, run_id = ctx["client"], ctx["run_id"]
    claimed = ctx.get("claimed") or []
    cfg = ctx["cfg"]
    try:
        items = [_as_source_item(r) for r in (ctx.get("items") or [])]
        if ctx.get("opportunity") is None:
            # Text-only path (analysis skipped): plan straight from
            # the item, mirroring the legacy cycle exactly.
            from research.generation import GenerationPlan, TextRequest
            _one = primary_item_for(None, items)
            plan = GenerationPlan(
                text=TextRequest(
                    title=(_one.title or "")[:200],
                    summary=(_one.text or "")[:4000]),
                media_intent="none",
                meta={"reason": "text-only-no-analysis"})
            opportunity = None
        else:
            opportunity = _as_opportunity(ctx.get("opportunity"))
            plan = plan_generation(
                opportunity, items,
                video_config=(cfg.get("media") or {}),
                policy=cfg)
        one = primary_item_for(opportunity, items)
        shaped = shape_item_for_g2(one, plan)
        from g2.pipeline import normalize
        brand = cfg.get("brand") or {}
        text = ctx["text_gen"].generate(
            shaped.title, normalize(shaped.text), brand, cfg)
    except Exception:
        _release_keys(client, run_id, claimed)
        raise
    return {"text": text, "claimed": list(claimed),
            "skills_used": [_trace_skill("text", cfg)]}


def run_prompt_stage(ctx: dict) -> dict:
    """Image Agent prompt LLM (prompt only, never bytes). Pure
    read; carried claims released only on terminal failure."""
    client, run_id = ctx["client"], ctx["run_id"]
    claimed = ctx.get("claimed") or []
    cfg = ctx["cfg"]
    try:
        brand = cfg.get("brand") or {}
        prompt = ctx["prompt_gen"].generate_prompt(
            str(ctx.get("topic") or ""), brand, cfg)
    except Exception:
        _release_keys(client, run_id, claimed)
        raise
    return {"prompt": prompt, "claimed": list(claimed)}


def run_execute_stage(ctx: dict) -> dict:
    """Assemble (deterministic replay of validated text+prompt),
    resolve media, inject. THE side-effect stage: releases held
    claims on every terminal outcome (success included),
    mirroring the legacy run. Dry-run performs zero writes
    (packages/intents computed, nothing resolved or injected).
    Every downstream helper is the existing function — only the
    text/prompt LLM calls are replaced by validated replay, and
    row/media ids are deterministic for idempotent replays."""
    from g2.generators import (FixedPromptGenerator, FixedTextGenerator,
                               TuzzinaImageGenerator)
    from g2.pipeline import build_package
    from g3.planner import plan
    from injection.intent import build_intent, split_companion
    from injection.service import InjectionService
    from injection.trace import StderrTracer
    from research.formats import (allowed_from_distribution,
                                  gate_media_for_roles,
                                  select_format)
    from research.generation import (image_gen_for, plan_generation,
                                     primary_item_for,
                                     shape_item_for_g2)
    from run_cycle import _post_ids, _record_published_topic
    client, run_id = ctx["client"], ctx["run_id"]
    cfg = dict(ctx["cfg"])
    claimed = list(ctx.get("claimed") or [])
    dry_run = bool(ctx.get("dry_run"))
    mode = ctx.get("mode") or "draft"
    integration_id = ctx.get("integration_id") or ""
    schedule = ctx.get("schedule") or {}
    brand = cfg.get("brand") or {}
    language = cfg.get("language") or {}
    hs_cfg = cfg.get("hashtags") or {"enabled": True}
    links_policy = cfg.get("links_policy", "hide")
    result = {"post_ids": [], "media": [], "intents": 0,
              "packages": 0, "committed": True, "value_ids": [],
              "media_prompts": []}
    try:
        opportunity = _as_opportunity(ctx.get("opportunity") or {})
        items = [_as_source_item(r) for r in (ctx.get("items") or [])]
        text = str(ctx.get("text") or "")
        prompt = ctx.get("prompt")
        fixed_policy = dict(cfg)
        fixed_policy["image_prompt_gen"] = FixedPromptGenerator(
            prompt or "")
        # Media + format gates, exactly like the legacy cycle (no
        # silent downgrades, same reasons).
        eff_media, _media_reason = gate_media_for_roles(
            opportunity.media_intent, cfg.get("roles"))
        allowed = allowed_from_distribution(cfg.get("distribution"))
        fmt, freason = select_format(
            eff_media, links_policy, allowed)
        if fmt is None:
            result["format_reason"] = freason
            return result
        gen_plan = plan_generation(
            opportunity, items,
            video_config=(cfg.get("media") or {}), policy=cfg)
        gen_plan.format = fmt
        video_resolve = None
        if not dry_run:
            def video_resolve(req, _client=client):  # noqa (closure)
                return _client.generate_video(
                    req.video_type, req.output, req.prompt, req.params)
        if gen_plan.video is not None:
            result["video_deferred"] = [gen_plan.video.prompt]
            if video_resolve is not None:
                # Executed video: failure fails the stage (no state
                # advance, no fake ref). Success records {id, path}.
                # Mirrors the legacy cycle exactly.
                try:
                    ref = video_resolve(gen_plan.video)
                except Exception as e:
                    raise ValueError(f"{type(e).__name__}: {e}")
                if not isinstance(ref, dict) or not ref.get("id") \
                        or not ref.get("path"):
                    raise ValueError("video resolve returned no "
                                     "media reference")
                result["video_media"] = [ref]
        gen_image = image_gen_for(gen_plan, TuzzinaImageGenerator())
        _one = primary_item_for(opportunity, items)
        packages = []
        if _one is not None:
            shaped = shape_item_for_g2(_one, gen_plan)
            packages.append(build_package(
                shaped, brand, language, hs_cfg, links_policy,
                FixedTextGenerator(text), gen_image,
                platform=_one.platform or "facebook", limits=None,
                policy=fixed_policy))
        result["packages"] = len(packages)
        planned = plan(packages, schedule)
        intents = []
        # Packages, planned posts, and intents align 1:1 (same
        # zip as the legacy execution half).
        for index, (pkg, p) in enumerate(zip(packages, planned)):
            intent = build_intent(
                integration_id=integration_id, content=p.content,
                media=[], publish_at=p.planned_at, mode=mode,
                hashtags=list(p.tags),
                link=p.source_ref if links_policy == "attach" else "",
                links_policy=links_policy, post_kind="post",
                settings={}, cta_style=brand.get("cta_style") or
                "Learn more")
            _, companion = split_companion(text)
            base = "brainrun:%s:%d" % (run_id, index)
            intent.value_ids = [base + ":main"] + (
                [base + ":comment"] if companion.strip() else [])
            intents.append((intent, pkg))
            result["value_ids"].extend(intent.value_ids)
            for _m in pkg.media:
                if isinstance(_m, dict) and _m.get("prompt"):
                    result["media_prompts"].append(_m["prompt"])
        result["intents"] = len(intents)
        if dry_run or not intents:
            return result
        from run import _resolve_media
        service = InjectionService(tracer=StderrTracer())
        post_ids: list = []
        media_refs: list = []
        for index, (intent, pkg) in enumerate(intents):
            images = []
            for m in pkg.media:
                up = _resolve_media(
                    client, m,
                    key_hint="ai/%s/%d" % (run_id, index))
                images.append({"id": up["id"], "path": up["path"]})
                media_refs.append({"id": up["id"], "path": up["path"]})
            intent.media = images
            injected = service.inject(intent, client)
            post_ids.extend(_post_ids(injected.get("response")))
        result["post_ids"] = post_ids
        result["media"] = media_refs
        if not dry_run and ctx.get("state_store") == "tuzzina" and \
                opportunity is not None:
            _record_published_topic(
                client, opportunity.topic, opportunity.angle)
        return result
    finally:
        _release_keys(client, run_id, claimed)


def run_research_stage(ctx: dict) -> dict:
    """Collect + claim + research LLM. Commits collection state
    on success (mirrors the legacy cycle); on failure nothing is
    committed so a later retry redelivers the same NEW items.
    Claims are HELD on success (execute releases at the end, like
    the legacy run); released here only on this stage's own
    terminal failure."""
    from research.cycle import (ClaimFailed, CycleResult,
                                claim_collected_items,
                                collect_all_sources)
    store, cfg = ctx["store"], ctx["cfg"]
    client, run_id = ctx["client"], ctx["run_id"]
    out = CycleResult()
    pending: list = []
    collect_all_sources(ctx["sources"], store, ctx["now"], out,
                        pending)
    try:
        claim_collected_items(out, ctx.get("claimer"))
    except ClaimFailed:
        _release_keys(client, run_id, out.claimed)
        raise
    from skills.loader import SKILL_TYPES
    raw_roles = cfg.get("roles") if isinstance(cfg, dict) else None
    if isinstance(raw_roles, list) and raw_roles:
        roles = [r for r in SKILL_TYPES if r in raw_roles]
    else:
        roles = list(SKILL_TYPES)
    skills = [_trace_skill("research", cfg)] \
        if "research" in roles else []
    if not out.items:
        for res in pending:
            res.commit(store)
        return {"items": [], "research": None, "claimed": [],
                "committed": True,
                "sources_checked": len(out.sources),
                "items_new": 0,
                "discovery": _to_jsonable(out.sources),
                "roles": roles,
                "roles_resolved": ctx.get("roles_resolved") or [],
                "skills_used": skills}
    if "research" not in roles:
        # Research role off: collection still ran (items needed
        # downstream), but no LLM call — mirrors the legacy
        # research-off path exactly.
        for res in pending:
            res.commit(store)
        return {"items": _to_jsonable(out.items),
                "research": None,
                "claimed": list(out.claimed),
                "committed": True,
                "sources_checked": len(out.sources),
                "items_new": len(out.items),
                "discovery": _to_jsonable(out.sources),
                "roles": roles,
                "roles_resolved": ctx.get("roles_resolved") or [],
                "skills_used": skills}
    try:
        result = ctx["research_model"].research(out.items, cfg)
    except Exception:
        _release_keys(client, run_id, out.claimed)
        raise
    for res in pending:
        res.commit(store)
    return {"items": _to_jsonable(out.items),
            "research": _to_jsonable(result),
            "claimed": list(out.claimed),
            "committed": True,
            "sources_checked": len(out.sources),
            "items_new": len(out.items),
            "discovery": _to_jsonable(out.sources),
            "roles": roles,
            "roles_resolved": ctx.get("roles_resolved") or [],
            "skills_used": skills}
