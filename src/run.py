"""run.py: campaign + (strategy.yaml | --strategy-by-integration) -> Tuzzina.
Usage A (legacy): TUZZINA_API_KEY=... python3 src/run.py <campaign.yaml> <strategy.yaml> [--mock|--openai]
Usage B (canonical): TUZZINA_API_KEY=... python3 src/run.py <campaign.yaml> --strategy-by-integration <integration_id> [--mock|--openai]

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
from channels.loader import load_channel_profile
from channels.profile import resolve, resolve_strategy
from channels.strategy_store import load as load_strategy
from contracts import ContentPackage
from g1.website import WebsiteAdapter
from g2 import generators as G
from g2.pipeline import build_package
from g3.planner import plan
from tuzzina.client import TuzzinaClient, TuzzinaError


def _build_client(base: str, key: str) -> TuzzinaClient:
    """Test seam: tests can monkey-patch this to return a stub."""
    return TuzzinaClient(base, key)


def _build_generators(mode: str):
    oai = os.environ.get("OPENAI_API_KEY", "")
    if mode == "--openai":
        if not oai:
            raise SystemExit("OPENAI_API_KEY is not set; use --mock")
        return G.OpenAITextGenerator(oai), None
    return G.MockTextGenerator(), G.MockImageGenerator()


def _run(campaign_path: str, strategy, channel_meta: dict, mode: str,
         client: TuzzinaClient) -> int:
    if isinstance(strategy, dict):
        cfg = strategy
    else:
        from channels.profile import ChannelProfile
        from channels.strategy import ChannelStrategy
        if isinstance(strategy, ChannelStrategy):
            cfg = resolve_strategy(campaign_path if isinstance(
                campaign_path, dict) else {}, strategy, channel_meta)
        elif isinstance(strategy, ChannelProfile):
            cfg = resolve(strategy, channel_meta=channel_meta)
        else:
            raise TypeError("strategy must be ChannelStrategy or "
                            "ChannelProfile or dict")
    return _execute(cfg, mode, client, strategy.integration_id)


def _execute(cfg: dict, mode: str, client: TuzzinaClient,
             integration_id: str) -> int:
    from adapters import get_adapter
    from adapters.base import UnsupportedPlatform
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
        # Adapter text shaping (length cap, hashtag placement).
        pkg.content = adapter.shape_text(pkg.content, pkg.hashtags)
        kept.append(pkg)
    if not kept:
        print("no packages survived media policy; nothing to do")
        return 1
    planned = plan(kept, cfg["schedule"])
    print(f"G3 planned {len(planned)} posts")

    posts_payload = []
    for p in planned:
        images = []
        for m in p.media:
            if m.get("kind") == "url":
                up = client.upload_from_url(m["url"])
            else:
                gen = m["generator"]
                blob, fname = gen.generate_png(m["prompt"])
                up = client.upload_bytes(blob, fname, "image/png")
            images.append({"id": up["id"], "path": up["path"]})
            print(f"media -> {up['path'][:80]}")
        posts_payload.append({
            "integration": {"id": integration_id},
            "value": [{"content": p.content, "image": images}],
            "settings": p.settings,
        })
    date = planned[0].planned_at
    out = client.create_draft(posts_payload, date)
    print("DRAFTS CREATED:")
    print(json.dumps(out, ensure_ascii=False)[:800])
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


def _main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Brain run: campaign + strategy -> Tuzzina")
    p.add_argument("campaign", help="Path to campaign.yaml")
    p.add_argument("strategy", nargs="?",
                   help="Path to strategy.yaml (legacy mode; or omit "
                        "and use --strategy-by-integration)")
    p.add_argument("--strategy-by-integration", default=None,
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
    args = p.parse_args(argv)

    if args.strategy and args.strategy_by_integration:
        print("error: pass either <strategy.yaml> or "
              "--strategy-by-integration, not both", file=sys.stderr)
        return 2
    if not args.strategy and not args.strategy_by_integration:
        print("usage: run.py <campaign.yaml> <strategy.yaml>\n"
              "       run.py <campaign.yaml> --strategy-by-integration "
              "<id>", file=sys.stderr)
        return 2

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
        # CRITICAL: the platform inside the resolved config is set
        # by the runtime channel_meta, not by the strategy. A future
        # audit must confirm channel_meta['identifier'] is what Tuzzina
        # returned, not what the YAML claims.
        return _execute(cfg, args.mode, client, args.strategy_by_integration)

    # Legacy mode: explicit strategy.yaml
    profile = load_channel_profile(args.strategy)
    integ = client.get_integration(profile.integration_id)
    channel_meta = {
        "integration_id": integ.get("id"),
        "name": integ.get("name"),
        "identifier": integ.get("identifier"),
        "picture": integ.get("picture"),
    }
    print(f"channel: name={integ.get('name')!r} "
          f"id={integ.get('id')} platform={integ.get('identifier')!r}")
    cfg = resolve(campaign, profile, channel_meta=channel_meta)
    return _execute(cfg, args.mode, client, profile.integration_id)


if __name__ == "__main__":
    raise SystemExit(main())
