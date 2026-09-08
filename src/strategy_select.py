"""Brain strategy selection CLI. Pure read-only against Tuzzina's
Public API, then prompts the operator for Brain-owned policy fields
and saves to brain-strategies/<integration_id>.yaml.

No Tuzzina write happens during selection. select.py NEVER calls
POST endpoints. run.py is the only entry that creates posts.

Usage:
  TUZZINA_API_KEY=... python3 src/strategy_select.py

The list of integrations is read at startup. If you add a new
channel in Tuzzina, re-run select.py.

If stdin is not a TTY, select.py reads answers from stdin as a
JSON object (machine-readable mode for the upcoming Brain API/
Frontend). The JSON object's keys map to the same Brain-owned
fields; the integration_id is selected via the --integration-id
flag in non-TTY mode.
"""
from __future__ import annotations
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from channels.strategy import (Brand, ChannelStrategy, ContentPolicy,
                                GenerationPolicy, HashtagPolicy,
                                PlanningPolicy)
from channels.strategy_store import (exists, path_for, save,
                                       validate_integration_id)
from tuzzina.client import TuzzinaClient, TuzzinaError


def _ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    val = input(f"{prompt}{suffix}: ").strip()
    return val or default


def _ask_list(prompt: str, default: list | None = None) -> list:
    default_str = ",".join(default or [])
    raw = _ask(f"{prompt} (comma-separated)", default_str)
    return [x.strip() for x in raw.split(",") if x.strip()]


def _ask_int(prompt: str, default: int) -> int:
    raw = _ask(prompt, str(default))
    try:
        return int(raw)
    except ValueError:
        print(f"  not a number, using default {default}")
        return default


def _list_integrations(client: TuzzinaClient) -> list:
    return client.integrations()


def _print_table(items: list) -> None:
    print(f"  Tuzzina says: {len(items)} integrations connected.")
    if not items:
        return
    print(f"  {'#':<3} {'id':<40} {'name':<32} {'platform':<12}")
    print(f"  {'-'*3} {'-'*40} {'-'*32} {'-'*12}")
    for i, it in enumerate(items, 1):
        print(f"  {i:<3} {str(it.get('id',''))[:40]:<40} "
              f"{str(it.get('name',''))[:32]:<32} "
              f"{str(it.get('identifier',''))[:12]:<12}")


def _interactive_select(items: list) -> dict:
    _print_table(items)
    while True:
        raw = input("Select integration (number or id) [q to quit]: ").strip()
        if raw.lower() in ("q", "quit", ""):
            raise SystemExit(0)
        if raw.isdigit():
            idx = int(raw) - 1
            if 0 <= idx < len(items):
                return items[idx]
        else:
            for it in items:
                if str(it.get("id", "")) == raw:
                    return it
        print("  invalid selection, try again")


def _ask_strategy_fields() -> dict:
    """Prompt only for Brain-owned fields. Channel name, platform,
    OAuth, and provider identity come from Tuzzina and are NEVER
    asked here."""
    print()
    print("Enter Brain-owned strategy fields (channel name / platform / "
          "OAuth come from Tuzzina).")
    print("Press Enter to accept the default in brackets.")
    print()
    tone = _ask("tone", "neutral")
    audience = _ask("audience", "general")
    banned = _ask_list("banned_words", [])
    preferred = _ask_list("preferred_words", [])
    cta_style = _ask("cta_style", "Learn more")
    print()
    print("Hashtag policy:")
    enabled = _ask("hashtags.enabled (y/n)", "y").lower().startswith("y")
    hs_max = _ask_int("hashtags.max", 5)
    hs_preferred = _ask_list("hashtags.preferred", [])
    print()
    print("Content policy:")
    pillars = _ask_list("content.content_pillars", [])
    print()
    print("Generation policy:")
    provider = _ask("generation.provider (auto/openai/mock)", "auto")
    print()
    print("Sources for THIS channel (per-channel, not shared):")
    src_urls = _ask_list("source website URLs (comma-separated)", [])
    src_n = _ask_int("items per source (n)", 3)
    print()
    print("Planning policy (this run's schedule is taken from the "
          "campaign, the strategy's days/times are advisory):")
    cadence = _ask("planning.cadence (e.g. weekly, daily)", "")
    return {
        "brand": {
            "tone": tone, "audience": audience,
            "banned_words": banned, "preferred_words": preferred,
            "cta_style": cta_style,
        },
        "hashtags": {"enabled": enabled, "max": hs_max,
                      "preferred": hs_preferred},
        "content": {"content_pillars": pillars},
        "generation": {"provider": provider},
        "planning": {"cadence": cadence},
        "links_policy": "hide",
        "sources": [{"type": "website", "url": u, "n": src_n}
                    for u in src_urls],
        "provider_overrides": {},
        "extras": {},
    }


def _fields_to_strategy(integration_id: str, f: dict) -> ChannelStrategy:
    from channels.strategy import (MediaPolicy, MentionPolicy, _MISSING)
    b = f.get("brand") or {}
    h = f.get("hashtags") or {}
    c = f.get("content") or {}
    g = f.get("generation") or {}
    p = f.get("planning") or {}
    md = f.get("media") or {}
    mn = f.get("mentions") or {}

    def _mi(v):
        return int(v) if v is not None else _MISSING

    srcs = []
    for s in f.get("sources") or []:
        if not isinstance(s, dict):
            continue
        url = str(s.get("url", "")).strip()
        if not url:
            continue
        n = s.get("n", 3)
        srcs.append({"type": "website", "url": url,
                     "n": int(n) if isinstance(n, int) and n >= 1 else 3})
    return ChannelStrategy(
        integration_id=integration_id,
        brand=Brand(
            tone=b.get("tone") or "neutral",
            audience=b.get("audience") or "general",
            banned_words=list(b.get("banned_words") or []),
            preferred_words=list(b.get("preferred_words") or []),
            cta_style=b.get("cta_style") or "Learn more",
        ),
        hashtags=HashtagPolicy(
            enabled=bool(h.get("enabled", True)),
            max=int(h.get("max", 5)),
            preferred=list(h.get("preferred") or []),
        ),
        content=ContentPolicy(
            content_pillars=list(c.get("content_pillars") or []),
        ),
        generation=GenerationPolicy(
            provider=g.get("provider", "auto"),
        ),
        planning=PlanningPolicy(
            cadence=p.get("cadence") or "",
        ),
        mentions=MentionPolicy(
            style=mn.get("style") or "inline",
        ),
        media=MediaPolicy(
            min_items=_mi(md.get("min_items")),
            max_items=_mi(md.get("max_items")),
        ),
        sources=srcs,
        links_policy=f.get("links_policy", "hide"),
        provider_overrides=dict(f.get("provider_overrides") or {}),
        extras=dict(f.get("extras") or {}),
    )


def _non_tty_flow(client: TuzzinaClient, integration_id: str,
                  fields: dict, base_dir: str | None = None) -> str:
    """Machine-readable flow: validate id, validate against Tuzzina,
    save. Used by the upcoming Brain API/Frontend. select.py in
    non-TTY mode is read-only on Tuzzina (one GET) and one local
    write."""
    validate_integration_id(integration_id)
    integ = client.get_integration(integration_id)
    strategy = _fields_to_strategy(integration_id, fields)
    target = save(strategy, base_dir=base_dir)
    return json.dumps({
        "ok": True,
        "integration_id": integration_id,
        "name": integ.get("name"),
        "platform": integ.get("identifier"),
        "path": str(target),
    })


def main(argv=None) -> int:
    try:
        return _main(argv)
    except (ValueError,) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except TuzzinaError as e:
        print(f"error: {e}", file=sys.stderr)
        return 3


def _main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Brain strategy selection")
    p.add_argument("--integration-id",
                   help="(non-TTY mode) the Tuzzina integration to attach "
                        "the strategy to. Required when stdin is not a "
                        "TTY; the channel's name/platform are read from "
                        "Tuzzina, never typed here.")
    p.add_argument("--stdin-json", action="store_true",
                   help="Read strategy fields as JSON from stdin (used "
                        "by the upcoming Brain API/Frontend). Implies "
                        "--integration-id.")
    p.add_argument("--base-dir", default=None,
                   help="Override the brain-strategies/ directory.")
    args = p.parse_args(argv)

    key = os.environ.get("TUZZINA_API_KEY", "")
    base = os.environ.get("TUZZINA_API_URL", "http://127.0.0.1:4107/api")
    if not key:
        print("error: TUZZINA_API_KEY is not set", file=sys.stderr)
        return 2
    client = _build_client(base, key)

    tty = sys.stdin.isatty() and sys.stdout.isatty()
    if args.stdin_json or not tty:
        try:
            payload = json.load(sys.stdin)
        except json.JSONDecodeError as e:
            print(f"error: invalid JSON on stdin: {e}", file=sys.stderr)
            return 2
        integration_id = args.integration_id or payload.get("integration_id", "")
        if not integration_id:
            print("error: --integration-id required in non-TTY mode",
                  file=sys.stderr)
            return 2
        fields = payload.get("strategy") or {}
        try:
            out = _non_tty_flow(client, integration_id, fields,
                                 base_dir=args.base_dir)
        except TuzzinaError as e:
            print(f"error: {e}", file=sys.stderr)
            return 3
        print(out)
        return 0

    print("Brain strategy selection.")
    print("  source of truth for channels: Tuzzina")
    print("  you choose ONE existing integration. Brain attaches a strategy to it.")
    print()
    items = _list_integrations(client)
    selected = _interactive_select(items)
    integration_id = str(selected.get("id", "")).strip()
    print()
    print(f"Selected: id={integration_id}  "
          f"name={selected.get('name')!r}  "
          f"platform={selected.get('identifier')!r}")
    if exists(integration_id, base_dir=args.base_dir):
        print(f"  (a strategy file already exists at {path_for(integration_id, args.base_dir)} — it will be overwritten.)")
    fields = _ask_strategy_fields()
    strategy = _fields_to_strategy(integration_id, fields)
    target = save(strategy, base_dir=args.base_dir)
    print(f"Saved: {target}")
    return 0


def _build_client(base: str, key: str) -> TuzzinaClient:
    """Test seam: tests can monkey-patch select.TuzzinaClient to
    return a stub client. The default builds a real TuzzinaClient
    that talks to Tuzzina's Public API."""
    return TuzzinaClient(base, key)


if __name__ == "__main__":
    raise SystemExit(main())
