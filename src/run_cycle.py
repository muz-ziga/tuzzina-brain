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
from run import _build_client, _build_generators, _resolve_media


def _post_ids(response) -> list:
    """Extract post ids from a create_post response. Never raises;
    mirrors the trace helper without importing privates."""
    try:
        items = response if isinstance(response, list) else [response]
        return [str(x.get("postId")) for x in items
                if isinstance(x, dict) and x.get("postId")]
    except Exception:
        return []


def _build_models(mode: str):
    """Role models for the cycle. --openai wires the real OpenAI
    trio on one env key; anything else stays Mock (deterministic,
    offline). Per-role multi-vendor selection is supported by the
    Phase 1 adapters but has no CLI surface yet: assignments live
    server-side (Phase 3) and need a resolution endpoint before a
    trigger can pass them. Documented, not silently defaulted."""
    from research.models import MockResearchModel, OpenAIResearchModel
    from research.analysis import MockAnalysisModel, OpenAIAnalysisModel
    if mode == "--openai":
        oai = os.environ.get("OPENAI_API_KEY", "")
        if not oai:
            raise SystemExit("OPENAI_API_KEY is not set; use --mock")
        return OpenAIResearchModel(oai), OpenAIAnalysisModel(oai)
    return MockResearchModel(), MockAnalysisModel()


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
              "post_ids": [], "committed": False, "error": ""}

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

    text_gen, image_gen_obj = _build_generators(args.mode)
    research_model, analysis_model = _build_models(args.mode)
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

    out = run_cycle(
        cfg.get("sources") or [], store=store, policy=cfg,
        schedule=cfg.get("schedule") or {}, text_gen=text_gen,
        image_gen=image_gen_obj, research_model=research_model,
        analysis_model=analysis_model, mode=args.post_mode,
        integration_id=args.strategy_by_integration,
        video_resolve=None if args.dry_run else _video_resolve)

    result["sources_checked"] = len(out.sources)
    result["items_new"] = len(out.items)
    result["eligible"] = bool(
        out.opportunity is not None and out.opportunity.eligible)
    result["intents"] = len(out.intents)
    result["committed"] = bool(out.committed)
    if out.error:
        print(f"run_id={run_id} error: {out.error}", file=sys.stderr)
        result["error"] = out.error
        result["status"] = "error"
        _emit_result(result)
        return 1

    if args.dry_run or not out.intents:
        result["status"] = "no-op"
        print(f"run_id={run_id} DRY-RUN {len(out.intents)} intent(s): "
              f"no writes performed")
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
