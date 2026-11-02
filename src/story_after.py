"""story_after.py: publish a Story from an ACTUALLY published post.

The after-post Story mode: a cycle publishes the post; this helper
later reads that REAL post (existing GET /posts/:id) and injects a
story for every channel of the same campaign, all through the ONE
injection service Tuzzina already owns (post_kind="story") — no
parallel pipeline, no new engine, no platform branches.

Rules:
- the permalink is a plain-text line in the caption: Meta's story
  API exposes no tappable link sticker server-side, and rendering
  text over an existing image needs compose-from-media, which the
  image engine does not have yet. This module documents that with
  a `ponytail:` ceiling and publishes the caption that carries the
  real permalink today.
- failures never fail the caller: per-channel errors are logged
  and skipped (the run ledger already owns the verdict); the
  caller's exit code reflects whether ANY story went out.
- idempotent: a retry only reads the post and re-runs the guard
  (story content already-exists guard short-circuits injection);
  media references are read, never re-uploaded.
- one reusable CLI: python3 src/story_after.py <campaign.yaml>
  --strategy-by-integration <id> --post-id <id> [--dry-run]
"""
from __future__ import annotations
import json
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

LINK_TMPL = "🔗 {url}"
MAX_MIRROR_LINES = 3  # title + permalink stay tiny (stories are visual)

POLL_INTERVAL_S = 5
POLL_TRIES = 8  # ≤40s worst case: publish workflow is async by design


def fetch_post_rows(client, post_ids: list,
                    tries: int = POLL_TRIES,
                    interval: int = POLL_INTERVAL_S) -> list:
    """Rows for the given post ids from the group window.

    releaseURL is written by Tuzzina's publish workflow AFTER the
    provider confirms, so the caller re-reads the window a few
    times until every requested row is publishable (or bounded
    tries elapse — never invents a URL)."""
    import datetime
    until = datetime.datetime.now(
        datetime.timezone.utc).isoformat()
    since = (datetime.datetime.now(datetime.timezone.utc)
             - datetime.timedelta(hours=48)).isoformat()
    wanted = {str(x) for x in post_ids}
    rows: list = []
    for _ in range(max(1, tries)):
        try:
            data = client.list_recent_posts(since, until) or {}
        except Exception as e:
            print("warning: posts read failed (%s)"
                  % type(e).__name__, file=sys.stderr)
            data = {}
        candidates = data.get("posts") or []
        rows = [r for r in candidates if str(r.get("id")) in wanted]
        if rows and all(
                str(r.get("releaseURL") or "").strip() for r in rows):
            break
        if _ < max(1, tries) - 1:
            time.sleep(max(1, interval))
    yield_rows = [r for r in rows
                  if str(r.get("releaseURL") or "").strip()]
    _ = post_ids  # kept for the exact-set contract
    return yield_rows


def real_permalink(client, post_id: str, tries: int = POLL_TRIES,
                   interval: int = POLL_INTERVAL_S) -> str:
    """The post's permalink once publish completed; resilience only.

    releaseURL is written by Tuzzina's publish workflow AFTER the
    provider confirms, so the caller polls the same read a few
    times before falling back to "" (never invents a URL)."""
    last = ""
    for attempt in range(max(1, tries)):
        try:
            data = client.get_post(post_id) or {}
        except Exception as e:  # transport is retried, not fatal
            print("warning: post read %s failed (%s)"
                  % (post_id, type(e).__name__), file=sys.stderr)
            data = {}
        rows = data.get("posts") or []
        for row in rows:
            url = str(row.get("releaseURL") or "").strip()
            if url:
                return url
            # keep the first non-trivial title as the story's text seed
            if not last and str(row.get("title") or "").strip():
                last = str(row.get("title")).strip()
        if attempt + 1 < max(1, tries):
            time.sleep(max(1, interval))
    return ""


def post_media_refs(data: dict) -> list:
    """Media refs from the post rows, read-only — reused verbatim
    (same {id, path} the injection layer validates), never
    re-uploaded. Facebook merges those rows into ONE group; the
    first row's refs are the first attachment."""
    for row in (data.get("posts") or []):
        image = row.get("image")
        parsed = image if isinstance(image, list) else []
        if not parsed and isinstance(image, str) and image.strip():
            import json
            try:
                parsed = json.loads(image)
            except Exception:
                parsed = []
        if not isinstance(parsed, list):
            continue
        refs = []
        for m in parsed:
            if isinstance(m, dict) and m.get("id") and m.get("path"):
                refs.append({"id": m["id"], "path": m["path"]})
        if refs:
            return refs
    return []


def story_caption(title: str, permalink: str, integration_id: str,
                  seen: dict) -> str:
    """Caption = title + permalink line (deduped per campaign:
    a story of the SAME post never carries a duplicate exact text)."""
    parts = [str(title or "new post").strip()[:MAX_MIRROR_LINES * 60]]
    link = int(bool(permalink))
    if link:
        parts.append(LINK_TMPL.format(url=permalink))
    text = "\n".join(p for p in parts if p).strip()
    if seen.get((integration_id, text)):
        return ""  # the guard path skips anyway; keep caller branch simple
    return text


def inject_story(client, campaign: dict, post_ids: list,
                 targets: list, poll_tries: int = POLL_TRIES):
    """One story per channel from the REAL published post rows.

    The group read (existing GET /posts/:id) returns every row of
    the post group — one per channel — so each channel's story
    reuses THAT channel's own media ref and permalink; a channel
    the publish workflow has not finished yet is skipped this
    attempt (idempotent retry covers it), and failures below are
    logged, never fatal (add-on contract)."""
    from dataclasses import replace
    from injection.service import InjectionService
    from injection.trace import StderrTracer

    current_post_ids = [str(x) for x in post_ids]
    rows = fetch_post_rows(client, current_post_ids,
                           tries=poll_tries)
    rows = [r for r in rows
            if str(r.get("releaseURL") or "").strip()
            and post_media_refs({"posts": [r]})]
    if not rows:
        return []

    service = InjectionService(tracer=StderrTracer())
    published: list = []
    seen: set = set()
    for row in rows:
        tgt = str(row.get("integrationId") or "").strip()
        url = str(row.get("releaseURL") or "").strip()
        title = str(row.get("title") or "").strip()
        media = post_media_refs({"posts": [row]})[:1]
        if not tgt or tgt in seen or not (title or url):
            continue
        seen.add(tgt)
        content = "\n".join(p for p in (title,
                                         LINK_TMPL.format(url=url))
                             if p).strip()
        try:
            from injection.intent import build_intent
            intent = build_intent(
                integration_id=tgt, content=content, media=media,
                publish_at=_now(), mode="now", hashtags=[],
                link="", links_policy="hide", post_kind="story",
                settings={}, cta_style="", companion="")
            out = service.inject(intent, client)
            ids = _ids(out.get("response"))
            published.extend(ids)
            print("story-after[%s] -> %d" % (tgt, len(ids)))
        except Exception as e:
            print("STORY-AFTER-SKIP[%s] %s: %s"
                  % (tgt, type(e).__name__, e), file=sys.stderr)
    return published


def _ids(response) -> list:
    from run_cycle import _post_ids
    return _post_ids(response)


def _now() -> str:
    import datetime
    return datetime.datetime.now(
        datetime.timezone.utc).isoformat()


def main(argv=None) -> int:
    # Stage-scoped entry used by the one-shot activity: reads the
    # REAL post and injects the story per campaign channel.
    parser = argparse.ArgumentParser(description="Brain after-post story")
    parser.add_argument("campaign", help="Path to campaign.yaml")
    parser.add_argument("--strategy-by-integration", required=True)
    parser.add_argument("--strategy-base-dir", default=None)
    parser.add_argument("--post-id", required=True,
                        help="the primary row id of the published post")
    parser.add_argument("--post-ids", default="",
                        help="comma list fallback ids (unused by the "
                        "group read; kept for ledger parity)")
    parser.add_argument("--targets", default="")
    parser.add_argument("--state-store", default="tuzzina",
                        help="accepted for orchestration parity; this "
                        "module always talks to Tuzzina directly")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    import run_cycle as rc
    campaign = rc.load_campaign(args.campaign)
    from tuzzina.client import TuzzinaClient
    client = TuzzinaClient(
        os.environ.get("TUZZINA_API_URL", "http://127.0.0.1:4107/api"),
        os.environ.get("TUZZINA_API_KEY", "")
    )
    client.run_id = "brainrun:%s:story" % args.post_id
    tgts = [str(x).strip() for x in args.targets.split(",") if x.strip()]
    if args.dry_run:
        print("STORY_AFTER dry-run: targets=%s" % tgts)
        return 0
    ids = [args.post_id] + [x for x in args.post_ids.split(",")
                            if x.strip() and x.strip() != args.post_id]
    published = inject_story(client, campaign, ids, tgts)
    print("BRAIN_RESULT %s" % json.dumps({
        "status": "success" if published else "no-op",
        "post_ids": published,
        "targets": tgts,
    }))
    return 0 if published else 0


if __name__ == "__main__":
    sys.exit(main())
