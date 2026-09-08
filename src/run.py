"""run.py: campaign + strategy-by-integration -> Tuzzina.
Usage: TUZZINA_API_KEY=... python3 src/run.py <campaign.yaml> --strategy-by-integration <integration_id> [--mock|--openai] [--post-mode draft|schedule|now] [--dry-run]

The strategy carries Brain-owned policy only. The integration's name,
platform, picture, OAuth and DTOs are read from Tuzzina at runtime.
Tuzzina is the source of truth for channel identity.
"""
from __future__ import annotations
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from campaign import load_campaign
from channels.profile import resolve_strategy
from channels.strategy_store import load as load_strategy
from contracts import ContentPackage
from g1.website import WebsiteAdapter
from g2 import generators as G
from g2.pipeline import build_package
from g3.planner import plan
from injection.intent import build_intent
from injection.service import InjectionService
from tuzzina.client import TuzzinaClient, TuzzinaError
from adapters.base import UnsupportedPlatform


def _build_client(base: str, key: str) -> TuzzinaClient:
    """Test seam: tests can monkey-patch this to return a stub."""
    return TuzzinaClient(base, key)


def _build_generators(mode: str):
    oai = os.environ.get("OPENAI_API_KEY", "")
    if mode == "--openai":
        if not oai:
            raise SystemExit("OPENAI_API_KEY is not set; use --mock")
        return G.OpenAITextGenerator(oai), G.TuzzinaImageGenerator()
    return G.MockTextGenerator(), G.MockImageGenerator()


def _resolve_media(client: TuzzinaClient, m: dict) -> dict:
    """One planned media item -> Tuzzina {id, path} reference.

    url kind: existing upload_from_url (extracted source media).
    Delegated generator kind (TuzzinaImageGenerator): client
    generate_image (Tuzzina's stored reference, no Brain-side bytes,
    no Brain-side upload). Mock/legacy generator kind:
    generate_png + upload_bytes (unit tests and --mock paths only).
    """
    if m.get("kind") == "url":
        return client.upload_from_url(m["url"])
    gen = m["generator"]
    if isinstance(gen, G.TuzzinaImageGenerator):
        return client.generate_image(m["prompt"])
    blob, fname = gen.generate_png(m["prompt"])
    return client.upload_bytes(blob, fname, "image/png")


def _execute(cfg: dict, mode: str, client: TuzzinaClient,
             integration_id: str, post_mode: str = "draft",
             dry_run: bool = False) -> int:
    from adapters import get_adapter
    text_gen, image_gen_obj = _build_generators(mode)
    identifier = str(cfg.get("channel_meta", {}).get("identifier") or "")
    try:
        adapter = get_adapter(identifier)
    except UnsupportedPlatform as e:
        print(f"error: {e}", file=sys.stderr)
        return 4
    print(f"adapter: {type(adapter).__name__} "
          f"(identifier={identifier!r})")
    caps = adapter.capabilities()
    src_adapters = {"website": WebsiteAdapter()}
    # Sources precedence: cfg["sources"] already resolved
    # (channel strategy > campaign fallback) by resolve_strategy.
    print(f"sources from: {cfg.get('sources_from', 'unknown')}")
    packages: list[ContentPackage] = []
    for src in cfg["sources"]:
        res = src_adapters[src["type"]].extract(src["url"], src["n"])
        print(f"G1 {src['type']} {src['url']}: "
              f"{res.meta.get('returned')}/{res.meta.get('requested')} "
              f"(partial={res.meta.get('partial')})")
        for item in res.items:
            packages.append(build_package(
                item, cfg["brand"], cfg["language"], cfg["hashtags"],
                cfg["links_policy"], text_gen, image_gen_obj,
                platform=identifier,
                platform_settings=cfg["provider_overrides"],
                limits={"max_length": caps.get("max_length")},
                link_fn=adapter.apply_link))
    if not packages:
        print("no content extracted; nothing to do")
        return 1
    # Media policy (strategy) + adapter bounds (platform).
    media_policy = cfg.get("media_policy") or {}
    kept: list[ContentPackage] = []
    for pkg in packages:
        try:
            shaped = adapter.shape_media(pkg.media)
        except UnsupportedPlatform as e:
            print(f"skip package {pkg.source_ref}: {e}")
            continue
        lo = media_policy.get("min_items")
        hi = media_policy.get("max_items")
        if isinstance(hi, int) and hi >= 0:
            shaped = shaped[:hi]
        if isinstance(lo, int) and lo > 0 and len(shaped) < lo:
            print(f"skip package {pkg.source_ref}: "
                  f"media below min_items={lo}")
            continue
        pkg.media = shaped
        # NOTE: no adapter.shape_text here. pkg.content stays plain and
        # pkg.hashtags stay separate; InjectionService assembles once.
        kept.append(pkg)
    if not kept:
        print("no packages survived media policy; nothing to do")
        return 1
    planned = plan(kept, cfg["schedule"])
    print(f"G3 planned {len(planned)} posts")

    if dry_run:
        return _dry_run_report(cfg, planned, integration_id, identifier,
                               adapter, post_mode)

    # Injection path (the ONLY publish path): each planned post
    # becomes a CanonicalIntent consumed by InjectionService, which
    # resolves the integration live, selects the adapter from
    # Tuzzina's identifier, translates, and posts via TuzzinaClient.
    # The stderr tracer emits causal trace lines; behavior otherwise
    # identical to a silent service.
    from injection.trace import StderrTracer
    service = InjectionService(tracer=StderrTracer())
    overrides = cfg.get("provider_overrides") or {}
    brand_cta = cfg.get("brand", {}).get("cta_style", "Learn more")
    links_policy = cfg.get("links_policy", "hide")
    results = []
    for p in planned:
        images = []
        for m in p.media:
            up = _resolve_media(client, m)
            images.append({"id": up["id"], "path": up["path"]})
            print(f"media -> {up['path'][:80]}")
        pk = overrides.get("post_type")
        intent = build_intent(
            integration_id=integration_id,
            content=p.content,
            media=images,
            publish_at=p.planned_at,
            mode=post_mode,
            hashtags=list(p.tags),
            link=p.source_ref if links_policy == "attach" else "",
            links_policy=links_policy,
            post_kind=pk if pk in ("post", "story") else "post",
            settings=dict(overrides),
            cta_style=brand_cta,
        )
        out = service.inject(intent, client)
        results.append(out)
    print(f"INJECTED {len(results)} post(s) [mode={post_mode}]:")
    for r in results:
        print(json.dumps({k: r[k] for k in (
            "integration_id", "identifier", "post_kind", "mode",
            "publish_at")}, ensure_ascii=False))
    return 0


def _dry_run_report(cfg: dict, planned: list, integration_id: str,
                    identifier: str, adapter, post_mode: str) -> int:
    """Dry-run display only. Reads planned posts and prints a safe
    summary per post. Performs ZERO writes: no upload, no
    upload-from-url, no create_post, no InjectionService call.
    Only safe metadata is shown (lengths/counts/keys, never content
    bodies beyond length, never secrets)."""
    overrides = cfg.get("provider_overrides") or {}
    pk = overrides.get("post_type")
    post_kind = pk if pk in ("post", "story") else "post"
    print(f"DRY-RUN {len(planned)} post(s) [mode={post_mode}]: "
          f"no writes performed")
    for i, p in enumerate(planned, 1):
        kinds = sorted({str(m.get("kind", "?"))
                        for m in (p.media or []) if isinstance(m, dict)})
        print(json.dumps({
            "n": i,
            "integration_id": integration_id,
            "identifier": identifier,
            "adapter": type(adapter).__name__,
            "mode": post_mode,
            "post_kind": post_kind,
            "publish_at": p.planned_at,
            "content_chars": len(p.content or ""),
            "hashtags": len(p.tags or []),
            "media_count": len(p.media or []),
            "media_kinds": kinds,
            "has_link": bool(
                (cfg.get("links_policy", "hide") == "attach")),
            "settings_keys": sorted(str(k) for k in overrides),
        }, ensure_ascii=False))
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


def _main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Brain run: campaign + strategy -> Tuzzina")
    p.add_argument("campaign", help="Path to campaign.yaml")
    p.add_argument("--strategy-by-integration", required=True,
                   help="Tuzzina integration id whose Brain strategy "
                        "(brain-strategies/<id>.yaml) should be used. "
                        "Channel identity is read from Tuzzina, not "
                        "from this argument.")
    p.add_argument("--strategy-base-dir", default=None,
                   help="Override the brain-strategies/ directory")
    p.add_argument("--mock", action="store_const", dest="mode",
                   const="--mock", default="--mock")
    p.add_argument("--openai", action="store_const", dest="mode",
                   const="--openai")
    p.add_argument("--post-mode", choices=["draft", "schedule", "now"],
                   default="draft",
                   help="Tuzzina post type for injected posts "
                        "(default: draft)")
    p.add_argument("--dry-run", action="store_true",
                   help="Plan and display only: runs G1/G2/G3 and shows "
                        "what would be injected, with ZERO writes "
                        "(no upload, no posts). get_integration read "
                        "is still performed to resolve the channel.")
    args = p.parse_args(argv)

    campaign = load_campaign(args.campaign)

    key = os.environ.get("TUZZINA_API_KEY", "")
    base = os.environ.get("TUZZINA_API_URL", "http://127.0.0.1:4107/api")
    if not key:
        print("error: TUZZINA_API_KEY is not set", file=sys.stderr)
        return 2
    client = _build_client(base, key)

    if args.strategy_by_integration:
        strategy = load_strategy(args.strategy_by_integration,
                                 base_dir=args.strategy_base_dir)
        integ = client.get_integration(args.strategy_by_integration)
        channel_meta = {
            "integration_id": integ.get("id"),
            "name": integ.get("name"),
            "identifier": integ.get("identifier"),
            "picture": integ.get("picture"),
        }
        print(f"channel: name={integ.get('name')!r} "
              f"id={integ.get('id')} platform={integ.get('identifier')!r}")
        cfg = resolve_strategy(campaign, strategy, channel_meta=channel_meta)
        return _execute(cfg, args.mode, client, args.strategy_by_integration,
                        post_mode=args.post_mode, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
