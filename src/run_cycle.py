"""run_cycle.py: ONE unattended Brain research cycle -> Tuzzina.

Usage: TUZZINA_API_KEY=... python3 src/run_cycle.py <campaign.yaml> --strategy-by-integration <integration_id> [--mock|--openai] [--post-mode draft|schedule|now] [--dry-run] [--run-id ID] [--state-store memory|tuzzina]

Unlike run.py (legacy direct G2 path), this entry drives the full
research pipeline: collect -> NEW gate -> research -> analysis ->
G2 -> G3 -> CanonicalIntent, then (unless --dry-run) resolves
media and injects via Tuzzina. Monitoring state commits only on
the cycle's clean completion (see research/monitor.py).

Machine contract: the LAST stdout line is always
`BRAIN_RESULT {...}` JSON ({run_id, status, sources_checked,
items_new, eligible, intents, post_ids, committed, error}).
Human progress lines go to stdout above it; diagnostics and the
causal trace go to stderr. Exit codes mirror run.py: 0 ok
(including clean no-op), 1 nothing-to-do/cycle error, 2 config,
3 Tuzzina, 4 unsupported platform.

Safety: live (non-dry) runs require --openai. --mock is dry-run
and test only: Mock bytes must never reach real storage.
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from campaign import load_campaign
from channels.profile import resolve_strategy
from channels.strategy_store import load as load_strategy
from tuzzina.client import TuzzinaError
from tuzzina.state_store import TuzzinaStateStore
from research.cycle import run_cycle
from research.monitor import MemoryStateStore
from adapters.base import UnsupportedPlatform
from run import _build_client, _resolve_media


def _post_ids(response) -> list:
    """Extract post ids from a create_post response. Never raises;
    mirrors the trace helper without importing privates."""
    try:
        items = response if isinstance(response, list) else [response]
        return [str(x.get("postId")) for x in items
                if isinstance(x, dict) and x.get("postId")]
    except Exception:
        return []


def _build_models(mode: str, session_id: str = ""):
    """Role models for the cycle.

    --mock wires Mocks (deterministic, offline; role env is
    ignored by design — mock runs never touch vendors).
    --openai wires real roles, each resolved independently:
    BRAIN_<ROLE>_ADAPTER (default "openai"), BRAIN_<ROLE>_PROTOCOL
    (generic, explicit), BRAIN_<ROLE>_KEY (default: shared
    OPENAI_API_KEY), BRAIN_<ROLE>_MODEL (default "gpt-4.1") for
    ROLE in RESEARCH/ANALYSIS/TEXT. Missing key or unknown
    adapter/protocol raises ValueError (exit 2): no silent fallback.
    Per-role switching needs nothing more: adapters are
    constructed from the resolved quad and injected. Model stays
    opaque. session_id is the automation run identity shared by
    all three roles; providers that require a session header
    (OpenCode Zen) emit it, all others ignore it."""
    from research.models import MockResearchModel, OpenAIResearchModel
    from research.analysis import MockAnalysisModel, OpenAIAnalysisModel
    from llm.adapters import resolve_adapter
    if mode != "--openai":
        return MockResearchModel(), MockAnalysisModel()
    shared_key = os.environ.get("OPENAI_API_KEY", "")
    built = []
    for role, cls in (("RESEARCH", OpenAIResearchModel),
                      ("ANALYSIS", OpenAIAnalysisModel)):
        prefix = f"BRAIN_{role}_"
        adapter_key = (os.environ.get(prefix + "ADAPTER") or
                       "openai").strip()
        protocol = (os.environ.get(prefix + "PROTOCOL") or "").strip() or None
        key = os.environ.get(prefix + "KEY") or shared_key
        if not key:
            raise ValueError(f"{prefix}KEY is not set (and no "
                             f"shared OPENAI_API_KEY)")
        model = os.environ.get(prefix + "MODEL") or "gpt-4.1"
        # Account-level endpoint for generic OpenAI-compatible
        # connections (BRAIN_<ROLE>_BASE). Empty means the adapter
        # default; only generic accounts resolve one server-side.
        base_url = (os.environ.get(prefix + "BASE") or "").strip()
        # Provider-declared session header name (BRAIN_<ROLE>_
        # SESSION_HEADER). Empty means none; the value is always
        # the existing session_id, never a new identity.
        session_header = (os.environ.get(prefix + "SESSION_HEADER") or "").strip()
        adapter_cls = resolve_adapter(adapter_key, protocol)
        built.append(cls(adapter=adapter_cls(
            key, model, session_id=session_id, base_url=base_url,
            session_header=session_header)))
    return built[0], built[1]


def _build_text_gen(mode: str, session_id: str = ""):
    """Text generator honoring the same per-role env contract
    (BRAIN_TEXT_ADAPTER/PROTOCOL/KEY/MODEL). Separated from
    run._build_ generators so the cycle path never inherits legacy
    defaults silently; behavior for unset env is identical. Protocol
    is generic and explicit; model stays opaque. session_id is the
    same automation run identity shared with research/analysis."""
    from g2.generators import MockTextGenerator, OpenAITextGenerator
    from llm.adapters import resolve_adapter
    if mode != "--openai":
        return MockTextGenerator()
    shared_key = os.environ.get("OPENAI_API_KEY", "")
    adapter_key = (os.environ.get("BRAIN_TEXT_ADAPTER") or
                   "openai").strip()
    protocol = (os.environ.get("BRAIN_TEXT_PROTOCOL") or "").strip() or None
    key = os.environ.get("BRAIN_TEXT_KEY") or shared_key
    if not key:
        raise ValueError("BRAIN_TEXT_KEY is not set (and no shared "
                         "OPENAI_API_KEY)")
    model = os.environ.get("BRAIN_TEXT_MODEL") or "gpt-4.1"
    base_url = (os.environ.get("BRAIN_TEXT_BASE") or "").strip()
    session_header = (os.environ.get("BRAIN_TEXT_SESSION_HEADER") or "").strip()
    return OpenAITextGenerator(
        adapter=resolve_adapter(adapter_key, protocol)(
            key, model, session_id=session_id, base_url=base_url,
            session_header=session_header))


def _emit_result(result: dict) -> None:
    print("BRAIN_RESULT " + json.dumps(result, ensure_ascii=False))


def _main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Brain one-shot research cycle -> Tuzzina")
    p.add_argument("campaign", help="Path to campaign.yaml")
    p.add_argument("--strategy-by-integration", required=True,
                   help="Tuzzina integration id whose Brain strategy "
                        "(brain-strategies/<id>.yaml) should be used.")
    p.add_argument("--strategy-base-dir", default=None,
                   help="Override the brain-strategies/ directory.")
    p.add_argument("--mock", action="store_const", dest="mode",
                   const="--mock", default="--mock")
    p.add_argument("--openai", action="store_const", dest="mode",
                   const="--openai")
    p.add_argument("--post-mode", choices=["draft", "schedule", "now"],
                   default="draft")
    p.add_argument("--dry-run", action="store_true",
                   help="Full cycle with ZERO writes (no upload, no "
                        "posts). State commits normally: a clean "
                        "no-op still advances the cursor.")
    p.add_argument("--run-id", default="",
                   help="Correlation id (default: generated). Echoed "
                        "in BRAIN_RESULT for trigger-side tracing.")
    p.add_argument("--state-store", choices=["memory", "tuzzina"],
                   default="memory",
                   help="Monitoring-state backend (default: memory). "
                        "'tuzzina' persists via the Public API.")
    args = p.parse_args(argv)

    run_id = args.run_id.strip() or uuid.uuid4().hex
    result = {"run_id": run_id, "status": "error", "sources_checked": 0,
              "items_new": 0, "eligible": False, "intents": 0,
              "post_ids": [], "committed": False, "error": "",
              "roles_resolved": [], "skills_used": [],
              "usage": {"used": {}, "remaining": {}}}

    if args.mode == "--mock" and not args.dry_run:
        # Mock image bytes must never reach real storage or posts.
        print("error: --mock is dry-run/test only; live runs "
              "require --openai", file=sys.stderr)
        result["error"] = "mock-live-refused"
        _emit_result(result)
        return 2

    campaign = load_campaign(args.campaign)

    key = os.environ.get("TUZZINA_API_KEY", "")
    base = os.environ.get("TUZZINA_API_URL", "http://127.0.0.1:4107/api")
    if not key:
        print("error: TUZZINA_API_KEY is not set", file=sys.stderr)
        result["error"] = "missing-api-key"
        _emit_result(result)
        return 2
    client = _build_client(base, key)

    strategy = load_strategy(args.strategy_by_integration,
                             base_dir=args.strategy_base_dir)
    integ = client.get_integration(args.strategy_by_integration)
    channel_meta = {
        "integration_id": integ.get("id"),
        "name": integ.get("name"),
        "identifier": integ.get("identifier"),
        "picture": integ.get("picture"),
    }
    print(f"run_id={run_id} channel: name={integ.get('name')!r} "
          f"id={integ.get('id')} platform={integ.get('identifier')!r}")
    cfg = resolve_strategy(campaign, strategy, channel_meta=channel_meta)
    # Per-integration distribution (allowed formats + desired mix)
    # is Tuzzina-owned config, not campaign/strategy policy: fetch
    # live and attach. Absent/invalid -> unconstrained (existing
    # behavior); the cycle gates on it when present.
    try:
        cfg["distribution"] = client.get_content_distribution(
            args.strategy_by_integration) or {}
    except Exception as e:
        print(f"run_id={run_id} warning: distribution unavailable "
              f"({type(e).__name__}); continuing unconstrained",
              file=sys.stderr)
        cfg["distribution"] = {}

    # Capacity enforcement: remaining = target - used(today, UTC).
    # Computed here so the cycle gate stays pure policy. A usage
    # failure preserves Phase 8 behavior (targets as allowlist);
    # only a clean read narrows the gate.
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
            result["usage"] = {"used": used, "remaining": remaining}
        except Exception as e:
            print(f"run_id={run_id} warning: usage unavailable "
                  f"({type(e).__name__}); enforcing targets only",
                  file=sys.stderr)

    # Image generation always delegates through Tuzzina (org
    # API key); it needs no provider credential of its own.
    from g2.generators import MockImageGenerator, TuzzinaImageGenerator
    text_gen = _build_text_gen(args.mode, session_id=run_id)
    image_gen_obj = TuzzinaImageGenerator() \
        if args.mode == "--openai" else MockImageGenerator()
    research_model, analysis_model = _build_models(
        args.mode, session_id=run_id)
    roles_resolved = []
    skills_used = []
    if args.mode == "--openai":
        # Echoed for observability (adapter+protocol+model — never
        # credentials). Mirrors the resolution order above.
        for role in ("RESEARCH", "ANALYSIS", "TEXT"):
            prefix = f"BRAIN_{role}_"
            roles_resolved.append({
                "role": role.lower(),
                "adapter": (os.environ.get(prefix + "ADAPTER") or
                            "openai").strip(),
                "protocol": (os.environ.get(prefix + "PROTOCOL") or "").strip() or None,
                "model": os.environ.get(prefix + "MODEL") or "gpt-4.1",
            })
        # Skill traceability: which expertise produced this run.
        # Each ACTIVE stage resolves its own type against the
        # campaign overrides (global file when the campaign is
        # silent). Inactive stages resolve nothing: their skills
        # are never needed, so a broken file there cannot fail
        # a run that does not use it.
        from skills.loader import get_skill
        campaign_skills = cfg.get("skills") if isinstance(
            cfg, dict) else None
        active = cfg.get("roles") if isinstance(cfg, dict) else None
        if not isinstance(active, list) or not active:
            from skills.loader import SKILL_TYPES
            active = list(SKILL_TYPES[:3])
        for skill_type, stage in (("research", "research"),
                                  ("analysis", "analysis"),
                                  ("text", "text")):
            if skill_type not in active:
                continue
            try:
                skill = get_skill(
                    skill_type, (campaign_skills or {}).get(skill_type))
                skills_used.append({
                    "stage": stage, "type": skill_type,
                    "id": skill["id"], "version": skill["version"],
                    "source": skill.get("source", "default")})
            except Exception as e:
                print(f"run_id={run_id} error: skill {skill_type} "
                      f"unavailable ({e})", file=sys.stderr)
                result["error"] = f"skill-unavailable:{skill_type}"
                result["status"] = "error"
                _emit_result(result)
                return 2
    if args.dry_run:
        # A cursor advance is a write: dry-run performs ZERO writes,
        # so it always runs on a memory store even when tuzzina was
        # requested (noted on stderr, not an error).
        if args.state_store == "tuzzina":
            print("run_id=%s note: dry-run forces memory state store "
                  "(no state writes)" % run_id, file=sys.stderr)
        store = MemoryStateStore()
    else:
        store = TuzzinaStateStore(client) \
            if args.state_store == "tuzzina" else MemoryStateStore()

    def _video_resolve(req):
        return client.generate_video(
            req.video_type, req.output, req.prompt, req.params)

    # Cross-slot claim wiring (Stage B): the Tuzzina claim
    # endpoint is the atomic lock; this run's id is the owner.
    # Slot label is the publish slot when Stage 9 supplies one;
    # until then the run id scopes the claim for evidence.
    # Release is best-effort on every terminal outcome below
    # (never fails the run); expiry covers crash paths.
    def _claimer(key: str) -> bool:
        return bool(client.claim_candidate(key, run_id))

    def _release_all() -> None:
        for key in list(getattr(out, "claimed", []) or []):
            try:
                client.release_claim(key, run_id)
            except Exception as e:
                print(f"run_id={run_id} warning: release failed "
                      f"({type(e).__name__}); expiry covers",
                      file=sys.stderr)

    out = run_cycle(
        cfg.get("sources") or [], store=store, policy=cfg,
        schedule=cfg.get("schedule") or {}, text_gen=text_gen,
        image_gen=image_gen_obj, research_model=research_model,
        analysis_model=analysis_model, mode=args.post_mode,
        integration_id=args.strategy_by_integration,
        video_resolve=None if args.dry_run else _video_resolve,
        claimer=None if args.dry_run else _claimer)

    result["sources_checked"] = len(out.sources)
    result["items_new"] = len(out.items)
    # Stage B discovery evidence: one entry per polled source
    # (type/ref/ok/collected/new + capability/tool when a
    # capability layer produced the candidates). Read-only
    # projection of CycleResult.sources; Tuzzina surfaces the
    # keys it allowlists and ignores the rest.
    result["discovery"] = [{
        "type": getattr(rep, "type", ""),
        "ref": getattr(rep, "ref", ""),
        "ok": bool(getattr(rep, "ok", False)),
        "collected": int(getattr(rep, "collected", 0) or 0),
        "new": int(getattr(rep, "new", 0) or 0),
        "capability": getattr(rep, "capability", "") or "",
        "tool": getattr(rep, "tool", "") or "",
        "discovered_at": getattr(rep, "discovered_at", "") or "",
        "error": getattr(rep, "error", "") or "",
    } for rep in (out.sources or [])]
    result["eligible"] = bool(
        out.opportunity is not None and out.opportunity.eligible)
    result["intents"] = len(out.intents)
    result["committed"] = bool(out.committed)
    result["roles_resolved"] = roles_resolved
    result["skills_used"] = skills_used
    result["roles"] = list(out.roles)
    result["skipped"] = list(out.skipped)
    result["claimed"] = list(getattr(out, "claimed", []) or [])
    if out.error:
        print(f"run_id={run_id} error: {out.error}", file=sys.stderr)
        result["error"] = out.error
        result["status"] = "error"
        _release_all()
        _emit_result(result)
        return 1

    if args.dry_run or not out.intents:
        result["status"] = "no-op"
        print(f"run_id={run_id} DRY-RUN {len(out.intents)} intent(s): "
              f"no writes performed")
        _release_all()
        _emit_result(result)
        return 0

    # Execution half: resolve media per package, then inject.
    # Packages and intents align 1:1 through G3 (see R5).
    from injection.service import InjectionService
    from injection.trace import StderrTracer
    service = InjectionService(tracer=StderrTracer())
    post_ids: list[str] = []
    for pkg, intent in zip(out.packages, out.intents):
        images = []
        for m in pkg.media:
            up = _resolve_media(client, m)
            images.append({"id": up["id"], "path": up["path"]})
            print(f"run_id={run_id} media -> {up['path'][:80]}")
        intent.media = images
        injected = service.inject(intent, client)
        post_ids.extend(_post_ids(injected.get("response")))
    result["post_ids"] = post_ids
    result["status"] = "success"
    print(f"run_id={run_id} INJECTED {len(post_ids)} post(s) "
          f"[mode={args.post_mode}]")
    _release_all()
    _emit_result(result)
    return 0


def main(argv=None) -> int:
    try:
        return _main(argv)
    except (FileNotFoundError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except TuzzinaError as e:
        print(f"error: {e}", file=sys.stderr)
        return 3
    except UnsupportedPlatform as e:
        print(f"error: {e}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
