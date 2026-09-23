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

# Prior-stage outputs carried into a stage's context. Every key
# a downstream stage reads must appear here: analysis reads
# "research" (its verdict needs the findings), so dropping it
# silently starves analysis into a permanent no-findings no-op.
STAGE_BASE_KEYS = ("opportunity", "research", "items", "text",
                   "prompt", "claimed")

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
    run_cycle._main per-stage resolution). Records the resolved
    semantic contract and flow ids so run evidence shows exactly
    which behavior version produced each stage (never silent)."""
    from skills.loader import get_skill
    campaign_skills = cfg.get("skills") if isinstance(
        cfg, dict) else None
    skill = get_skill(skill_type,
                      (campaign_skills or {}).get(skill_type))
    flow = (cfg.get("flow") or {}) if isinstance(cfg, dict) else {}
    return {"stage": skill_type, "type": skill_type,
            "id": skill["id"], "version": skill["version"],
            "source": skill.get("source", "default"),
            "contract": skill.get("contract_id", "legacy-v1"),
            "flow": str(flow.get("version") or "default-v1")}


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
        for key in STAGE_BASE_KEYS:
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
    if not opportunity.eligible and not bool(ctx.get("dry_run")):
        # Findings that did not reach a post park in the queue
        # (deduped inside); the next starving cycle reuses them
        # first instead of stopping with nothing.
        _queue_add(client, research.findings, items,
                   str(ctx.get("now") or ""))
    # Legacy outcome map (V2 flow lookup): the verdict IS the
    # outcome. No thresholds or branches here — the flow map
    # decides what an ineligible verdict means downstream.
    return {"opportunity": _to_jsonable(opportunity),
            "claimed": list(claimed),
            "skills_used": [_trace_skill("analysis", cfg)],
            "outcome": ("eligible" if opportunity.eligible
                        else "ineligible")}


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
    # Legacy outcome map (V2 flow lookup): image intent means the
    # prompt stage runs next, anything else goes to execute. The
    # intent itself comes from the opportunity/policy data — this
    # line only reports it as the declared outcome, it never
    # decides what the intent should be. Role gating (image role
    # off) lives in the trigger-time effective flow, not here.
    intent = ""
    if opportunity is not None:
        intent = str(getattr(opportunity, "media_intent", "") or "")
    return {"text": text, "claimed": list(claimed),
            "skills_used": [_trace_skill("text", cfg)],
            "outcome": ("drafted_image" if intent == "image"
                        else "drafted")}


ICON_TAG_RE = r"\[icon:([A-Za-z0-9][A-Za-z0-9._/-]{0,120})\]\s*$"


def _assets_manifest(client):
    """Synced identity manifest ({version, tree, files, docs}),
    best-effort: missing/broken reads mean no assets (current
    behavior), never a failed run."""
    try:
        row = client.get_research_state("assets-manifest")
    except Exception:
        return {}
    if not isinstance(row, dict):
        return {}
    assets = row.get("assets")
    return assets if isinstance(assets, dict) else {}


def _parse_icon_tag(prompt: str) -> tuple[str, str | None]:
    """Split the [icon:key] tag channel (the only channel the
    immutable workflow forwards: the prompt string itself). The
    model sometimes emits its own hallucinated tag mid-prompt, so
    ALL tag occurrences are stripped from the image prompt while
    the LAST well-formed one wins as the key. Legacy prompts
    (no tags) stay untouched."""
    import re as _re
    text = str(prompt or "")
    matches = list(_re.finditer(
        r"\[icon:([A-Za-z0-9][A-Za-z0-9._/-]{0,120})\]", text))
    if not matches:
        return text, None
    stripped = _re.sub(
        r"\s*\[icon:[A-Za-z0-9][A-Za-z0-9._/-]{0,120}\]", "", text)
    return stripped.strip(), matches[-1].group(1)


def run_prompt_stage(ctx: dict) -> dict:
    """Image Agent prompt LLM (prompt only, never bytes). Pure
    read; carried claims released only on terminal failure. When
    an identity manifest is synced, the agent also picks one
    icon from it (two-step, fail-closed); the pick travels as a
    trailing [icon:key] tag inside the prompt string because the
    workflow forwards only that string downstream."""
    client, run_id = ctx["client"], ctx["run_id"]
    claimed = ctx.get("claimed") or []
    cfg = ctx["cfg"]
    try:
        brand = cfg.get("brand") or {}
        topic = str(ctx.get("topic") or "")
        gen = ctx.get("prompt_gen")
        manifest = _assets_manifest(client)
        icon_key = None
        if gen is not None and hasattr(gen, "select_icon"):
            try:
                icon_key = gen.select_icon(
                    topic, manifest.get("assets", manifest))
            except Exception:
                icon_key = None
        prompt = gen.generate_prompt(topic, brand, cfg)
        if icon_key:
            prompt = prompt.rstrip() + "\n[icon:%s]" % icon_key
    except Exception:
        _release_keys(client, run_id, claimed)
        raise
    return {"prompt": prompt, "claimed": list(claimed),
            "icon_key": icon_key, "outcome": "ready"}


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
            if not dry_run:
                # Format gate killed an eligible opportunity:
                # park its facts instead of dropping them.
                _queue_add(client,
                           _opp_pseudo_findings(opportunity, items),
                           items, str(ctx.get("now") or ""))
            # Legacy outcome map: a gated execute still ends the
            # chain (its terminal status is recorded, not routed).
            result["outcome"] = "done"
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
            if not dry_run:
                # Eligible but nothing to inject: park the facts
                # for the next cycle instead of dropping them.
                _queue_add(client,
                           _opp_pseudo_findings(opportunity, items),
                           items, str(ctx.get("now") or ""))
            # Legacy outcome map: no intents still ends the chain.
            result["outcome"] = "done"
            return result
        from run import _resolve_media
        service = InjectionService(tracer=StderrTracer())
        prompt, icon_key = _parse_icon_tag(prompt)
        # Composed ads only for icon-selected posts (legacy raw
        # path untouched); the headline is the main post's first
        # line, best-effort.
        headline = None
        if icon_key:
            main = str(text or "").split("---COMPANION---", 1)[0]
            for line in main.splitlines():
                if line.strip():
                    headline = line.strip()[:160]
                    break
        post_ids: list = []
        media_refs: list = []
        for index, (intent, pkg) in enumerate(intents):
            images = []
            for m in pkg.media:
                up = _resolve_media(
                    client, m,
                    key_hint="ai/%s/%d" % (run_id, index),
                    headline=headline, icon_key=icon_key)
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
        if post_ids:
            # Published = gone from the queue; whatever remains
            # there is still unpublished by definition.
            _queue_consume(client, opportunity.source_item_ids
                           if opportunity is not None else [])
        result["outcome"] = "done"
        return result
    finally:
        _release_keys(client, run_id, claimed)


QUEUE_SOURCE = "unpublished-queue"
QUEUE_CAP = 10  # entries kept; small enough for one stage envelope
QUEUE_MAX_ATTEMPTS = 3  # fallback reuses before a stale entry drops


def _field(o, name: str, default=""):
    if isinstance(o, dict):
        return o.get(name, default)
    return getattr(o, name, default)


def _queue_key(item_ids, statement: str) -> str:
    ids = [str(i) for i in (item_ids or []) if str(i).strip()]
    if ids:
        return ids[0]
    return "q:" + (statement or "")[:120]


def _queue_load(client):
    """Unpublished findings, oldest first. None on transport
    failure (callers must skip saves then, never clobber the
    queue with a partial view); [] when the queue is empty."""
    try:
        row = client.get_research_state(QUEUE_SOURCE)
    except Exception:
        return None
    if not isinstance(row, dict):
        return []
    cands = row.get("candidates")
    if not isinstance(cands, dict):
        return []
    out = [e for e in cands.values() if isinstance(e, dict)]
    out.sort(key=lambda e: str(e.get("queued_at") or ""))
    return out


def _queue_save(client, entries) -> None:
    """Best-effort persist. Reuses the opaque `candidates` state
    key, so no server change is needed. Never fails the run."""
    try:
        client.save_research_state(
            QUEUE_SOURCE,
            {"candidates": {
                _queue_key(e.get("item_ids"),
                           str(e.get("statement") or "")): e
                for e in (entries or [])[:QUEUE_CAP]}})
    except Exception:
        pass


def _queue_entry(finding, items_by_id: dict, now: str) -> dict:
    ids = [str(i) for i in (_field(finding, "item_ids") or [])]
    stub = {}
    for i in ids:
        if i in items_by_id:
            stub = items_by_id[i]
            break
    conf = _field(finding, "confidence", "medium")
    kind = _field(finding, "kind", "fact")
    return {
        "statement": str(_field(finding, "statement") or "")[:500],
        "kind": kind if kind in ("fact", "inference") else "fact",
        "confidence": conf if conf in ("high", "medium", "low")
        else "medium",
        "item_ids": ids,
        "excerpt": str(_field(finding, "excerpt") or "")[:300],
        "title": str(_field(stub, "title") or "")[:200],
        "text": str(_field(stub, "text") or "")[:2000],
        "source_url": str(_field(stub, "source_url") or "")[:500],
        "attempts": 0, "queued_at": now}


def _items_by_id(items) -> dict:
    out = {}
    for it in items or []:
        for k in (str(_field(it, "item_id") or ""),
                  str(_field(it, "source_id") or "")):
            if k and k not in out:
                out[k] = it
    return out


def _queue_add(client, findings, items, now: str) -> None:
    """Park findings that did not reach a post (dedupe by key,
    attempts preserved, capped). The next starving cycle reuses
    them before giving up."""
    queued = _queue_load(client)
    if queued is None:
        return
    items_by_id = _items_by_id(items)
    have = {_queue_key(e.get("item_ids"),
                       str(e.get("statement") or ""))
            for e in queued}
    for f in findings or []:
        if not str(_field(f, "statement") or "").strip():
            continue
        e = _queue_entry(f, items_by_id, now)
        key = _queue_key(e["item_ids"], e["statement"])
        if key not in have:
            have.add(key)
            queued.append(e)
    _queue_save(client, queued)


def _queue_take(client):
    """Oldest entries due for reuse (attempts bumped, stale
    dropped). Returns (findings, items, rest) with rest already
    persisted; use findings/items as this cycle's research."""
    from contracts import SourceItem
    from research.models import MAX_FINDINGS, Finding, ResearchResult
    queued = _queue_load(client)
    if not queued:
        return None
    use, rest = [], []
    for e in queued:
        attempts = int(e.get("attempts") or 0) + 1
        # ponytail: 3 reuses then drop; raise QUEUE_MAX_ATTEMPTS
        # if evergreen material should linger longer.
        if attempts > QUEUE_MAX_ATTEMPTS:
            continue
        e = dict(e, attempts=attempts)
        if len(use) < MAX_FINDINGS:
            use.append(e)
        else:
            rest.append(e)
    _queue_save(client, use + rest)
    if not use:
        return None
    findings = [Finding(
        statement=e["statement"], kind=e["kind"],
        confidence=e["confidence"], item_ids=list(e["item_ids"]),
        excerpt=e["excerpt"]) for e in use]
    items = [SourceItem(
        source_id=e["item_ids"][0] if e["item_ids"] else "queue",
        source_type="queue",
        source_url=e.get("source_url") or "",
        title=e.get("title") or e["statement"][:200],
        text=e.get("text") or e["statement"],
        item_id=e["item_ids"][0] if e["item_ids"] else "") for e in use]
    seen_topics = []
    for e in use:
        title = str(e.get("title") or "").strip()[:60]
        if title and title not in seen_topics:
            seen_topics.append(title)
    result = ResearchResult(
        summary="Reusing %d unpublished finding(s) from the queue." % len(use),
        findings=findings,
        topics=seen_topics or ["unpublished"],
        entities=[], item_ids=sorted({i for e in use for i in e["item_ids"]}),
        model="queue", meta={"from_queue": True})
    return result, items


def _queue_consume(client, source_item_ids) -> None:
    """Drop queued entries that reached a post (published =
    gone; whatever remains is still unpublished)."""
    queued = _queue_load(client)
    if not queued:
        return
    used = {str(i) for i in (source_item_ids or [])}
    rest = [e for e in queued
            if not (e.get("item_ids") and
                    {str(i) for i in e["item_ids"]} <= used)]
    if len(rest) != len(queued):
        _queue_save(client, rest)


# ponytail: queue lives in the stage path only; the legacy
# run_cycle keeps forward-only behavior until a legacy run
# proves it needs the same treatment.


def _opp_pseudo_findings(opportunity, items) -> list:
    """Finding-shaped dicts from an opportunity that produced no
    post (execute sees facts, not findings), so the material can
    park in the queue like any other unposted finding."""
    by_id = _items_by_id(items)
    conf = _field(opportunity, "confidence", "medium")
    conf = conf if conf in ("high", "medium", "low") else "medium"
    sids = [str(s) for s in
            (_field(opportunity, "source_item_ids") or [])]
    out = []
    for fact in (_field(opportunity, "facts") or []):
        if not str(fact or "").strip():
            continue
        ids = [s for s in sids if s in by_id] or list(by_id)[:1]
        out.append({"statement": str(fact)[:500], "kind": "fact",
                    "confidence": conf, "item_ids": ids,
                    "excerpt": str(fact)[:300]})
    return out


def _research_agent_skill(cfg: dict):
    """The assigned research skill doc when it declares a CUSTOM
    semantic contract. Absent contract means the frozen legacy
    path (unchanged behavior), so this returns None."""
    from skills.loader import get_skill
    campaign_skills = cfg.get("skills") if isinstance(cfg, dict) \
        else None
    doc = get_skill("research",
                    (campaign_skills or {}).get("research"))
    if doc.get("contract_id") != "custom" or not doc.get("contract"):
        return None
    return doc


def _agent_trace(loop: dict) -> list:
    """Redacted turn log for run evidence: action/tool names and
    the final outcome only — no prompts, no raw outputs, no
    source content. Proves the loop shape (invoke → tool calls →
    final) without leaking anything. A loop-level stop (budget
    exhaustion) is recorded as its own outcome here too, so the
    distinction can never collapse downstream."""
    trace = []
    for entry in (loop.get("history") or []):
        if not isinstance(entry, dict):
            continue
        result = entry.get("result")
        trace.append({
            "action": "tool_call",
            "tool": str(entry.get("tool") or ""),
            "ok": bool(isinstance(result, dict) and result.get("ok")),
            "error": str(result.get("error") or "")
            if isinstance(result, dict) else "",
        })
    trace.append({"action": "final",
                  "outcome": loop.get("outcome")
                  if loop.get("outcome") is not None
                  else (loop.get("error") or None),
                  "error": str(loop.get("error") or "")})
    return trace


def _agent_discovered_items(history) -> list:
    """SourceItem-shaped dicts for everything the agent discovered
    through capabilities (typed records already match the stage
    item shape). Used for evidence, dedupe, and downstream
    citation; never treated as instructions."""
    out = []
    for entry in history or []:
        if not isinstance(entry, dict):
            continue
        result = entry.get("result")
        if not isinstance(result, dict) or not result.get("ok"):
            continue
        for rec in result.get("items") or []:
            if isinstance(rec, dict) and rec.get("item_id"):
                out.append(rec)
    return out


def _mark_discovered_seen(store, items, now: str) -> None:
    """Dedupe bookkeeping for agent-discovered material: the same
    canonical identity a later discovery would produce, committed
    under one agent source id so a later cycle does not rediscover
    the same pages. Best-effort: bookkeeping never fails a run."""
    if not items:
        return
    try:
        from research.monitor import classify_candidates
        source_id = "discovery-agent"
        candidates = [{"carrier": it, "item_id": it.get("item_id", ""),
                       "content_hash": "",
                       "published_at": it.get("published_at", "")}
                      for it in items if it.get("item_id")]
        _classified, pending = classify_candidates(
            source_id, candidates, store.load(source_id), now)
        pending.commit(store)
    except Exception:
        pass


def _research_agent_payload(ctx, out, roles, skills, store,
                            pending, dry_run) -> dict:
    """Phase 2 research path: the agent loop owns the decision
    (capability calls, or a declared final outcome). Emptiness is
    data here — the agent is invoked with `items = []` exactly as
    with items; there is no empty-input branch on this path."""
    from research import agent_loop, tools
    from research.models import MAX_ITEMS, ResearchError
    cfg = ctx["cfg"]
    skill_doc = _research_agent_skill(cfg)
    contract = skill_doc["contract"]
    try:
        tool_limits = tools.enabled_tools(cfg.get("capabilities"))
    except tools.ToolError as e:
        raise ResearchError("capabilities-invalid:" + str(e))
    budget = agent_loop.campaign_tool_budget(cfg.get("budgets"))
    model = ctx.get("research_model")
    if not hasattr(model, "agent_turn"):
        raise ResearchError("research-model-cannot-run-agent")

    def _invoke(system: str, user: str) -> str:
        return model.agent_turn(system, user)

    loop = agent_loop.run_loop(
        invoke=_invoke, skill_doc=skill_doc, contract=contract,
        tool_names=list(tool_limits), tool_limits=tool_limits,
        tool_calls=budget, items=out.items, now=str(ctx.get("now") or ""))

    error = loop.get("error") or ""
    if error and error != "budget_exhausted":
        # Real failure (malformed turns, exhausted liveness,
        # transport): loud, and the stage retry contract applies.
        raise ResearchError("research-agent:" + error)
    if error == "budget_exhausted" and not loop.get("outcome"):
        # Policy outcome: the campaign budget is spent. A clean
        # stop, never an exception (design: stop(budget_exhausted)).
        # Recorded as its OWN outcome — never reinterpreted as
        # no_material (gate: the distinction must survive in
        # trace/history forever).
        for res in pending:
            res.commit(store)
        return {"items": [], "research": None, "claimed": [],
                "committed": True,
                "sources_checked": len(out.sources),
                "items_new": len(out.items),
                "discovery": _to_jsonable(out.sources),
                "roles": roles,
                "roles_resolved": ctx.get("roles_resolved") or [],
                "skills_used": skills, "queued": False,
                "outcome": "budget_exhausted",
                "agent_usage": loop.get("usage") or {},
                "agent_trace": _agent_trace(loop)}

    discovered = _agent_discovered_items(loop.get("history"))
    all_items = (_to_jsonable(out.items[:MAX_ITEMS]) + discovered)
    if not dry_run:
        _mark_discovered_seen(store, discovered,
                              str(ctx.get("now") or ""))
    for res in pending:
        res.commit(store)

    artifacts = loop.get("artifacts") or {}
    findings = artifacts.get("findings")
    has_findings = isinstance(findings, list) and len(findings) > 0

    if not has_findings and not dry_run:
        # Nothing usable: same unpublished-queue fallback the
        # legacy starving cycle uses, so material that failed to
        # post earlier is retried instead of dropped.
        take = _queue_take(ctx["client"])
        if take is not None:
            result, qitems = take
            return {"items": _to_jsonable(qitems),
                    "research": _to_jsonable(result),
                    "claimed": list(out.claimed),
                    "committed": True,
                    "sources_checked": len(out.sources),
                    "items_new": len(out.items),
                    "discovery": _to_jsonable(out.sources),
                    "roles": roles,
                    "roles_resolved": ctx.get("roles_resolved") or [],
                    "skills_used": skills, "queued": True,
                    "outcome": loop.get("outcome"),
                    "agent_usage": loop.get("usage") or {},
                    "agent_trace": _agent_trace(loop)}

    if not has_findings:
        # Declared no-material outcome (or dry run): clean stop.
        return {"items": [], "research": None,
                "claimed": list(out.claimed), "committed": True,
                "sources_checked": len(out.sources),
                "items_new": len(out.items),
                "discovery": _to_jsonable(out.sources),
                "roles": roles,
                "roles_resolved": ctx.get("roles_resolved") or [],
                "skills_used": skills, "queued": False,
                "outcome": loop.get("outcome"),
                "agent_usage": loop.get("usage") or {},
                "agent_trace": _agent_trace(loop)}

    research = {
        "summary": str(artifacts.get("summary") or "")[:1000],
        "findings": [f for f in findings if isinstance(f, dict)],
        "topics": [str(t)[:60] for t in (artifacts.get("topics") or [])
                   if isinstance(t, str)][:10],
        "entities": [str(e)[:60] for e in (artifacts.get("entities") or [])
                     if isinstance(e, str)][:10],
        "item_ids": sorted({str(i.get("item_id"))
                            for i in all_items
                            if isinstance(i, dict) and i.get("item_id")}),
        "model": "agent",
        "meta": {"outcome": loop.get("outcome"),
                 "usage": loop.get("usage") or {}},
    }
    return {"items": all_items[:MAX_ITEMS * 2],
            "research": research,
            "claimed": list(out.claimed), "committed": True,
            "sources_checked": len(out.sources),
            "items_new": len(out.items),
            "discovery": _to_jsonable(out.sources),
            "roles": roles,
            "roles_resolved": ctx.get("roles_resolved") or [],
            "skills_used": skills, "queued": False,
            "outcome": loop.get("outcome"),
            "agent_usage": loop.get("usage") or {},
            "agent_trace": _agent_trace(loop)}


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
    from research.models import MAX_ITEMS
    store, cfg = ctx["store"], ctx["cfg"]
    client, run_id = ctx["client"], ctx["run_id"]
    dry_run = bool(ctx.get("dry_run"))
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
    # Phase 2 split: a research skill declaring a CUSTOM semantic
    # contract runs the generic agent loop (capability calls, no
    # empty-input branch). Every other skill keeps the frozen
    # legacy path below, byte-identical to before.
    if "research" in roles and _research_agent_skill(cfg) is not None:
        return _research_agent_payload(
            ctx, out, roles, skills, store, pending, dry_run)
    if not out.items:
        for res in pending:
            res.commit(store)
        # Starving cycle: reuse unpublished findings first
        # instead of giving up (dry runs never touch the
        # shared queue).
        if not dry_run:
            take = _queue_take(client)
            if take is not None:
                result, qitems = take
                return {"items": _to_jsonable(qitems),
                        "research": _to_jsonable(result),
                        "claimed": [], "committed": True,
                        "sources_checked": len(out.sources),
                        "items_new": 0,
                        "discovery": _to_jsonable(out.sources),
                        "roles": roles,
                        "roles_resolved": ctx.get("roles_resolved") or [],
                        "skills_used": skills, "queued": True,
                        # Legacy outcome map (V2 flow lookup): the
                        # queue always carries usable findings, so a
                        # take means proceed; empty still means stop.
                        "outcome": "findings"}
        return {"items": [], "research": None, "claimed": [],
                "committed": True,
                "sources_checked": len(out.sources),
                "items_new": 0,
                "discovery": _to_jsonable(out.sources),
                "roles": roles,
                "roles_resolved": ctx.get("roles_resolved") or [],
                "skills_used": skills, "queued": False,
                "outcome": "no_material"}
    if "research" not in roles:
        # Research role off: collection still ran (items needed
        # downstream), but no LLM call — mirrors the legacy
        # research-off path exactly.
        for res in pending:
            res.commit(store)
        # Carry at most what the research LLM would read
        # (MAX_ITEMS): the full collection (up to 80 items x
        # ~4KB) bursts the 512KB stage envelope and kills the
        # run with oversize payload. Claims stay held; execute
        # releases them. items_new keeps the discovery count.
        return {"items": _to_jsonable(out.items[:MAX_ITEMS]),
                "research": None,
                "claimed": list(out.claimed),
                "committed": True,
                "sources_checked": len(out.sources),
                "items_new": len(out.items),
                "discovery": _to_jsonable(out.sources),
                "roles": roles,
                "roles_resolved": ctx.get("roles_resolved") or [],
                "skills_used": skills,
                # Legacy outcome map: research role off never runs
                # the model, so downstream treats it as "proceed
                # without research" (V1 text-only path).
                "outcome": "off"}
    try:
        result = ctx["research_model"].research(out.items, cfg)
    except Exception:
        _release_keys(client, run_id, out.claimed)
        raise
    for res in pending:
        res.commit(store)
    if not result.findings and not dry_run:
        # Model found nothing usable: same fallback as the
        # no-items branch above (queue first, clean stop last).
        take = _queue_take(client)
        if take is not None:
            result, qitems = take
            return {"items": _to_jsonable(qitems),
                    "research": _to_jsonable(result),
                    "claimed": list(out.claimed),
                    "committed": True,
                    "sources_checked": len(out.sources),
                    "items_new": len(out.items),
                    "discovery": _to_jsonable(out.sources),
                    "roles": roles,
                    "roles_resolved": ctx.get("roles_resolved") or [],
                    "skills_used": skills, "queued": True,
                    # Legacy outcome map: a queue take always
                    # carries usable findings, so proceed.
                    "outcome": "findings"}
    return {"items": _to_jsonable(out.items[:MAX_ITEMS]),
            "research": _to_jsonable(result),
            "claimed": list(out.claimed),
            "committed": True,
            "sources_checked": len(out.sources),
            "items_new": len(out.items),
            "discovery": _to_jsonable(out.sources),
            "roles": roles,
            "roles_resolved": ctx.get("roles_resolved") or [],
            "skills_used": skills, "queued": False,
            # Legacy outcome map: non-empty collection reached the
            # model, so downstream proceeds (analysis decides).
            "outcome": "findings"}
