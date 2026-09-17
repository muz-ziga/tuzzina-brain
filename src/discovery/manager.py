"""Discovery manager (Stage C). Decides WHAT to poll, never
fetches anything itself: collectors (rss/website/youtube,
later capability adapters) stay the only code that touches
the network, and the monitor stays the only code that owns
cursors.

Pure functions (now is an explicit ISO string, like the rest
of the pipeline): deterministic, fully testable, no clock.

Poll policy precedence per source:
  explicit poll_minutes (strategy/campaign entry) wins;
  otherwise the per-type default below applies.

Defaults are polling hygiene, not schedule: RSS/Atom and
feeds change often (5m), plain websites rarely (10m).
Unknown types fall back to 10m rather than failing: an
unrecognized future source polls conservatively instead of
breaking the whole cycle (its collection still fails
closed on unknown-source-type downstream).
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone

DEFAULT_POLL_MINUTES = {
    "rss": 5,
    "youtube": 5,
    "website": 10,
    "web": 5,
    "news": 5,
    "social.facebook": 5,
    "social.instagram": 5,
    "social.x": 5,
}
FALLBACK_POLL_MINUTES = 10


def _parse_iso(value: str):
    try:
        dt = datetime.fromisoformat((value or "").strip().replace(
            "Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def poll_minutes_for(source: dict) -> int:
    """Effective cadence for one source entry. Explicit
    poll_minutes wins when it is int >= 1; otherwise the
    per-type default; unknown types fall back conservatively.
    Never raises: garbage config polls at the fallback rate
    (collection validates strictly downstream)."""
    try:
        explicit = (source or {}).get("poll_minutes")
        if isinstance(explicit, bool):
            raise ValueError("bool-is-not-int")
        if isinstance(explicit, int) and explicit >= 1:
            return explicit
    except Exception:
        pass
    stype = ""
    try:
        stype = str((source or {}).get("type") or "").strip().lower()
    except Exception:
        stype = ""
    return DEFAULT_POLL_MINUTES.get(stype, FALLBACK_POLL_MINUTES)


def _source_key(source: dict) -> str:
    """Identity a last_checked map uses: explicit source_id,
    else the source URL, else channel-scoped id. Mirrors the
    monitor's source_id derivation without importing it."""
    try:
        sid = str((source or {}).get("source_id") or "").strip()
        if sid:
            return sid
        url = str((source or {}).get("url") or "").strip()
        if url:
            return url
        cid = str((source or {}).get("channel_id") or "").strip()
        if cid:
            return "youtube:%s" % cid
    except Exception:
        pass
    return ""


@dataclass
class DueSource:
    """One source due for polling, with its cadence attached
    for evidence. The entry dict itself is never mutated."""
    source: dict = field(default_factory=dict)
    poll_minutes: int = FALLBACK_POLL_MINUTES
    last_checked_at: str = ""


def due_sources(sources: list, last_checked: dict | None,
                now: str) -> list:
    """Sources due for a poll at now. A source is due when it
    has no recorded check, its last check is unparseable, or
    now - last_checked >= its cadence. Disabled sources never
    return. Pure: inputs untouched, no network, no clock."""
    base = _parse_iso(now)
    if base is None:
        raise ValueError("now must be ISO datetime")
    checked = last_checked if isinstance(last_checked, dict) else {}
    out: list = []
    for source in sources or []:
        if not isinstance(source, dict):
            continue
        if source.get("enabled") is False:
            continue
        key = _source_key(source)
        last_raw = checked.get(key, "") if key else ""
        last = _parse_iso(last_raw) if isinstance(
            last_raw, str) else None
        cadence = poll_minutes_for(source)
        if last is None:
            out.append(DueSource(source=dict(source),
                                 poll_minutes=cadence,
                                 last_checked_at=""))
            continue
        elapsed_min = (base - last).total_seconds() / 60.0
        if elapsed_min >= cadence:
            out.append(DueSource(
                source=dict(source), poll_minutes=cadence,
                last_checked_at=last_raw))
    return out
