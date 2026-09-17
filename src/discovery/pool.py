"""Candidate pool (Stage G). Source-neutral retention of
eligible discovered material, independent of publication
timing.

Design constraints (non-negotiable, see AGENTS.md):
- Pure functions over plain dicts. No network, no clock
  (``now`` is an explicit ISO string), no store, no LLM,
  no claiming, no scheduling. The pool never reserves,
  never publishes, never calls Research.
- Persistence lives in Tuzzina: the pool serializes to the
  ``{"candidates": {...}}`` envelope saved under one
  well-known source id (POOL_SOURCE_ID) through the
  existing research-state endpoint. Brain owns no DB.
- Identity is the shared stable key (item_id when present,
  else platform/external_id/URL/hash precedence per
  monitor.stable_identity). Title alone is never identity.
- Freshness mirrors Stage E policy: parseable-stale is
  dropped, dateless is kept, dates are never invented.

Entry shape (all strings unless noted):
  candidate_id, source_type, platform, capability, tool,
  server, query, source_url, title, snippet, author,
  published_at, discovered_at, external_id, first_seen.
"""
from __future__ import annotations
from datetime import datetime, timezone

POOL_SOURCE_ID = "candidate-pool"

MAX_ENTRIES = 200
MAX_ENVELOPE_BYTES = 1_000_000

_FIELD_LIMITS = {
    "candidate_id": 500,
    "source_type": 64,
    "platform": 64,
    "capability": 64,
    "tool": 128,
    "server": 128,
    "query": 500,
    "source_url": 500,
    "title": 200,
    "snippet": 500,
    "author": 120,
    "published_at": 64,
    "discovered_at": 64,
    "external_id": 256,
    "first_seen": 64,
}

_ENTRY_KEYS = tuple(sorted(_FIELD_LIMITS))


def _clean_str(value, limit: int) -> str:
    try:
        text = str(value) if value is not None else ""
    except Exception:
        return ""
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[:limit]
    return text


def _parse_dt(value: str):
    try:
        text = (value or "").strip()
        if not text:
            return None
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def entry_from_item(item, *, now: str, query: str = "",
                    tool: str = "", server: str = "") -> dict | None:
    """One SourceItem-like object -> pool entry dict, or None
    when it carries no stable identity (never stored, never
    invented). Duck-typed via getattr so tests never import
    the contracts module."""
    try:
        cid = _clean_str(getattr(item, "item_id", ""),
                         _FIELD_LIMITS["candidate_id"])
        if not cid:
            return None
        return {
            "candidate_id": cid,
            "source_type": _clean_str(
                getattr(item, "source_type", ""), 64),
            "platform": _clean_str(
                getattr(item, "platform", ""), 64),
            "capability": _clean_str(
                getattr(item, "capability", ""), 64),
            "tool": _clean_str(tool, 128),
            "server": _clean_str(server, 128),
            "query": _clean_str(query, 500),
            "source_url": _clean_str(
                getattr(item, "source_url", ""), 500),
            "title": _clean_str(getattr(item, "title", ""),
                                200),
            "snippet": _clean_str(getattr(item, "text", ""),
                                  500),
            "author": _clean_str(getattr(item, "author", ""),
                                 120),
            "published_at": _clean_str(
                getattr(item, "published_at", ""), 64),
            "discovered_at": _clean_str(
                getattr(item, "discovered_at", "") or now, 64),
            "external_id": _clean_str(
                getattr(item, "external_id", ""), 256),
            "first_seen": _clean_str(now, 64),
        }
    except Exception:
        return None


def upsert(pool: dict | None, entries: list,
           *, now: str, max_entries: int = MAX_ENTRIES) -> dict:
    """Merge entries into a NEW pool dict (input untouched).
    Existing ids keep their original first_seen; mutable
    fields refresh (UPDATED preserves identity). Empty pool
    on bad input (fail-closed, never raises). Caps at
    max_entries by oldest first_seen (deterministic)."""
    base = dict(pool) if isinstance(pool, dict) else {}
    try:
        limit = int(max_entries)
    except Exception:
        limit = MAX_ENTRIES
    if limit < 0:
        limit = 0
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        cid = entry.get("candidate_id", "")
        if not isinstance(cid, str) or not cid:
            continue
        cleaned = {k: (entry.get(k, "") if isinstance(
            entry.get(k, ""), str) else "")
            for k in _ENTRY_KEYS}
        if not cleaned["candidate_id"]:
            continue
        prev = base.get(cid)
        if isinstance(prev, dict):
            merged = dict(prev)
            merged.update(cleaned)
            merged["first_seen"] = prev.get("first_seen",
                                            cleaned["first_seen"])
            merged["discovered_at"] = prev.get(
                "discovered_at", cleaned["discovered_at"])
            base[cid] = merged
        else:
            base[cid] = cleaned
    if limit >= 0 and len(base) > limit:
        ordered = sorted(base.items(),
                         key=lambda kv: (kv[1].get("first_seen",
                                                   ""), kv[0]))
        base = dict(ordered[-limit:])
    return base


def _eligible(entry: dict, cutoff) -> bool:
    pub = _parse_dt(entry.get("published_at", ""))
    if pub is not None and cutoff is not None and pub < cutoff:
        return False
    return True


def select(pool: dict | None, *, now: str,
           max_age_days=None, limit: int = 10,
           exclude=()) -> list:
    """Eligible entries, deterministic order: dated newest
    first, then dateless by discovered_at desc, ties by id.
    Stale (parseable older than cutoff) dropped; dateless
    kept per Stage E policy. Excluded ids skipped (lets a
    caller withhold claimed ids WITHOUT claiming here).
    Never raises; empty pool selects nothing."""
    try:
        lim = int(limit)
    except Exception:
        lim = 10
    if lim < 0:
        lim = 0
    cutoff = None
    try:
        if max_age_days is not None and int(max_age_days) >= 0:
            base = _parse_dt(now)
            if base is not None:
                from datetime import timedelta
                cutoff = base - timedelta(days=int(max_age_days))
    except (TypeError, ValueError):
        cutoff = None
    skip = set(exclude or [])
    rows = []
    src = pool if isinstance(pool, dict) else {}
    for cid, entry in src.items():
        if not isinstance(entry, dict):
            continue
        if cid in skip:
            continue
        if not _eligible(entry, cutoff):
            continue
        rows.append(entry)
    def _sort_key(entry):
        pub = _parse_dt(entry.get("published_at", ""))
        disc = _parse_dt(entry.get("discovered_at", ""))
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        if pub is not None:
            return (1, pub, disc or epoch,
                    entry.get("candidate_id", ""))
        return (0, disc or epoch, epoch,
                entry.get("candidate_id", ""))
    rows.sort(key=_sort_key, reverse=True)
    return rows[:lim]


def prune(pool: dict | None, *, now: str,
          max_age_days=None,
          max_entries: int = MAX_ENTRIES) -> dict:
    """Drop stale entries (same rule as select) then cap by
    oldest first_seen. Returns a NEW dict. Pure."""
    try:
        lim = int(max_entries)
    except Exception:
        lim = MAX_ENTRIES
    if lim < 0:
        lim = 0
    cutoff = None
    try:
        if max_age_days is not None and int(max_age_days) >= 0:
            base = _parse_dt(now)
            if base is not None:
                from datetime import timedelta
                cutoff = base - timedelta(days=int(max_age_days))
    except (TypeError, ValueError):
        cutoff = None
    src = pool if isinstance(pool, dict) else {}
    kept = {cid: dict(entry) for cid, entry in src.items()
            if isinstance(entry, dict) and
            _eligible(entry, cutoff)}
    if len(kept) > lim:
        ordered = sorted(kept.items(),
                         key=lambda kv: (kv[1].get("first_seen",
                                                   ""), kv[0]))
        kept = dict(ordered[-lim:])
    return kept


def to_envelope(pool: dict | None) -> dict:
    """Pool -> Tuzzina research-state envelope. Only the
    'candidates' key; the server allowlists and size-caps it
    like every other envelope key."""
    src = pool if isinstance(pool, dict) else {}
    return {"candidates": {k: dict(v) for k, v in src.items()
                           if isinstance(v, dict)}}


def envelope_size(envelope: dict) -> int:
    """JSON byte size of an envelope (client-side guard
    mirroring the server 1MB cap). Never raises."""
    try:
        import json
        return len(json.dumps(envelope).encode("utf-8"))
    except Exception:
        return MAX_ENVELOPE_BYTES + 1
